import json
import logging
import re
import time
from collections.abc import Callable

from sqlmodel import Session, col, select

from .ai import (
    AITransientError,
    AIValidationError,
    LESSON_PROMPT_VERSION,
    OUTLINE_PROMPT_VERSION,
    generate_lesson_content,
    generate_outline_content,
)
from .config import AI_MAX_ATTEMPTS, AI_RETRY_BASE_SECONDS, Settings
from .models import CourseSkill, GenerationTask, SessionLesson, SkillSession
from .session_service import (
    get_or_create_session_lesson,
    now,
    serialize_word,
    session_words,
)


LessonGenerator = Callable[..., dict]
OutlineGenerator = Callable[..., dict]
logger = logging.getLogger(__name__)


def _run_ai_generator(
    generator: Callable[..., dict],
    settings: Settings,
    *args,
    request_kind: str,
    **kwargs,
) -> dict:
    transient_limit = max(1, AI_MAX_ATTEMPTS)
    validation_limit = min(2, transient_limit)
    attempt = 0
    started = time.monotonic()
    while True:
        attempt += 1
        try:
            result = generator(settings, *args, **kwargs)
            logger.info(
                "AI %s completed in %.1fs after %d attempt(s)",
                request_kind,
                time.monotonic() - started,
                attempt,
            )
            return result
        except (AITransientError, AIValidationError) as exc:
            limit = (
                transient_limit
                if isinstance(exc, AITransientError)
                else validation_limit
            )
            if attempt >= limit:
                message = f"{exc}（已尝试 {attempt} 次）"
                raise exc.__class__(message) from exc
            delay = max(0.0, AI_RETRY_BASE_SECONDS) * (2 ** (attempt - 1))
            logger.warning(
                "AI %s attempt %d/%d failed after %.1fs: %s; retrying in %.1fs",
                request_kind,
                attempt,
                limit,
                time.monotonic() - started,
                exc,
                delay,
            )
            if delay:
                time.sleep(delay)


