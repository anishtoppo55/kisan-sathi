from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings loaded from environment variables and `.env`.

    Keys are SecretStr so they never show up in logs or reprs.
    They are optional in Task 1 because the echo endpoint doesn't need them.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gnani_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None

    rate_limit_per_minute: int = 20  # /api/* requests per client IP
    kb_score_threshold: float = 0.70  # below this top retrieval score, use the web fallback


@lru_cache
def get_settings() -> Settings:
    return Settings()
