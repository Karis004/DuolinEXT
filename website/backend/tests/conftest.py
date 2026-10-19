import os
import tempfile
from pathlib import Path

# Keep the test suite away from the real learning database. Logging writes on
# every AI call, so an isolated database is required even for unit tests.
_database_path = Path(tempfile.gettempdir()) / f"duolinext-pytest-{os.getpid()}.db"
if _database_path.exists():
    _database_path.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_database_path.as_posix()}"

from app import models  # noqa: E402,F401
from app.database import create_db_and_tables, engine  # noqa: E402

create_db_and_tables()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    try:
        engine.dispose()
        _database_path.unlink()
    except OSError:
        pass
