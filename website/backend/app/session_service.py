import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, func, select

from .config import Settings
from .models import (
    CourseSkill,
    SessionLesson,
    SessionWord,
    SkillSession,
    Word,
)


def now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def serialize_word(word: Word) -> dict:
    return {
        "id": word.id,
        "text": word.text,
        "translations": json.loads(word.translations_json),
        "audioUrl": word.audio_url,
        "studyCount": word.study_count,
        "reviewCount": word.review_count,
    }


def session_words(session: Session, skill_session_id: int) -> list[Word]:
    statement = (
        select(Word)
        .join(SessionWord, col(SessionWord.word_id) == col(Word.id))
        .where(SessionWord.skill_session_id == skill_session_id)
        .order_by(SessionWord.position)
    )
    return list(session.exec(statement).all())


def get_or_create_session_lesson(
    session: Session,
    settings: Settings,
    skill_session: SkillSession,
) -> SessionLesson:
    lesson = session.exec(
        select(SessionLesson).where(
            SessionLesson.skill_session_id == skill_session.id
        )
    ).first()
    if lesson is None:
        lesson = SessionLesson(
            skill_session_id=skill_session.id,
            created_at=now(settings),
        )
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
    return lesson


def _lesson_for_session(
    session: Session,
    skill_session_id: int,
) -> SessionLesson | None:
    return session.exec(
        select(SessionLesson).where(
            SessionLesson.skill_session_id == skill_session_id
        )
    ).first()


def is_learning_completed(row: SkillSession) -> bool:
    return bool(
        row.completed_at is not None
        or (row.synced_at is not None and row.word_count == 0)
    )


def serialize_session_summary(
    session: Session,
    row: SkillSession,
) -> dict:
    lesson = _lesson_for_session(session, row.id)
    has_content = False
    if lesson is not None and lesson.content_json and lesson.outline_json:
        try:
            content_payload = json.loads(lesson.content_json)
            outline_payload = json.loads(lesson.outline_json)
            has_content = isinstance(content_payload, dict) and isinstance(outline_payload, dict)
        except json.JSONDecodeError:
            has_content = False
    return {
        "id": row.id,
        "sessionIndex": row.session_index,
        "totalSessions": row.total_sessions,
        "isCompleted": row.is_completed,
        "isSynced": row.synced_at is not None,
        "wordCount": row.word_count,
        "hasContent": has_content,
        "contentVersion": lesson.content_version if lesson else 0,
        "outlineVersion": lesson.outline_version if lesson else 0,
        "generationStatus": lesson.generation_status if lesson else "pending",
        "generationError": lesson.generation_error if lesson else None,
        "learningCompleted": is_learning_completed(row),
        "completedAt": row.completed_at,
    }


def serialize_session_detail(
    session: Session,
    settings: Settings,
    row: SkillSession,
) -> dict:
    skill = session.get(CourseSkill, row.course_skill_id)
    if skill is None:
        raise ValueError("课程技能不存在。")
    lesson = _lesson_for_session(session, row.id)
    content = None
    if lesson is not None and lesson.content_json and lesson.outline_json:
        try:
            content = json.loads(lesson.content_json)
        except json.JSONDecodeError:
            content = None
    return {
        **serialize_session_summary(session, row),
        "skill": {
            "id": skill.id,
            "duolingoSkillId": skill.duolingo_skill_id,
            "title": skill.title,
            "debugName": skill.debug_name,
        },
        "content": content,
        "contentGeneratedAt": lesson.content_generated_at if lesson else None,
        "words": [serialize_word(word) for word in session_words(session, row.id)],
    }


def course_path_payload(session: Session) -> list[dict]:
    skills = session.exec(select(CourseSkill).order_by(CourseSkill.path_order)).all()
    result = []
    for skill in skills:
        sessions = session.exec(
            select(SkillSession)
            .where(
                SkillSession.course_skill_id == skill.id,
                SkillSession.level_index == 0,
            )
            .order_by(SkillSession.path_order)
        ).all()
        skill_completed = bool(sessions) and all(
            is_learning_completed(row) for row in sessions
        )
        result.append(
            {
                "id": skill.id,
                "duolingoSkillId": skill.duolingo_skill_id,
                "title": skill.title,
                "debugName": skill.debug_name,
                "state": skill.state,
                "currentSessions": skill.current_sessions,
                "totalSessions": skill.total_sessions,
                "learningCompleted": skill_completed,
                "sessions": [
                    serialize_session_summary(session, row) for row in sessions
                ],
            }
        )
    return result


def default_session(session: Session) -> SkillSession | None:
    rows = session.exec(
        select(SkillSession)
        .where(
            SkillSession.level_index == 0,
            SkillSession.is_completed == True,  # noqa: E712
            SkillSession.synced_at.is_not(None),
        )
        .order_by(SkillSession.path_order)
    ).all()
    for row in rows:
        if not is_learning_completed(row):
            return row
    return rows[-1] if rows else None


def progress_counts(session: Session) -> dict:
    rows = session.exec(
        select(SkillSession)
        .where(SkillSession.level_index == 0)
        .order_by(SkillSession.path_order)
    ).all()
    completed_rows = [row for row in rows if is_learning_completed(row)]
    last_completed = max(
        (
            row
            for row in completed_rows
            if row.completed_at is not None and row.word_count > 0
        ),
        key=lambda row: row.completed_at,
        default=None,
    )
    next_row = next(
        (
            row
            for row in rows
            if row.is_completed
            and row.synced_at is not None
            and not is_learning_completed(row)
        ),
        None,
    )
    skill_titles = {
        skill.id: skill.title
        for skill in session.exec(select(CourseSkill)).all()
    }
    return {
        "totalSessions": len(rows),
        "completedSessions": len(completed_rows),
        "lastCompleted": (
            {
                "id": last_completed.id,
                "skill": skill_titles.get(last_completed.course_skill_id, ""),
                "sessionIndex": last_completed.session_index,
            }
            if last_completed is not None
            else None
        ),
        "nextSession": (
            {
                "id": next_row.id,
                "skill": skill_titles.get(next_row.course_skill_id, ""),
                "sessionIndex": next_row.session_index,
            }
            if next_row is not None
            else None
        ),
    }


def complete_session(
    session: Session,
    settings: Settings,
    skill_session: SkillSession,
    phase: str | None = None,
) -> SessionLesson:
    if not skill_session.is_completed or skill_session.synced_at is None:
        raise ValueError("这个 Session 尚未从多邻国同步。")

    timestamp = now(settings)
    words = session_words(session, skill_session.id)
    if is_learning_completed(skill_session):
        lesson = _lesson_for_session(session, skill_session.id)
        return lesson

    lesson = get_or_create_session_lesson(session, settings, skill_session)
    skill_session.completed_at = timestamp
    lesson.study_completed_at = timestamp
    for word in words:
        word.study_count += 1
        word.last_studied_at = timestamp
        session.add(word)
    session.add(skill_session)
    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    return lesson


def complete_session_phase(
    session: Session,
    settings: Settings,
    skill_session: SkillSession,
    phase: str,
) -> SessionLesson | None:
    """Compatibility wrapper for older callers; completion is now one action."""
    if phase not in {"study", "review"}:
        raise ValueError("无效课程阶段。")
    lesson = complete_session(session, settings, skill_session)
    if phase == "review" and lesson is not None and lesson.review_completed_at is None:
        timestamp = now(settings)
        lesson.review_completed_at = timestamp
        for word in session_words(session, skill_session.id):
            word.review_count += 1
            word.last_reviewed_at = timestamp
            session.add(word)
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
    return lesson
