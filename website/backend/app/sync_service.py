import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from .config import Settings
from .duolingo import DuolingoClient
from .models import (
    CourseSkill,
    SessionLesson,
    SessionWord,
    SkillSession,
    Word,
)


def now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.app_timezone))


def _upsert_skill(
    session: Session,
    settings: Settings,
    raw_skill: dict,
) -> CourseSkill:
    row = session.exec(
        select(CourseSkill).where(
            CourseSkill.duolingo_skill_id == raw_skill["skillId"]
        )
    ).first()
    timestamp = now(settings)
    if row is None:
        row = CourseSkill(
            duolingo_skill_id=raw_skill["skillId"],
            title=raw_skill["title"],
            debug_name=raw_skill["debugName"],
            section_index=raw_skill["sectionIndex"],
            unit_index=raw_skill["unitIndex"],
            path_level_index=raw_skill["pathLevelIndex"],
            path_order=raw_skill["pathOrder"],
            current_level=raw_skill["currentLevel"],
            current_sessions=raw_skill["currentSessions"],
            total_sessions=raw_skill["totalSessions"],
            state=raw_skill["state"],
            created_at=timestamp,
            updated_at=timestamp,
        )
    else:
        row.title = raw_skill["title"]
        row.debug_name = raw_skill["debugName"]
        row.section_index = raw_skill["sectionIndex"]
        row.unit_index = raw_skill["unitIndex"]
        row.path_level_index = raw_skill["pathLevelIndex"]
        row.path_order = raw_skill["pathOrder"]
        row.current_level = raw_skill["currentLevel"]
        row.current_sessions = raw_skill["currentSessions"]
        row.total_sessions = raw_skill["totalSessions"]
        row.state = raw_skill["state"]
        row.updated_at = timestamp
    session.add(row)
    session.flush()
    return row


def _upsert_checkpoints(
    session: Session,
    course_skill: CourseSkill,
    checkpoints: list[dict],
) -> list[SkillSession]:
    existing = {
        (row.level_index, row.session_index): row
        for row in session.exec(
            select(SkillSession).where(
                SkillSession.course_skill_id == course_skill.id
            )
        ).all()
    }
    result = []
    for checkpoint in checkpoints:
        key = (checkpoint["levelIndex"], checkpoint["sessionIndex"])
        row = existing.get(key)
        if row is None:
            row = SkillSession(
                course_skill_id=course_skill.id,
                level_index=checkpoint["levelIndex"],
                session_index=checkpoint["sessionIndex"],
                total_sessions=checkpoint["totalSessions"],
                path_order=checkpoint["pathOrder"],
                is_completed=checkpoint["isCompleted"],
            )
        else:
            row.total_sessions = checkpoint["totalSessions"]
            row.path_order = checkpoint["pathOrder"]
            row.is_completed = checkpoint["isCompleted"]
        session.add(row)
        session.flush()
        result.append(row)
    return result


def cleanup_noncanonical_sessions(session: Session) -> int:
    """Remove empty crown-repeat rows while preserving any user/course data."""
    removed = 0
    rows = session.exec(
        select(SkillSession).where(SkillSession.level_index != 0)
    ).all()
    for row in rows:
        has_words = session.exec(
            select(SessionWord.id).where(SessionWord.skill_session_id == row.id)
        ).first()
        has_lesson = session.exec(
            select(SessionLesson.id).where(SessionLesson.skill_session_id == row.id)
        ).first()
        if has_words is not None or has_lesson is not None:
            continue
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
    return removed


def _prior_word_texts(session: Session, skill_session: SkillSession) -> set[str]:
    statement = (
        select(Word.text)
        .join(SessionWord, col(SessionWord.word_id) == col(Word.id))
        .join(
            SkillSession,
            col(SkillSession.id) == col(SessionWord.skill_session_id),
        )
        .where(
            SkillSession.course_skill_id == skill_session.course_skill_id,
            SkillSession.level_index == 0,
            SkillSession.synced_at.is_not(None),
            SkillSession.path_order < skill_session.path_order,
        )
    )
    return set(session.exec(statement).all())