def _json_dict(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _has_complete_lesson_payload(lesson: SessionLesson | None) -> bool:
    return bool(
        lesson is not None
        and _json_dict(lesson.content_json) is not None
        and _json_dict(lesson.outline_json) is not None
    )


def _without_legacy_level_positions(outline: dict | None) -> dict | None:
    if outline is None:
        return None

    def clean(value: object) -> object:
        if not isinstance(value, str):
            return value
        return re.sub(
            r"\s*/\s*Level\s+\d+\s*/\s*",
            " / ",
            value,
            flags=re.IGNORECASE,
        )

    result = dict(outline)
    result["currentPosition"] = clean(result.get("currentPosition"))
    result["grammarLedger"] = [
        {**item, "lastPosition": clean(item.get("lastPosition"))}
        for item in result.get("grammarLedger") or []
        if isinstance(item, dict)
    ]
    result["recentSessions"] = [
        {**item, "position": clean(item.get("position"))}
        for item in result.get("recentSessions") or []
        if isinstance(item, dict)
    ]
    return result


def _previous_lessons(
    session: Session,
    skill_session: SkillSession,
    limit: int = 6,
) -> list[tuple[SessionLesson, SkillSession, CourseSkill]]:
    statement = (
        select(SessionLesson, SkillSession, CourseSkill)
        .join(
            SkillSession,
            col(SkillSession.id) == col(SessionLesson.skill_session_id),
        )
        .join(
            CourseSkill,
            col(CourseSkill.id) == col(SkillSession.course_skill_id),
        )
        .where(
            SkillSession.path_order < skill_session.path_order,
            SessionLesson.content_json.is_not(None),
            SessionLesson.outline_json.is_not(None),
        )
        .order_by(col(SkillSession.path_order).desc())
        .limit(limit)
    )
    complete_rows = []
    for lesson, row, skill in session.exec(statement).all():
        if _json_dict(lesson.content_json) is None:
            continue
        if _json_dict(lesson.outline_json) is None:
            continue
        complete_rows.append((lesson, row, skill))
        if len(complete_rows) >= limit:
            break
    return complete_rows


def course_context_for_session(
    session: Session,
    skill_session: SkillSession,
) -> dict:
    previous_rows = _previous_lessons(session, skill_session)
    previous_outline = next(
        (
            parsed
            for lesson, _, _ in previous_rows
            if (
                parsed := _without_legacy_level_positions(
                    _json_dict(lesson.outline_json)
                )
            ) is not None
        ),
        None,
    )
    recent_lessons = []
    for lesson, row, skill in reversed(previous_rows):
        content = _json_dict(lesson.content_json) or {}
        grammar = content.get("grammarLesson")
        if not isinstance(grammar, dict):
            grammar = {}
        recent_lessons.append(
            {
                "skill": skill.title,
                "session": row.session_index,
                "title": str(content.get("title") or ""),
                "overview": str(content.get("overview") or ""),
                "summary": list(content.get("summary") or [])[:4],
                "grammarTopic": str(grammar.get("primaryTopic") or ""),
                "grammarObjective": str(grammar.get("objective") or ""),
            }
        )
    return {
        "previousOutline": previous_outline,
        "recentLessons": recent_lessons,
    }


def lesson_needs_update(lesson: SessionLesson | None) -> bool:
    return bool(
        not _has_complete_lesson_payload(lesson)
        or lesson.content_version < LESSON_PROMPT_VERSION
        or lesson.outline_version < OUTLINE_PROMPT_VERSION
    )


def _mark_generation_error(
    session: Session,
    lesson: SessionLesson,
    error: Exception,
) -> None:
    lesson.content_json = None
    lesson.content_version = 0
    lesson.content_generated_at = None
    lesson.outline_json = None
    lesson.outline_version = 0
    lesson.outline_generated_at = None
    lesson.generation_status = "error"
    lesson.generation_error = str(error)
    session.add(lesson)
    session.commit()


def generate_and_store_session_lesson(
    session: Session,
    settings: Settings,
    skill_session: SkillSession,
    *,
    force_content: bool = False,
    upgrade_outdated_content: bool = True,
    lesson_generator: LessonGenerator = generate_lesson_content,
    outline_generator: OutlineGenerator = generate_outline_content,
) -> SessionLesson:
    if not skill_session.is_completed or skill_session.synced_at is None:
        raise ValueError("这个 Session 尚未从多邻国同步。")
    words = session_words(session, skill_session.id)
    if not words:
        raise ValueError("这个 Session 没有新增词汇。")
    skill = session.get(CourseSkill, skill_session.course_skill_id)
    if skill is None:
        raise ValueError("课程技能不存在。")

    lesson = get_or_create_session_lesson(session, settings, skill_session)
    content_needed = bool(
        force_content
        or not lesson.content_json
        or (
            upgrade_outdated_content
            and lesson.content_version < LESSON_PROMPT_VERSION
        )
    )
    outline_needed = bool(
        force_content
        or content_needed
        or not lesson.outline_json
        or lesson.outline_version < OUTLINE_PROMPT_VERSION
    )
    if not content_needed and not outline_needed:
        lesson.generation_status = "ready"
        lesson.generation_error = None
        session.add(lesson)
        session.commit()
        return lesson

    lesson.generation_status = "generating"
    lesson.generation_error = None
    session.add(lesson)
    session.commit()

    context = course_context_for_session(session, skill_session)
    content = _json_dict(lesson.content_json)
    if content_needed:
        try:
            content = _run_ai_generator(
                lesson_generator,
                settings,
                [serialize_word(word) for word in words],
                request_kind="lesson",
                skill_title=skill.title,
                level_index=skill_session.level_index,
                session_index=skill_session.session_index,
                course_context=context,
            )
        except ValueError as exc:
            _mark_generation_error(session, lesson, exc)
            raise

    if content is None:
        error = ValueError("已保存的课程内容无法解析。")
        _mark_generation_error(session, lesson, error)
        raise error

    if outline_needed:
        try:
            outline = _run_ai_generator(
                outline_generator,
                settings,
                context["previousOutline"],
                content,
                request_kind="outline",
                skill_title=skill.title,
                level_index=skill_session.level_index,
                session_index=skill_session.session_index,
            )
        except ValueError as exc:
            _mark_generation_error(session, lesson, exc)
            raise

        if content_needed:
            lesson.content_json = json.dumps(content, ensure_ascii=False)
            lesson.content_version = LESSON_PROMPT_VERSION
            lesson.content_generated_at = now(settings)
        lesson.outline_json = json.dumps(outline, ensure_ascii=False)
        lesson.outline_version = OUTLINE_PROMPT_VERSION
        lesson.outline_generated_at = now(settings)

    lesson.generation_status = "ready"
    lesson.generation_error = None
    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    return lesson


def _eligible_sessions(session: Session) -> list[SkillSession]:
    return list(
        session.exec(
            select(SkillSession)
            .where(
                SkillSession.is_completed == True,  # noqa: E712
                SkillSession.level_index == 0,
                SkillSession.synced_at.is_not(None),
                SkillSession.word_count > 0,
            )
            .order_by(SkillSession.path_order)
        ).all()
    )


def queue_outdated_lessons(session: Session, settings: Settings) -> list[int]:
    queued_ids = []
    for row in _eligible_sessions(session):
        lesson = get_or_create_session_lesson(session, settings, row)
        if not _has_complete_lesson_payload(lesson):
            continue
        if (
            lesson.content_version >= LESSON_PROMPT_VERSION
            and lesson.outline_version >= OUTLINE_PROMPT_VERSION
        ):
            continue
        if lesson.generation_status in {"queued", "generating", "outlining"}:
            continue
        lesson.generation_status = "queued"
        lesson.generation_error = None
        session.add(lesson)
        queued_ids.append(row.id)
    session.commit()
    return queued_ids


def queue_incomplete_lessons(session: Session, settings: Settings) -> list[int]:
    queued_ids = []
    for row in _eligible_sessions(session):
        lesson = get_or_create_session_lesson(session, settings, row)
        if _has_complete_lesson_payload(lesson):
            continue
        if lesson.generation_status in {"queued", "generating", "outlining"}:
            continue
        lesson.generation_status = "queued"
        lesson.generation_error = None
        session.add(lesson)
        queued_ids.append(row.id)
    session.commit()
    return queued_ids


def queue_failed_lessons(session: Session, settings: Settings) -> list[int]:
    queued_ids = []
    for row in _eligible_sessions(session):
        lesson = get_or_create_session_lesson(session, settings, row)
        if lesson.generation_status not in {"error", "outline_error"}:
            continue
        lesson.generation_status = "queued"
        lesson.generation_error = None
        session.add(lesson)
        queued_ids.append(row.id)
    session.commit()
    return queued_ids


def active_generation_task(session: Session) -> GenerationTask | None:
    return session.exec(
        select(GenerationTask)
        .where(GenerationTask.status.in_(["queued", "running"]))
        .order_by(GenerationTask.id.desc())
    ).first()


def create_generation_task(
    session: Session,
    settings: Settings,
    kind: str,
    skill_session_ids: list[int],
) -> GenerationTask:
    active = active_generation_task(session)
    if active is not None:
        return active
    task = GenerationTask(
        kind=kind,
        total_count=len(skill_session_ids),
        created_at=now(settings),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def request_generation_cancel(session: Session) -> GenerationTask | None:
    task = active_generation_task(session)
    if task is None:
        return None
    task.cancel_requested = True
    session.add(task)
    session.commit()
    return task


def run_generation_batch(
    skill_session_ids: list[int],
    settings: Settings,
    upgrade_outdated_content: bool = True,
    task_id: int | None = None,
) -> None:
    from .database import engine

    task: GenerationTask | None = None
    with Session(engine) as session:
        for skill_session_id in skill_session_ids:
            task = session.get(GenerationTask, task_id) if task_id else None
            if task is not None:
                session.refresh(task)
                if task.cancel_requested:
                    task.status = "cancelled"
                    task.finished_at = now(settings)
                    task.current_session_id = None
                    session.add(task)
                    session.commit()
                    return
                task.status = "running"
                task.started_at = task.started_at or now(settings)
                task.current_session_id = skill_session_id
                session.add(task)
                session.commit()
            row = session.get(SkillSession, skill_session_id)
            if row is None:
                continue
            try:
                generate_and_store_session_lesson(
                    session,
                    settings,
                    row,
                    upgrade_outdated_content=upgrade_outdated_content,
                )
                if task is not None:
                    task.completed_count += 1
            except ValueError as exc:
                if task is not None:
                    task.failed_count += 1
                    task.error_message = str(exc)
            finally:
                if task is not None:
                    task.current_session_id = None
                    session.add(task)
                    session.commit()
        if task is not None:
            task.status = "partial" if task.failed_count else "completed"
            task.finished_at = now(settings)
            session.add(task)
            session.commit()


def generation_progress_payload(session: Session) -> dict:
    eligible = _eligible_sessions(session)
    lessons = {
        lesson.skill_session_id: lesson
        for lesson in session.exec(select(SessionLesson)).all()
    }
    incomplete = sum(
        not _has_complete_lesson_payload(lessons.get(row.id))
        for row in eligible
    )
    outdated = sum(
        _has_complete_lesson_payload(lessons.get(row.id))
        and lesson_needs_update(lessons.get(row.id))
        for row in eligible
    )
    pending = sum(
        lessons.get(row.id) is not None
        and lessons[row.id].generation_status in {"queued", "generating", "outlining"}
        for row in eligible
    )
    failed = sum(
        lessons.get(row.id) is not None
        and lessons[row.id].generation_status in {"error", "outline_error"}
        for row in eligible
    )
    upgradeable = max(0, outdated - failed)
    task = active_generation_task(session)
    latest_task = session.exec(
        select(GenerationTask).order_by(GenerationTask.id.desc())
    ).first()
    return {
        "lessonVersion": LESSON_PROMPT_VERSION,
        "outlineVersion": OUTLINE_PROMPT_VERSION,
        "totalLessons": len(eligible),
        "readyLessons": len(eligible) - incomplete - outdated,
        "outdatedLessons": incomplete + outdated,
        "upgradeableLessons": outdated,
        "pendingLessons": pending,
        "failedLessons": failed,
        "incompleteLessons": incomplete,
        "task": (
            {
                "id": (task or latest_task).id,
                "kind": (task or latest_task).kind,
                "status": (task or latest_task).status,
                "total": (task or latest_task).total_count,
                "completed": (task or latest_task).completed_count,
                "failed": (task or latest_task).failed_count,
                "currentSessionId": (task or latest_task).current_session_id,
                "cancelRequested": (task or latest_task).cancel_requested,
            }
            if task or latest_task
            else None
        ),
    }


def latest_outline_payload(session: Session) -> dict | None:
    statement = (
        select(SessionLesson, SkillSession, CourseSkill)
        .join(
            SkillSession,
            col(SkillSession.id) == col(SessionLesson.skill_session_id),
        )
        .join(
            CourseSkill,
            col(CourseSkill.id) == col(SkillSession.course_skill_id),
        )
        .where(
            SessionLesson.content_json.is_not(None),
            SessionLesson.outline_json.is_not(None),
        )
        .order_by(col(SkillSession.path_order).desc())
    )
    for lesson, row, skill in session.exec(statement).all():
        if not _has_complete_lesson_payload(lesson):
            continue
        outline = _without_legacy_level_positions(_json_dict(lesson.outline_json))
        if outline is None:
            continue
        return {
            "content": outline,
            "version": lesson.outline_version,
            "generatedAt": lesson.outline_generated_at,
            "through": {
                "skill": skill.title,
                "sessionIndex": row.session_index,
            },
        }
    return None
