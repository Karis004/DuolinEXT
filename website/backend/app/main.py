import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, col, func, select

from .config import Settings, get_settings
from .database import create_db_and_tables, engine, get_session
from .duolingo import DuolingoError
from .generation_service import (
    active_generation_task,
    create_generation_task,
    generate_and_store_session_lesson,
    generation_progress_payload,
    latest_outline_payload,
    queue_failed_lessons,
    queue_incomplete_lessons,
    queue_outdated_lessons,
    request_generation_cancel,
    run_generation_batch,
)
from .models import CourseSkill, SkillSession, SyncRun, VocabularyTask, Word
from .ai import AIError, ask_tutor
from .vocabulary_service import (
    active_vocabulary_task,
    create_vocabulary_task,
    queue_vocabulary_words,
    run_vocabulary_batch,
    vocabulary_payload,
    vocabulary_pending_count,
)
from .session_service import (
    complete_session,
    course_path_payload,
    default_session,
    progress_counts,
    serialize_session_detail,
)
from .sync_service import sync_course_progress


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    settings = get_settings()
    with Session(engine) as session:
        has_pending_vocabulary = vocabulary_pending_count(session) > 0
    if settings.ai_configured and has_pending_vocabulary:
        asyncio.create_task(_start_vocabulary_backfill(settings))
    yield


async def _start_vocabulary_backfill(settings: Settings) -> None:
    from .database import engine

    with Session(engine) as session:
        if active_vocabulary_task(session) is not None:
            return
        word_ids = queue_vocabulary_words(session, settings)
        if not word_ids:
            return
        task = create_vocabulary_task(session, settings, word_ids)
    await asyncio.to_thread(run_vocabulary_batch, word_ids, settings, task.id)


def _run_sync_generation_pipeline(
    lesson_ids: list[int],
    lesson_task_id: int | None,
    word_ids: list[int],
    vocabulary_task_id: int | None,
    settings: Settings,
) -> None:
    if lesson_ids:
        run_generation_batch(lesson_ids, settings, False, lesson_task_id)
    if word_ids:
        run_vocabulary_batch(word_ids, settings, vocabulary_task_id)


