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
    tts_voice: str = "Yashraj"  # Timbre v3.0 voice (male options: Avinash, Shubham, Prakash, Devansh, Yashraj, Harshad)
    # Comma-separated Gemini model fallback chains (see app/clients/gemini.py for the reasoning)
    answer_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash"
    fast_models: str = "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3.6-flash"
    # Free web search: self-hosted SearXNG (JSON API); DuckDuckGo via ddgs is the fallback
    searxng_url: str = "http://127.0.0.1:8888"
    web_search_timeout_s: float = 6.0
    # Gemini's Google-Search grounding is not available on the free tier for Gemini 3 models;
    # turn on only with billing (otherwise every KB miss wastes a 429 round trip).
    gemini_grounding: bool = False

    def model_list(self, name: str) -> tuple[str, ...]:
        return tuple(m.strip() for m in getattr(self, name).split(",") if m.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
