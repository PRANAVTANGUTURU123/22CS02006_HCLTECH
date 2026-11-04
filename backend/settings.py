"""Runtime settings from environment variables / .env. Secrets stay SecretStr."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "cold-chain-alerting"
    environment: str = "local"
    log_level: str = "INFO"

    # Sent-notification ids. SQLite file by default; PostgreSQL in the cloud.
    # SecretStr: a PostgreSQL URL contains the password, so it must never show in logs/repr.
    database_url: SecretStr = SecretStr(
        f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"
    )
    # Cargo profile used by any truck without its own assignment (e.g. a new truck).
    default_profile: str = "pharma"
    # Readings loaded at startup.
    readings_path: Path = PROJECT_ROOT / "starter_kit" / "data" / "sensor_readings.json"
    max_upload_readings: int = Field(default=5000, gt=0, le=50000)
    max_upload_bytes: int = Field(default=2_000_000, gt=0)
    cors_origins: list[str] = Field(default_factory=list)
    # If set, POST endpoints (upload, reset) need header X-API-Key with this value.
    # Reading the dashboard stays open. Always set it on a public deployment.
    admin_api_key: SecretStr | None = None
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    # Discord / Slack incoming webhook. Empty = print notifications to the console instead.
    alert_webhook_url: SecretStr | None = None
    webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    webhook_max_attempts: int = Field(default=3, ge=1, le=5)

    # AI alert summary. "mock" needs no key; tests always use it.
    llm_provider: Literal["mock", "groq", "gemini"] = "mock"
    llm_fallback_provider: Literal["mock", "groq", "gemini"] | None = None
    ai_summary_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    groq_api_key: SecretStr | None = None
    groq_model: str = ""
    groq_reasoning_effort: str = ""
    gemini_api_key: SecretStr | None = None
    gemini_model: str = ""

    @field_validator("llm_fallback_provider", mode="before")
    @classmethod
    def _blank_fallback_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("alert_webhook_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("alert_webhook_url")
    @classmethod
    def _https_only(cls, value: SecretStr | None) -> SecretStr | None:
        # The error message must not echo the URL: it is a secret.
        if value is not None and not value.get_secret_value().startswith("https://"):
            raise ValueError("ALERT_WEBHOOK_URL must start with https://")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
