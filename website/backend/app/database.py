from pathlib import Path

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine

from .config import DEFAULT_DATABASE_PATH, LEGACY_DATABASE_PATH, get_settings


def migrate_legacy_database_file(
    database_url: str,
    legacy_path: Path = LEGACY_DATABASE_PATH,
    target_path: Path = DEFAULT_DATABASE_PATH,
) -> bool:
    default_url = f"sqlite:///{target_path.as_posix()}"
    if database_url != default_url or target_path.exists() or not legacy_path.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.replace(target_path)
    return True


settings = get_settings()
migrate_legacy_database_file(settings.database_url)
if settings.database_url.startswith("sqlite:///"):
    database_path = Path(settings.database_url.removeprefix("sqlite:///"))
    database_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)


def apply_schema_migrations(target_engine=engine) -> None:
    if target_engine.dialect.name != "sqlite":
        return

    with target_engine.begin() as connection:
        inspector = inspect(connection)
        table_names = inspector.get_table_names()
        if "skillsession" in table_names:
            columns = {
                column["name"] for column in inspector.get_columns("skillsession")
            }
        else:
            columns = set()
        if columns and "is_completed" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE skillsession "
                "ADD COLUMN is_completed BOOLEAN NOT NULL DEFAULT 0"
            )
        if columns and "completed_at" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE skillsession ADD COLUMN completed_at DATETIME"
            )

        if "sessionlesson" in table_names:
            lesson_columns = {
                column["name"] for column in inspector.get_columns("sessionlesson")
            }
            additions = {
                "content_version": "INTEGER NOT NULL DEFAULT 0",
                "content_generated_at": "DATETIME",
                "outline_json": "VARCHAR",
                "outline_version": "INTEGER NOT NULL DEFAULT 0",
                "outline_generated_at": "DATETIME",
                "generation_status": "VARCHAR NOT NULL DEFAULT 'pending'",
                "generation_error": "VARCHAR",
            }
            for name, definition in additions.items():
                if name not in lesson_columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE sessionlesson ADD COLUMN {name} {definition}"
                    )

            connection.exec_driver_sql(
                "UPDATE sessionlesson "
                "SET content_version = 1, "
                "content_generated_at = COALESCE(content_generated_at, created_at), "
                "generation_status = 'ready' "
                "WHERE content_json IS NOT NULL AND content_json != '' "
                "AND content_version = 0"
            )
            connection.exec_driver_sql(
                "UPDATE sessionlesson "
                "SET generation_status = 'pending' "
                "WHERE generation_status IN ('queued', 'generating', 'outlining')"
            )
            connection.exec_driver_sql(
                "UPDATE sessionlesson SET "
                "content_json = NULL, content_version = 0, content_generated_at = NULL, "
                "outline_json = NULL, outline_version = 0, outline_generated_at = NULL, "
                "generation_status = 'error', "
                "generation_error = COALESCE(generation_error, '课程正文和累计大纲必须成对存在。') "
                "WHERE ("
                "(content_json IS NULL OR content_json = '') "
                "AND outline_json IS NOT NULL AND outline_json != ''"
                ") OR ("
                "(outline_json IS NULL OR outline_json = '') "
                "AND content_json IS NOT NULL AND content_json != ''"
                ")"
            )

        if (
            {"skillsession", "sessionlesson"}.issubset(table_names)
            and "study_completed_at" in lesson_columns
        ):
            connection.exec_driver_sql(
                "UPDATE skillsession SET completed_at = ("
                "SELECT sessionlesson.study_completed_at FROM sessionlesson "
                "WHERE sessionlesson.skill_session_id = skillsession.id"
                ") "
                "WHERE skillsession.completed_at IS NULL "
                "AND EXISTS ("
                "SELECT 1 FROM sessionlesson "
                "WHERE sessionlesson.skill_session_id = skillsession.id "
                "AND sessionlesson.study_completed_at IS NOT NULL"
                ")"
            )
        if {"skillsession", "sessionword", "sessionlesson"}.issubset(table_names):
            connection.exec_driver_sql(
                "DELETE FROM skillsession "
                "WHERE level_index != 0 "
                "AND NOT EXISTS ("
                "SELECT 1 FROM sessionword "
                "WHERE sessionword.skill_session_id = skillsession.id"
                ") "
                "AND NOT EXISTS ("
                "SELECT 1 FROM sessionlesson "
                "WHERE sessionlesson.skill_session_id = skillsession.id"
                ")"
            )
        if {"skillsession", "sessionword"}.issubset(table_names):
            connection.exec_driver_sql(
                "UPDATE skillsession SET completed_at = COALESCE(completed_at, synced_at) "
                "WHERE level_index = 0 AND synced_at IS NOT NULL AND word_count = 0 "
                "AND completed_at IS NULL"
            )
        if "generationtask" in table_names:
            connection.exec_driver_sql(
                "UPDATE generationtask SET status = 'cancelled', "
                "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), "
                "current_session_id = NULL, "
                "error_message = COALESCE(error_message, '应用重启后任务已停止。') "
                "WHERE status IN ('queued', 'running')"
            )
        if "vocabularytask" in table_names:
            connection.exec_driver_sql(
                "UPDATE vocabularytask SET status = 'cancelled', "
                "finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP), "
                "error_message = COALESCE(error_message, '应用重启后任务已停止。') "
                "WHERE status IN ('queued', 'running')"
            )
        if "lexeme" in table_names:
            connection.exec_driver_sql(
                "UPDATE lexeme SET generation_status = 'pending' "
                "WHERE generation_status IN ('queued', 'generating')"
            )


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    apply_schema_migrations(engine)


def get_session():
    with Session(engine) as session:
        yield session
