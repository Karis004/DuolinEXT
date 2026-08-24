import json
from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine, select

from app.config import Settings
from app.lesson_service import complete_lesson_phase, get_or_create_today_lesson, lesson_words
from app.models import Word


def test_backlog_is_assigned_oldest_first_and_completed_in_two_phases():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    settings = Settings(
        database_url="sqlite://",
        daily_word_limit=2,
        _env_file=None,
    )
    timestamp = datetime.now()

    with Session(engine) as session:
        session.add_all(
            [
                Word(
                    text=text,
                    translations_json=json.dumps([translation]),
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                )
                for text, translation in [("bonjour", "你好"), ("merci", "谢谢"), ("avec", "和")]
            ]
        )
        session.commit()

        lesson = get_or_create_today_lesson(session, settings)
        assert lesson is not None
        assert lesson.kind == "new"
        assert [word.text for word in lesson_words(session, lesson.id)] == ["bonjour", "merci"]

        complete_lesson_phase(session, settings, lesson, "study")
        studied = session.exec(select(Word).order_by(Word.id)).all()
        assert [word.study_count for word in studied] == [1, 1, 0]

        complete_lesson_phase(session, settings, lesson, "review")
        reviewed = session.exec(select(Word).order_by(Word.id)).all()
        assert [word.review_count for word in reviewed] == [1, 1, 0]

