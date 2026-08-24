import json
from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.ai import VOCABULARY_PROMPT_VERSION
from app.models import Lexeme, LexemeForm, VocabularyTask, Word, WordLexeme
from app.vocabulary_service import (
    VOCABULARY_TASK_STALE_AFTER,
    _store_profile,
    active_vocabulary_task,
    queue_vocabulary_words,
    vocabulary_payload,
)


def test_vocabulary_profiles_are_linked_to_surface_words():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        word = Word(
            text="parle",
            translations_json=json.dumps(["说"]),
            first_seen_at=datetime.now(),
            last_seen_at=datetime.now(),
        )
        session.add(word)
        session.commit()
        session.refresh(word)

        _store_profile(
            session,
            settings,
            word,
            {
                "lemma": "parler",
                "partOfSpeech": "动词",
                "meaning": "说",
                "forms": [
                    {"form": "parler", "label": "不定式"},
                    {"form": "parle", "label": "现在时第一/第三人称单数"},
                ],
            },
        )
        session.commit()
        result = vocabulary_payload(session)

    assert result[0]["lemma"] == "parler"
    assert [item["form"] for item in result[0]["forms"]] == ["parler", "parle"]
    assert session.exec(select(Lexeme)).all()
    assert session.exec(select(LexemeForm)).all()
    assert session.exec(select(WordLexeme)).all()


def test_replacing_a_vocabulary_profile_replaces_forms_without_unique_conflict():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        now = datetime.now()
        word = Word(text="habite", first_seen_at=now, last_seen_at=now)
        session.add(word)
        session.commit()
        session.refresh(word)

        profile = {
            "lemma": "habiter",
            "partOfSpeech": "动词",
            "forms": [{"form": "habiter", "label": "不定式"}],
        }
        _store_profile(session, settings, word, profile)
        session.commit()
        _store_profile(session, settings, word, profile)
        session.commit()

        forms = session.exec(select(LexemeForm)).all()

    assert len(forms) == 1


def test_queue_skips_ready_words_and_queues_missing_words():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        now = datetime.now()
        ready = Word(text="bonjour", first_seen_at=now, last_seen_at=now)
        missing = Word(text="merci", first_seen_at=now, last_seen_at=now)
        session.add_all([ready, missing])
        session.commit()
        session.refresh(ready)
        session.add(Lexeme(lemma="bonjour", generation_status="ready", generation_version=1))
        session.commit()
        lexeme = session.exec(select(Lexeme)).one()
        session.add(WordLexeme(word_id=ready.id, lexeme_id=lexeme.id))
        session.commit()
        missing_id = missing.id

        queued = queue_vocabulary_words(session, settings)

    assert queued == [missing_id]


def test_queue_retries_only_errors_from_an_older_prompt_version():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        now = datetime.now()
        old_error = Word(text="parle", first_seen_at=now, last_seen_at=now)
        current_error = Word(text="merci", first_seen_at=now, last_seen_at=now)
        session.add_all([old_error, current_error])
        session.commit()
        session.refresh(old_error)
        session.refresh(current_error)
        old_error_id = old_error.id
        current_error_id = current_error.id
        old_lexeme = Lexeme(
            lemma="parle",
            generation_status="error",
            generation_version=VOCABULARY_PROMPT_VERSION - 1,
        )
        current_lexeme = Lexeme(
            lemma="merci",
            generation_status="error",
            generation_version=VOCABULARY_PROMPT_VERSION,
        )
        session.add_all([old_lexeme, current_lexeme])
        session.commit()
        session.refresh(old_lexeme)
        session.refresh(current_lexeme)
        session.add_all(
            [
                WordLexeme(word_id=old_error.id, lexeme_id=old_lexeme.id),
                WordLexeme(word_id=current_error.id, lexeme_id=current_lexeme.id),
            ]
        )
        session.commit()

        queued = queue_vocabulary_words(session, settings)

    assert queued == [old_error_id]


def test_stale_vocabulary_task_is_cancelled_and_unlocked():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(database_url="sqlite://", _env_file=None)
    with Session(engine) as session:
        task = VocabularyTask(
            status="running",
            total_count=4,
            started_at=datetime.now() - VOCABULARY_TASK_STALE_AFTER - timedelta(seconds=1),
            created_at=datetime.now(),
        )
        session.add(task)
        session.commit()

        assert active_vocabulary_task(session) is None
        session.refresh(task)

    assert task.status == "cancelled"
    assert task.cancel_requested is True
