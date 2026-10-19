from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

from app.main import words
from app.models import Word


def test_words_endpoint_serializes_word_rows():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    timestamp = datetime.now()

    with Session(engine) as session:
        session.add(
            Word(
                text="bonjour",
                translations_json='["你好"]',
                first_seen_at=timestamp,
                last_seen_at=timestamp,
            )
        )
        session.commit()

        result = words(limit=100, session=session)

    assert result[0]["text"] == "bonjour"
    assert result[0]["translations"] == ["你好"]
