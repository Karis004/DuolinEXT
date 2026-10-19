from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine
from starlette.background import BackgroundTasks

from app.config import Settings
from app.main import retry_vocabulary
from app.models import Lexeme, Word, WordLexeme


def test_retry_vocabulary_endpoint_queues_failed_and_pending_words():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    settings = Settings(
        ai_api_key="key",
        ai_base_url="https://api.example.com/v1",
        ai_model="model",
        _env_file=None,
    )

    with Session(engine) as session:
        timestamp = datetime.now()
        failed = Word(text="merci", first_seen_at=timestamp, last_seen_at=timestamp)
        pending = Word(text="bonjour", first_seen_at=timestamp, last_seen_at=timestamp)
        session.add_all([failed, pending])
        session.commit()
        session.refresh(failed)
        session.refresh(pending)
        lexeme = Lexeme(lemma="merci", generation_status="error", generation_version=1)
        session.add(lexeme)
        session.commit()
        session.refresh(lexeme)
        session.add(WordLexeme(word_id=failed.id, lexeme_id=lexeme.id))
        session.commit()

        background_tasks = BackgroundTasks()
        result = retry_vocabulary(background_tasks, session, settings)

        assert result["success"] is True
        assert result["queued"] == 2
        assert result["task"]["status"] == "queued"
        assert len(background_tasks.tasks) == 1
