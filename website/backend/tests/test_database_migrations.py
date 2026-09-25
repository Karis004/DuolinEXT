from sqlalchemy import inspect
from sqlmodel import create_engine

from app.database import apply_schema_migrations, migrate_legacy_database_file


def test_moves_legacy_default_database_without_overwriting(tmp_path):
    legacy_path = tmp_path / "duolinex.db"
    target_path = tmp_path / "duolinext.db"
    legacy_path.write_bytes(b"existing database")

    moved = migrate_legacy_database_file(
        f"sqlite:///{target_path.as_posix()}",
        legacy_path,
        target_path,
    )

    assert moved is True
    assert not legacy_path.exists()
    assert target_path.read_bytes() == b"existing database"
    legacy_path.write_bytes(b"legacy database")
    assert migrate_legacy_database_file(
        f"sqlite:///{target_path.as_posix()}",
        legacy_path,
        target_path,
    ) is False
    assert legacy_path.read_bytes() == b"legacy database"
    assert target_path.read_bytes() == b"existing database"


def test_adds_missing_skill_session_completion_column_idempotently():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE skillsession ("
            "id INTEGER PRIMARY KEY, "
            "course_skill_id INTEGER NOT NULL"
            ")"
        )
        connection.exec_driver_sql(
            "INSERT INTO skillsession (id, course_skill_id) VALUES (1, 10)"
        )

    apply_schema_migrations(engine)
    apply_schema_migrations(engine)

    with engine.connect() as connection:
        columns = {
            column["name"]
            for column in inspect(connection).get_columns("skillsession")
        }
        completed = connection.exec_driver_sql(
            "SELECT is_completed FROM skillsession WHERE id = 1"
        ).scalar_one()

    assert "is_completed" in columns
    assert completed == 0


def test_adds_lesson_generation_metadata_and_preserves_content():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE sessionlesson ("
            "id INTEGER PRIMARY KEY, "
                "skill_session_id INTEGER NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "content_json VARCHAR, "
                "outline_json VARCHAR"
            ")"
        )
        connection.exec_driver_sql(
                "INSERT INTO sessionlesson "
                "(id, skill_session_id, created_at, content_json, outline_json) "
                "VALUES (1, 3, '2026-08-21 12:00:00', '{\"title\":\"saved\"}', '{\"currentPosition\":\"Session 1\"}')"
        )

    apply_schema_migrations(engine)
    apply_schema_migrations(engine)

    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT content_json, outline_json, content_version, generation_status "
            "FROM sessionlesson WHERE id = 1"
        ).one()

    assert row[0] == '{"title":"saved"}'
    assert row[1] == '{"currentPosition":"Session 1"}'
    assert row[2] == 1
    assert row[3] == "ready"
