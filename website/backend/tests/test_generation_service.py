import json
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai import (
    AITransientError,
    AIValidationError,
    LESSON_PROMPT_VERSION,
    OUTLINE_PROMPT_VERSION,
)
from app.config import Settings
from app.generation_service import (
    course_context_for_session,
    generate_and_store_session_lesson,
    queue_incomplete_lessons,
    queue_failed_lessons,
    queue_outdated_lessons,
)
from app.models import CourseSkill, SessionLesson, SessionWord, SkillSession, Word


@pytest.fixture
def engine():
    value = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(value)
    return value


@pytest.fixture
def settings():
    return Settings(database_url="sqlite://", _env_file=None)


def add_session(
    session: Session,
    skill: CourseSkill,
    session_index: int,
    text: str,
) -> SkillSession:
    timestamp = datetime.now()
    row = SkillSession(
        course_skill_id=skill.id,
        level_index=0,
        session_index=session_index,
        total_sessions=2,
        path_order=session_index,
        is_completed=True,
        synced_at=timestamp,
        word_count=1,
    )
    session.add(row)
    session.flush()
    word = Word(
        text=text,
        translations_json=json.dumps([f"{text}-zh"]),
        first_seen_at=timestamp,
        last_seen_at=timestamp,
    )
    session.add(word)
    session.flush()
    session.add(SessionWord(skill_session_id=row.id, word_id=word.id, position=0))
    session.commit()
    return row