app = FastAPI(title="DuolinEx API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_time(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def sync_in_progress(session: Session) -> bool:
    return session.exec(
        select(SyncRun).where(SyncRun.status == "running")
    ).first() is not None


def ensure_generation_available(session: Session) -> None:
    if sync_in_progress(session):
        raise HTTPException(status_code=409, detail="多邻国同步正在进行，请稍后再生成课程。")
    if active_generation_task(session) is not None:
        raise HTTPException(status_code=409, detail="已有课程生成任务正在进行。")
    if active_vocabulary_task(session) is not None:
        raise HTTPException(status_code=409, detail="词汇表正在整理，请稍后再生成课程。")


def _last_sync_payload(session: Session) -> dict | None:
    last_sync = session.exec(
        select(SyncRun)
        .where(SyncRun.status == "success")
        .order_by(col(SyncRun.id).desc())
    ).first()
    if last_sync is None:
        return None
    return {
        "at": last_sync.completed_at,
        "fetchedCount": last_sync.fetched_count,
        "newCount": last_sync.new_count,
    }


def dashboard_payload(
    session: Session,
    settings: Settings,
    selected_session_id: int | None = None,
) -> dict:
    selected = (
        session.get(SkillSession, selected_session_id)
        if selected_session_id is not None
        else None
    )
    if selected is not None and selected.level_index != 0:
        selected = None
    if selected is None:
        selected = default_session(session)
    return {
        "totalWords": session.exec(select(func.count()).select_from(Word)).one(),
        "skillCount": session.exec(
            select(func.count()).select_from(CourseSkill)
        ).one(),
        "aiConfigured": settings.ai_configured,
        "generation": generation_progress_payload(session),
        "syncInProgress": sync_in_progress(session),
        "courseOutline": latest_outline_payload(session),
        "lastSync": _last_sync_payload(session),
        "progress": progress_counts(session),
        "coursePath": course_path_payload(session),
        "selectedSession": (
            serialize_session_detail(session, settings, selected)
            if selected is not None
            else None
        ),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard(
    selected_session_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    return dashboard_payload(session, settings, selected_session_id)


@app.post("/api/sync/duolingo")
def sync_duolingo(
    background_tasks: BackgroundTasks,
    selected_session_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    active_task = active_generation_task(session)
    if active_task is not None:
        raise HTTPException(status_code=409, detail="课程生成正在进行，请先等待完成或中断生成。")
    if active_vocabulary_task(session) is not None:
        raise HTTPException(status_code=409, detail="词汇表正在整理，请稍后再同步。")
    if sync_in_progress(session):
        raise HTTPException(status_code=409, detail="多邻国同步已经在进行中。")
    sync_run = SyncRun(started_at=current_time(settings))
    session.add(sync_run)
    session.commit()
    session.refresh(sync_run)

    try:
        result = sync_course_progress(session, settings)
        sync_run.status = "success"
        sync_run.completed_at = current_time(settings)
        sync_run.fetched_count = result["fetchedCount"]
        sync_run.new_count = result["newCount"]
        session.add(sync_run)
        session.commit()
        if settings.ai_configured:
            queued_ids = queue_incomplete_lessons(session, settings)
            lesson_task = None
            if queued_ids:
                lesson_task = create_generation_task(
                    session, settings, "missing", queued_ids
                )
            word_ids = []
            if active_vocabulary_task(session) is None:
                word_ids = queue_vocabulary_words(session, settings)
            vocabulary_task = None
            if word_ids:
                vocabulary_task = create_vocabulary_task(session, settings, word_ids)
            if queued_ids or word_ids:
                background_tasks.add_task(
                    _run_sync_generation_pipeline,
                    queued_ids,
                    lesson_task.id if lesson_task else None,
                    word_ids,
                    vocabulary_task.id if vocabulary_task else None,
                    settings,
                )
        return dashboard_payload(session, settings, selected_session_id)
    except DuolingoError as exc:
        sync_run.status = "failed"
        sync_run.completed_at = current_time(settings)
        sync_run.error_code = exc.code
        sync_run.error_message = exc.message
        session.add(sync_run)
        session.commit()
        raise HTTPException(
            status_code=401 if exc.code in {"token_missing", "auth_failed"} else 502,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@app.get("/api/sessions/{skill_session_id}")
def session_detail(
    skill_session_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = session.get(SkillSession, skill_session_id)
    if row is None or row.level_index != 0:
        raise HTTPException(status_code=404, detail="Session 不存在。")
    return serialize_session_detail(session, settings, row)


@app.post("/api/sessions/{skill_session_id}/generate")
def generate_session_lesson(
    skill_session_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    ensure_generation_available(session)
    row = session.get(SkillSession, skill_session_id)
    if row is None or row.level_index != 0:
        raise HTTPException(status_code=404, detail="Session 不存在。")
    try:
        generate_and_store_session_lesson(
            session,
            settings,
            row,
            force_content=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return dashboard_payload(session, settings, skill_session_id)


@app.post("/api/lessons/upgrade")
def upgrade_lessons(
    background_tasks: BackgroundTasks,
    selected_session_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.ai_configured:
        raise HTTPException(status_code=400, detail="AI 尚未配置。")
    ensure_generation_available(session)
    queued_ids = queue_outdated_lessons(session, settings)
    if queued_ids:
        task = create_generation_task(session, settings, "upgrade", queued_ids)
        background_tasks.add_task(
            run_generation_batch, queued_ids, settings, True, task.id
        )
    return dashboard_payload(session, settings, selected_session_id)


@app.post("/api/lessons/generate-missing")
def generate_missing_lessons(
    background_tasks: BackgroundTasks,
    selected_session_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.ai_configured:
        raise HTTPException(status_code=400, detail="AI 尚未配置。")
    ensure_generation_available(session)
    queued_ids = queue_incomplete_lessons(session, settings)
    if queued_ids:
        task = create_generation_task(session, settings, "missing", queued_ids)
        background_tasks.add_task(
            run_generation_batch, queued_ids, settings, False, task.id
        )
    return dashboard_payload(session, settings, selected_session_id)


@app.post("/api/lessons/retry-failed")
def retry_failed_lessons(
    background_tasks: BackgroundTasks,
    selected_session_id: int | None = None,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.ai_configured:
        raise HTTPException(status_code=400, detail="AI 尚未配置。")
    ensure_generation_available(session)
    queued_ids = queue_failed_lessons(session, settings)
    if queued_ids:
        task = create_generation_task(session, settings, "retry", queued_ids)
        background_tasks.add_task(
            run_generation_batch, queued_ids, settings, False, task.id
        )
    return dashboard_payload(session, settings, selected_session_id)


@app.post("/api/lessons/generation/cancel")
def cancel_generation(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = request_generation_cancel(session)
    return dashboard_payload(session, settings)


@app.post("/api/tutor/ask")
def tutor_ask(
    payload: dict,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.ai_configured:
        raise HTTPException(status_code=400, detail="AI 尚未配置。")
    skill_session_id = payload.get("sessionId")
    row = session.get(SkillSession, skill_session_id)
    if row is None or row.level_index != 0:
        raise HTTPException(status_code=404, detail="当前 Session 不存在。")
    skill = session.get(CourseSkill, row.course_skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="当前 Skill 不存在。")
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题。")
    try:
        answer = ask_tutor(
            settings,
            skill_title=skill.title,
            session_index=row.session_index,
            course_outline=(latest_outline_payload(session) or {}).get("content"),
            selected_text=str(payload.get("selectedText") or "").strip(),
            question=question,
            messages=payload.get("messages") if isinstance(payload.get("messages"), list) else [],
        )
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"answer": answer}


@app.get("/api/vocabulary")
def vocabulary(
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = active_vocabulary_task(session)
    if task is None and settings.ai_configured and vocabulary_pending_count(session):
        word_ids = queue_vocabulary_words(session, settings)
        if word_ids:
            task = create_vocabulary_task(session, settings, word_ids)
            background_tasks.add_task(
                run_vocabulary_batch, word_ids, settings, task.id
            )
    latest = session.exec(
        select(VocabularyTask).order_by(col(VocabularyTask.id).desc())
    ).first()
    return {
        "items": vocabulary_payload(session),
        "pending": vocabulary_pending_count(session),
        "task": (
            {
                "id": (task or latest).id,
                "status": (task or latest).status,
                "total": (task or latest).total_count,
                "completed": (task or latest).completed_count,
                "failed": (task or latest).failed_count,
            }
            if task or latest
            else None
        ),
    }


@app.post("/api/sessions/{skill_session_id}/complete")
def complete_session_lesson(
    skill_session_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    row = session.get(SkillSession, skill_session_id)
    if row is None or row.level_index != 0:
        raise HTTPException(status_code=404, detail="Session 不存在。")
    try:
        complete_session(session, settings, row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dashboard_payload(session, settings, skill_session_id)


@app.get("/api/words")
def words(
    limit: int = 100,
    session: Session = Depends(get_session),
) -> list[dict]:
    rows = session.exec(
        select(Word).order_by(col(Word.id).asc()).limit(min(limit, 500))
    ).all()
    return [serialize_word(word) for word in rows]
