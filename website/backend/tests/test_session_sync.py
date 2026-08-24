import json
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.duolingo import DuolingoClient, DuolingoError
from app.models import CourseSkill, SessionLesson, SessionWord, SkillSession, Word
from app.session_service import (
    complete_session,
    complete_session_phase,
    course_path_payload,
    default_session,
    progress_counts,
    serialize_session_detail,
)
from app.sync_service import cleanup_noncanonical_sessions, sync_course_progress


SKILL_ID = "languages-skill"


def checkpoint(session_index: int, *, completed: bool) -> dict:
    return {
        "levelIndex": 0,
        "sessionIndex": session_index,
        "totalSessions": 3,
        "isCompleted": completed,
        "sectionIndex": 0,
        "unitIndex": 0,
        "pathLevelIndex": 0,
        "pathOrder": session_index,
    }


def progress(completed_sessions: int) -> list[dict]:
    return [
        {
            "skillId": SKILL_ID,
            "title": "Languages",
            "debugName": "Languages, Level 0",
            "sectionIndex": 0,
            "unitIndex": 0,
            "pathLevelIndex": 0,
            "pathOrder": 0,
            "currentLevel": 0,
            "currentSessions": completed_sessions,
            "totalSessions": 3,
            "state": "active",
            "checkpoints": [
                checkpoint(index, completed=index <= completed_sessions)
                for index in range(1, 4)
            ],
        }
    ]


def raw_word(text: str) -> dict:
    return {
        "text": text,
        "translations": [f"{text}-zh"],
        "audioURL": f"https://audio.test/{text}",
    }


class FakeDuolingoClient:
    def __init__(
        self,
        completed_sessions: int,
        responses: dict[tuple[int, int], list[dict]],
        fail_at: tuple[int, int] | None = None,
    ):
        self.completed_sessions = completed_sessions
        self.responses = responses
        self.fail_at = fail_at
        self.calls: list[tuple[str, int, int]] = []

    def fetch_current_course(self) -> dict:
        return {"fake": True}

    def extract_course_progress(self, _course_data: dict) -> list[dict]:
        return progress(self.completed_sessions)

    def fetch_words_for_skill(
        self,
        skill_id: str,
        level_index: int,
        session_index: int,
    ) -> list[dict]:
        self.calls.append((skill_id, level_index, session_index))
        if self.fail_at == (level_index, session_index):
            raise DuolingoError("request_failed", "temporary failure")
        return self.responses[(level_index, session_index)]


@pytest.fixture
def engine():
    value = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(value)
    return value


@pytest.fixture
def settings():
    return Settings(database_url="sqlite://", _env_file=None)


def session_word_texts(session: Session, skill_session: SkillSession) -> list[str]:
    return list(
        session.exec(
            select(Word.text)
            .join(SessionWord, SessionWord.word_id == Word.id)
            .where(SessionWord.skill_session_id == skill_session.id)
            .order_by(SessionWord.position)
        ).all()
    )


def test_course_progress_uses_only_vocabulary_crown_sessions():
    def path_level(crown: int, finished: int, state: str) -> dict:
        return {
            "type": "skill",
            "subtype": "regular",
            "state": state,
            "debugName": f"Cafe, Level {crown}",
            "finishedSessions": finished,
            "totalSessions": 4,
            "pathLevelMetadata": {
                "skillId": "cafe-skill",
                "crownLevelIndex": crown,
            },
        }

    course_data = {
        "currentCourse": {
            "pathSectioned": [
                {
                    "units": [
                        {
                            "levels": [
                                path_level(0, 4, "passed"),
                                path_level(1, 2, "active"),
                            ]
                        }
                    ]
                }
            ]
        }
    }

    skill = DuolingoClient.extract_course_progress(course_data)[0]

    assert skill["title"] == "Cafe"
    assert skill["currentLevel"] == 0
    assert skill["currentSessions"] == 4
    assert len(skill["checkpoints"]) == 4
    assert {
        checkpoint["levelIndex"] for checkpoint in skill["checkpoints"]
    } == {0}


