from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


WEBSITE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    duolingo_jwt: str = ""
    duolingo_user_id: str = "1148050773"
    duolingo_course_id: str = "fr"
    duolingo_from_language: str = "zh"
    duolingo_page_size: int = 50

    daily_word_limit: int = 6
    app_timezone: str = "Asia/Shanghai"

    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_timeout_seconds: float = 150.0
    ai_max_attempts: int = 3
    ai_retry_base_seconds: float = 2.0
    ai_reasoning_effort: str = "low"

    database_url: str = f"sqlite:///{(WEBSITE_DIR / 'backend' / 'data' / 'duolinex.db').as_posix()}"

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