def setup_course(session: Session) -> tuple[SkillSession, SkillSession]:
    timestamp = datetime.now()
    skill = CourseSkill(
        duolingo_skill_id="skill",
        title="Languages",
        debug_name="Languages, Level 0",
        section_index=0,
        unit_index=0,
        path_level_index=0,
        path_order=0,
        current_level=0,
        current_sessions=2,
        total_sessions=2,
        state="active",
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(skill)
    session.flush()
    first = add_session(session, skill, 1, "bonjour")
    second = add_session(session, skill, 2, "merci")
    return first, second


def lesson_content(title: str) -> dict:
    return {
        "title": title,
        "overview": f"{title} overview",
        "wordNotes": [],
        "grammarPoints": [],
        "examples": [],
        "exercises": [],
        "summary": [f"{title} summary"],
        "pronunciation": {"focus": "focus", "items": []},
        "grammarLesson": {
            "primaryTopic": f"{title} grammar",
            "objective": f"{title} objective",
        },
    }


def outline_content(position: str) -> dict:
    return {
        "title": "法语学习总览",
        "currentPosition": position,
        "courseSummary": "累计内容",
        "learnedThemes": ["问候"],
        "grammarProgress": [],
        "pronunciationProgress": [],
        "recentSessions": [],
        "nextFocus": [],
    }


def test_saved_lesson_is_reused_without_new_ai_calls(engine, settings):
    calls = {"lesson": 0, "outline": 0}

    def generate_lesson(*_args, **_kwargs):
        calls["lesson"] += 1
        return lesson_content("first")

    def generate_outline(*_args, **_kwargs):
        calls["outline"] += 1
        return outline_content("Session 1")

    with Session(engine) as session:
        first, _ = setup_course(session)
        generate_and_store_session_lesson(
            session,
            settings,
            first,
            lesson_generator=generate_lesson,
            outline_generator=generate_outline,
        )
        generate_and_store_session_lesson(
            session,
            settings,
            first,
            lesson_generator=generate_lesson,
            outline_generator=generate_outline,
        )
        saved = session.exec(
            select(SessionLesson).where(SessionLesson.skill_session_id == first.id)
        ).one()

    assert calls == {"lesson": 1, "outline": 1}
    assert json.loads(saved.content_json)["title"] == "first"
    assert saved.content_version == LESSON_PROMPT_VERSION
    assert saved.outline_version == OUTLINE_PROMPT_VERSION
    assert saved.generation_status == "ready"


def test_next_session_receives_previous_outline_and_recent_lesson(engine, settings):
    captured_context = None

    def first_lesson(*_args, **_kwargs):
        return lesson_content("first")

    def first_outline(*_args, **_kwargs):
        return outline_content("Session 1")

    def second_lesson(*_args, **kwargs):
        nonlocal captured_context
        captured_context = kwargs["course_context"]
        return lesson_content("second")

    with Session(engine) as session:
        first, second = setup_course(session)
        generate_and_store_session_lesson(
            session,
            settings,
            first,
            lesson_generator=first_lesson,
            outline_generator=first_outline,
        )
        generate_and_store_session_lesson(
            session,
            settings,
            second,
            lesson_generator=second_lesson,
            outline_generator=lambda *_args, **_kwargs: outline_content("Session 2"),
        )

    assert captured_context["previousOutline"]["currentPosition"] == "Session 1"
    assert captured_context["recentLessons"][0]["title"] == "first"
    assert captured_context["recentLessons"][0]["grammarTopic"] == "first grammar"


def test_outline_failure_clears_content_and_retry_regenerates_both(
    engine, settings
):
    lesson_calls = 0

    def generate_lesson(*_args, **_kwargs):
        nonlocal lesson_calls
        lesson_calls += 1
        return lesson_content("persistent")

    def fail_outline(*_args, **_kwargs):
        raise ValueError("outline failed")

    with Session(engine) as session:
        first, _ = setup_course(session)
        with pytest.raises(ValueError, match="outline failed"):
            generate_and_store_session_lesson(
                session,
                settings,
                first,
                lesson_generator=generate_lesson,
                outline_generator=fail_outline,
            )
        saved = session.exec(select(SessionLesson)).one()
        assert saved.content_json is None
        assert saved.outline_json is None
        assert saved.generation_status == "error"

        generate_and_store_session_lesson(
            session,
            settings,
            first,
            lesson_generator=generate_lesson,
            outline_generator=lambda *_args, **_kwargs: outline_content("Session 1"),
        )
        saved = session.exec(select(SessionLesson)).one()

    assert lesson_calls == 2
    assert saved.generation_status == "ready"
    assert saved.outline_json is not None


def test_upgrade_queue_excludes_incomplete_lessons(engine, settings):
    with Session(engine) as session:
        first, second = setup_course(session)
        session.add(
            SessionLesson(
                skill_session_id=first.id,
                created_at=datetime.now(),
                content_json=json.dumps(lesson_content("current")),
                content_version=LESSON_PROMPT_VERSION,
                outline_json=json.dumps(outline_content("Session 1")),
                outline_version=OUTLINE_PROMPT_VERSION,
                generation_status="ready",
            )
        )
        session.commit()

        queued = queue_outdated_lessons(session, settings)
        first_lesson = session.exec(
            select(SessionLesson).where(SessionLesson.skill_session_id == first.id)
        ).one()
        second_lesson = session.exec(
            select(SessionLesson).where(SessionLesson.skill_session_id == second.id)
        ).one()

    assert queued == []
    assert first_lesson.generation_status == "ready"
    assert second_lesson.generation_status == "pending"


def test_context_is_empty_before_first_lesson(engine):
    with Session(engine) as session:
        first, _ = setup_course(session)
        context = course_context_for_session(session, first)
    assert context == {"previousOutline": None, "recentLessons": []}


def test_normal_sync_queue_fills_missing_content_without_upgrading_old_content(
    engine, settings
):
    with Session(engine) as session:
        first, second = setup_course(session)
        second_id = second.id
        session.add(
            SessionLesson(
                skill_session_id=first.id,
                created_at=datetime.now(),
                content_json=json.dumps(lesson_content("saved old version")),
                content_version=0,
                outline_json=json.dumps(outline_content("Session 1")),
                outline_version=0,
                generation_status="ready",
            )
        )
        session.commit()

        first_queue = queue_incomplete_lessons(session, settings)
        second_queue = queue_incomplete_lessons(session, settings)
        first_lesson = session.exec(
            select(SessionLesson).where(SessionLesson.skill_session_id == first.id)
        ).one()

    assert first_queue == [second_id]
    assert second_queue == []
    assert json.loads(first_lesson.content_json)["title"] == "saved old version"


def test_normal_sync_mode_adds_outline_without_replacing_old_content(
    engine, settings
):
    lesson_calls = 0

    def unexpected_lesson_generation(*_args, **_kwargs):
        nonlocal lesson_calls
        lesson_calls += 1
        return lesson_content("replacement")

    with Session(engine) as session:
        first, _ = setup_course(session)
        session.add(
            SessionLesson(
                skill_session_id=first.id,
                created_at=datetime.now(),
                content_json=json.dumps(lesson_content("saved v1")),
                content_version=1,
                generation_status="queued",
            )
        )
        session.commit()

        generate_and_store_session_lesson(
            session,
            settings,
            first,
            upgrade_outdated_content=False,
            lesson_generator=unexpected_lesson_generation,
            outline_generator=lambda *_args, **_kwargs: outline_content("Session 1"),
        )
        saved = session.exec(select(SessionLesson)).one()

    assert lesson_calls == 0
    assert json.loads(saved.content_json)["title"] == "saved v1"
    assert saved.content_version == 1
    assert saved.outline_version == OUTLINE_PROMPT_VERSION


def test_transient_ai_failures_are_retried(engine, monkeypatch):
    attempts = 0

    def flaky_lesson(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise AITransientError("temporary timeout")
        return lesson_content("recovered")

    monkeypatch.setattr("app.generation_service.time.sleep", lambda _seconds: None)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        first, _ = setup_course(session)
        saved = generate_and_store_session_lesson(
            session,
            settings,
            first,
            lesson_generator=flaky_lesson,
            outline_generator=lambda *_args, **_kwargs: outline_content("Session 1"),
        )

    assert attempts == 3
    assert saved.generation_status == "ready"


def test_validation_failures_receive_one_repair_attempt(engine, monkeypatch):
    attempts = 0

    def invalid_lesson(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise AIValidationError("missing fields")

    monkeypatch.setattr("app.generation_service.time.sleep", lambda _seconds: None)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        first, _ = setup_course(session)
        with pytest.raises(AIValidationError, match="已尝试 2 次"):
            generate_and_store_session_lesson(
                session,
                settings,
                first,
                lesson_generator=invalid_lesson,
            )

    assert attempts == 2


def test_failed_lessons_can_be_queued_without_touching_ready_lessons(engine, settings):
    with Session(engine) as session:
        first, second = setup_course(session)
        session.add_all(
            [
                SessionLesson(
                    skill_session_id=first.id,
                    created_at=datetime.now(),
                    generation_status="error",
                    generation_error="timeout",
                ),
                SessionLesson(
                    skill_session_id=second.id,
                    created_at=datetime.now(),
                    content_json=json.dumps(lesson_content("ready")),
                    content_version=LESSON_PROMPT_VERSION,
                    outline_json=json.dumps(outline_content("Session 2")),
                    outline_version=OUTLINE_PROMPT_VERSION,
                    generation_status="ready",
                ),
            ]
        )
        session.commit()
        first_id = first.id

        queued = queue_failed_lessons(session, settings)
        lessons = session.exec(select(SessionLesson).order_by(SessionLesson.skill_session_id)).all()

    assert queued == [first_id]
    assert [lesson.generation_status for lesson in lessons] == ["queued", "ready"]