def _derive_legacy_completion(
    session: Session,
    settings: Settings,
    skill_session: SkillSession,
    words: list[Word],
) -> None:
    study_completed = bool(words) and all(word.study_count > 0 for word in words)
    review_completed = bool(words) and all(word.review_count > 0 for word in words)
    if not study_completed and not review_completed:
        return

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
    if study_completed:
        skill_session.completed_at = skill_session.completed_at or now(settings)
        studied = [word.last_studied_at for word in words if word.last_studied_at]
        lesson.study_completed_at = max(studied) if studied else now(settings)
    if review_completed:
        reviewed = [word.last_reviewed_at for word in words if word.last_reviewed_at]
        lesson.review_completed_at = max(reviewed) if reviewed else now(settings)
    session.add(skill_session)
    session.add(lesson)


def sync_course_progress(
    session: Session,
    settings: Settings,
    client: DuolingoClient | None = None,
) -> dict:
    client = client or DuolingoClient(settings)
    course_data = client.fetch_current_course()
    progress = client.extract_course_progress(course_data)
    cleanup_noncanonical_sessions(session)

    checkpoint_rows: list[tuple[CourseSkill, SkillSession]] = []
    for raw_skill in progress:
        course_skill = _upsert_skill(session, settings, raw_skill)
        for checkpoint in _upsert_checkpoints(
            session, course_skill, raw_skill["checkpoints"]
        ):
            checkpoint_rows.append((course_skill, checkpoint))
    session.commit()

    existing_words = {
        word.text: word for word in session.exec(select(Word)).all()
    }
    fetched_count = 0
    new_count = 0
    synced_session_count = 0
    changed_skill_ids: set[int] = set()

    checkpoint_rows.sort(key=lambda pair: pair[1].path_order)
    for course_skill, checkpoint in checkpoint_rows:
        if not checkpoint.is_completed or checkpoint.synced_at is not None:
            continue

        raw_words = client.fetch_words_for_skill(
            course_skill.duolingo_skill_id,
            checkpoint.level_index,
            checkpoint.session_index,
        )
        fetched_count += len(raw_words)
        previous_texts = _prior_word_texts(session, checkpoint)
        introduced_words: list[Word] = []
        seen_texts: set[str] = set()
        timestamp = now(settings)

        for raw_word in raw_words:
            text = str(raw_word.get("text") or "").strip()
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            translations_json = json.dumps(
                raw_word.get("translations") or [], ensure_ascii=False
            )
            word = existing_words.get(text)
            if word is None:
                word = Word(
                    text=text,
                    translations_json=translations_json,
                    audio_url=raw_word.get("audioURL"),
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                )
                session.add(word)
                session.flush()
                existing_words[text] = word
                new_count += 1
            else:
                word.translations_json = translations_json
                word.audio_url = raw_word.get("audioURL") or word.audio_url
                word.last_seen_at = timestamp
                session.add(word)

            if text not in previous_texts:
                introduced_words.append(word)

        for position, word in enumerate(introduced_words):
            session.add(
                SessionWord(
                    skill_session_id=checkpoint.id,
                    word_id=word.id,
                    position=position,
                )
            )

        checkpoint.word_count = len(introduced_words)
        checkpoint.synced_at = timestamp
        if checkpoint.word_count == 0:
            checkpoint.completed_at = timestamp
        session.add(checkpoint)
        _derive_legacy_completion(
            session, settings, checkpoint, introduced_words
        )
        session.commit()

        synced_session_count += 1
        changed_skill_ids.add(course_skill.id)

    return {
        "fetchedCount": fetched_count,
        "newCount": new_count,
        "syncedSessionCount": synced_session_count,
        "changedSkillCount": len(changed_skill_ids),
        "skillCount": len(progress),
    }
