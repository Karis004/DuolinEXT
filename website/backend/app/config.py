from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


WEBSITE_DIR = Path(__file__).resolve().parents[2]
DATABASE_DIR = WEBSITE_DIR / "backend" / "data"
DEFAULT_DATABASE_PATH = DATABASE_DIR / "duolinext.db"
LEGACY_DATABASE_PATH = DATABASE_DIR / "duolinex.db"
DUOLINGO_PAGE_SIZE = 50
AI_TIMEOUT_SECONDS = 150.0
AI_MAX_ATTEMPTS = 3
AI_RETRY_BASE_SECONDS = 2.0


class Settings(BaseSettings):
    duolingo_jwt: str = ""
    duolingo_user_id: str = ""
    duolingo_course_id: str = "fr"
    duolingo_from_language: str = "zh"
    app_timezone: str = "Asia/Shanghai"

    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_reasoning_effort: str = "low"

    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"

    model_config = SettingsConfigDict(
        env_file=WEBSITE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_api_key and self.ai_base_url and self.ai_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Discard the cached environment settings and load them again.

    The database engine is initialized once at process startup, so callers
    should use this for runtime service settings (API credentials, model,
    language and timezone). A DATABASE_URL change still requires a restart.
    """
    get_settings.cache_clear()
    return get_settings()