def test_cleanup_removes_only_empty_noncanonical_sessions(engine):
    timestamp = datetime.now()
    with Session(engine) as session:
        skill = CourseSkill(
            duolingo_skill_id="cleanup-skill",
            title="Cleanup",
            debug_name="Cleanup",
            section_index=0,
            unit_index=0,
            path_level_index=0,
            path_order=0,
            current_level=0,
            current_sessions=1,
            total_sessions=1,
            state="passed",
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(skill)
        session.flush()
        empty = SkillSession(
            course_skill_id=skill.id,
            level_index=1,
            session_index=1,
            total_sessions=1,
            path_order=101,
        )
        with_word = SkillSession(
            course_skill_id=skill.id,
            level_index=1,
            session_index=2,
            total_sessions=2,
            path_order=102,
        )
        with_lesson = SkillSession(
            course_skill_id=skill.id,
            level_index=1,
            session_index=3,
            total_sessions=3,
            path_order=103,
        )
        session.add_all([empty, with_word, with_lesson])
        session.flush()
        word = Word(
            text="preserved",
            translations_json="[]",
            first_seen_at=timestamp,
            last_seen_at=timestamp,
        )
        session.add(word)
        session.flush()
        session.add(SessionWord(skill_session_id=with_word.id, word_id=word.id, position=0))
        session.add(SessionLesson(skill_session_id=with_lesson.id, created_at=timestamp))
        session.commit()
        preserved_ids = [with_word.id, with_lesson.id]

        assert cleanup_noncanonical_sessions(session) == 1
        remaining = session.exec(
            select(SkillSession).order_by(SkillSession.session_index)
        ).all()

    assert [row.id for row in remaining] == preserved_ids


def test_initial_backfill_attributes_only_each_sessions_new_words(
    engine, settings
):
    client = FakeDuolingoClient(
        2,
        {
            (0, 1): [raw_word("ma"), raw_word("mere")],
            (0, 2): [
                raw_word("vient de"),
                raw_word("ma"),
                raw_word("pere"),
                raw_word("mere"),
            ],
        },
    )

    with Session(engine) as session:
        result = sync_course_progress(session, settings, client)
        rows = session.exec(select(SkillSession).order_by(SkillSession.path_order)).all()

        assert result["syncedSessionCount"] == 2
        assert result["newCount"] == 4
        assert len(rows) == 3
        assert session_word_texts(session, rows[0]) == ["ma", "mere"]
        assert session_word_texts(session, rows[1]) == ["vient de", "pere"]
        assert rows[2].is_completed is False
        assert rows[2].synced_at is None


def test_unchanged_progress_makes_no_learned_word_requests(engine, settings):
    responses = {(0, 1): [raw_word("ma")], (0, 2): [raw_word("ma"), raw_word("mon")]}
    with Session(engine) as session:
        sync_course_progress(session, settings, FakeDuolingoClient(2, responses))

        unchanged_client = FakeDuolingoClient(2, responses)
        result = sync_course_progress(session, settings, unchanged_client)

        assert unchanged_client.calls == []
        assert result["fetchedCount"] == 0
        assert result["syncedSessionCount"] == 0


def test_newly_completed_session_causes_exactly_one_request(engine, settings):
    responses = {(0, 1): [raw_word("ma")], (0, 2): [raw_word("ma"), raw_word("mon")]}
    with Session(engine) as session:
        sync_course_progress(session, settings, FakeDuolingoClient(1, responses))

        incremental_client = FakeDuolingoClient(2, responses)
        sync_course_progress(session, settings, incremental_client)

        assert incremental_client.calls == [(SKILL_ID, 0, 2)]
        second = session.exec(
            select(SkillSession).where(SkillSession.session_index == 2)
        ).one()
        assert session_word_texts(session, second) == ["mon"]


def test_interrupted_backfill_resumes_at_first_unsynced_checkpoint(
    engine, settings
):
    responses = {(0, 1): [raw_word("ma")], (0, 2): [raw_word("ma"), raw_word("mon")]}
    with Session(engine) as session:
        with pytest.raises(DuolingoError):
            sync_course_progress(
                session,
                settings,
                FakeDuolingoClient(2, responses, fail_at=(0, 2)),
            )

    with Session(engine) as session:
        retry_client = FakeDuolingoClient(2, responses)
        sync_course_progress(session, settings, retry_client)

        assert retry_client.calls == [(SKILL_ID, 0, 2)]


def test_existing_learning_counts_are_preserved_and_derived(engine, settings):
    timestamp = datetime.now()
    with Session(engine) as session:
        session.add(
            Word(
                text="ma",
                translations_json=json.dumps(["我的"]),
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                study_count=3,
                review_count=2,
                last_studied_at=timestamp,
                last_reviewed_at=timestamp,
            )
        )
        session.commit()
        sync_course_progress(
            session,
            settings,
            FakeDuolingoClient(1, {(0, 1): [raw_word("ma")]}),
        )

        word = session.exec(select(Word).where(Word.text == "ma")).one()
        lesson = session.exec(select(SessionLesson)).one()
        assert (word.study_count, word.review_count) == (3, 2)
        assert lesson.study_completed_at == timestamp
        assert lesson.review_completed_at == timestamp


def test_session_detail_is_read_only_and_completion_updates_words(
    engine, settings
):
    with Session(engine) as session:
        sync_course_progress(
            session,
            settings,
            FakeDuolingoClient(1, {(0, 1): [raw_word("ma")]}),
        )
        row = session.exec(select(SkillSession).where(SkillSession.session_index == 1)).one()

        detail = serialize_session_detail(session, settings, row)
        assert detail["content"] is None
        assert session.exec(select(SessionLesson)).first() is None

        complete_session_phase(session, settings, row, "study")
        complete_session_phase(session, settings, row, "review")

        word = session.exec(select(Word).where(Word.text == "ma")).one()
        lesson = session.exec(select(SessionLesson)).one()
        assert (word.study_count, word.review_count) == (1, 1)
        assert lesson.study_completed_at is not None
        assert lesson.review_completed_at is not None


def test_single_completion_action_marks_course_and_advances_default(engine, settings):
    responses = {(0, 1): [raw_word("ma")], (0, 2): [raw_word("ma"), raw_word("mon")]}
    with Session(engine) as session:
        sync_course_progress(session, settings, FakeDuolingoClient(2, responses))
        rows = session.exec(select(SkillSession).order_by(SkillSession.path_order)).all()

        complete_session(session, settings, rows[0])
        first_detail = serialize_session_detail(session, settings, rows[0])
        path = course_path_payload(session)
        progress = progress_counts(session)

        assert first_detail["learningCompleted"] is True
        assert default_session(session).id == rows[1].id
        assert path[0]["learningCompleted"] is False
        assert progress["completedSessions"] == 1
        assert progress["nextSession"]["id"] == rows[1].id
        assert progress["lastCompleted"]["id"] == rows[0].id


def test_empty_synced_session_is_automatically_completed(engine, settings):
    with Session(engine) as session:
        sync_course_progress(session, settings, FakeDuolingoClient(1, {(0, 1): []}))
        row = session.exec(select(SkillSession).where(SkillSession.session_index == 1)).one()

        assert row.completed_at is not None
        assert serialize_session_detail(session, settings, row)["learningCompleted"] is True
