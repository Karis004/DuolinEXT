import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from .config import Settings
from .models import Lesson, LessonWord, Word


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


def lesson_words(session: Session, lesson_id: int) -> list[Word]:
    statement = (
        select(Word)
        .join(LessonWord, col(LessonWord.word_id) == col(Word.id))
        .where(LessonWord.lesson_id == lesson_id)
        .order_by(LessonWord.position)
    )
    return list(session.exec(statement).all())


def serialize_lesson(session: Session, lesson: Lesson | None) -> dict | None:
    if lesson is None or lesson.id is None:
        return None
    content = json.loads(lesson.content_json) if lesson.content_json else None
    return {
        "id": lesson.id,
        "date": lesson.lesson_date,
        "kind": lesson.kind,
        "studyCompleted": lesson.study_completed_at is not None,
        "reviewCompleted": lesson.review_completed_at is not None,
        "content": content,
        "words": [serialize_word(word) for word in lesson_words(session, lesson.id)],
    }


def _select_words(
    session: Session,
    settings: Settings,
    limit: int | None = None,
    kind: str | None = None,
    excluded_ids: set[int] | None = None,
) -> tuple[str, list[Word]]:
    limit = limit or settings.daily_word_limit
    excluded_ids = excluded_ids or set()
    pending = list(
        session.exec(
            select(Word).where(Word.study_count == 0).order_by(col(Word.id).asc())
        ).all()
    )
    pending = [word for word in pending if word.id not in excluded_ids]
    if pending and kind != "review":
        return "new", pending[:limit]

    review = list(
        session.exec(
            select(Word)
            .where(Word.study_count > 0)
            .order_by(
                col(Word.last_reviewed_at).asc().nulls_first(),
                col(Word.last_studied_at).asc(),
            )
        ).all()
    )
    review = [word for word in review if word.id not in excluded_ids]
    return "review", review[:limit]


def get_or_create_today_lesson(session: Session, settings: Settings) -> Lesson | None:
    today = now(settings).date().isoformat()
    existing = session.exec(select(Lesson).where(Lesson.lesson_date == today)).first()
    if existing:
        return existing

    # Keep an unfinished lesson active so the backlog does not silently skip words.
    unfinished = session.exec(
        select(Lesson)
        .where(Lesson.review_completed_at.is_(None))
        .order_by(col(Lesson.id).desc())
    ).first()
    if unfinished:
        if unfinished.lesson_date != today:
            unfinished.lesson_date = today
            session.add(unfinished)
            session.commit()
            session.refresh(unfinished)
        return unfinished

    kind, words = _select_words(session, settings)
    if not words:
        return None

    lesson = Lesson(lesson_date=today, kind=kind, created_at=now(settings))
    session.add(lesson)
    session.flush()
    for position, word in enumerate(words):
        session.add(LessonWord(lesson_id=lesson.id, word_id=word.id, position=position))
    session.commit()
    session.refresh(lesson)
    return lesson


def extend_lesson(
    session: Session,
    settings: Settings,
    lesson: Lesson,
    count: int,
) -> Lesson:
    if count < 1 or count > 50:
        raise ValueError("一次只能追加 1 到 50 个词。")
    existing_ids = {word.id for word in lesson_words(session, lesson.id)}
    kind, words = _select_words(
        session,
        settings,
        limit=count,
        kind=lesson.kind,
        excluded_ids=existing_ids,
    )
    if kind != lesson.kind or not words:
        raise ValueError("当前没有更多可以追加的词汇。")

    next_position = len(existing_ids)
    for position, word in enumerate(words, start=next_position):
        session.add(LessonWord(lesson_id=lesson.id, word_id=word.id, position=position))

    lesson.content_json = None
    lesson.study_completed_at = None
    lesson.review_completed_at = None
    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    return lesson


def complete_lesson_phase(
    session: Session,
    settings: Settings,
    lesson: Lesson,
    phase: str,
) -> Lesson:
    timestamp = now(settings)
    words = lesson_words(session, lesson.id)

    if phase == "study" and lesson.study_completed_at is None:
        lesson.study_completed_at = timestamp
        for word in words:
            if lesson.kind == "new" and word.study_count == 0:
                word.study_count += 1
                word.last_studied_at = timestamp
            elif lesson.kind == "review":
                word.review_count += 1
                word.last_reviewed_at = timestamp
            session.add(word)
    elif phase == "review":
        if lesson.study_completed_at is None:
            raise ValueError("请先完成今天的学习，再进行复习。")
        if lesson.review_completed_at is None:
            lesson.review_completed_at = timestamp
            for word in words:
                reviewed_today = (
                    word.last_reviewed_at is not None
                    and word.last_reviewed_at.date() == timestamp.date()
                )
                if not reviewed_today:
                    word.review_count += 1
                    word.last_reviewed_at = timestamp
                    session.add(word)

    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    return lesson
