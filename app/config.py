"""Settings loaded from environment / .env (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    planner_model: str = "gpt-5.4"
    planner_reasoning_effort: str | None = None

    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    ctgov_cache_dir: Path | None = Path(".cache/ctgov")
    ctgov_cache_ttl_seconds: int = 86_400
    ctgov_timeout_seconds: float = 15.0
    ctgov_max_attempts: int = 4
    ctgov_requests_per_minute: float = 40.0
    ctgov_burst: int = 5
    max_trials_per_cohort: int = Field(default=30_000, ge=1)

    run_deadline_seconds: float = 90.0
    data_dir: Path = Path("data")


@lru_cache
def get_settings() -> Settings:
    return Settings()
