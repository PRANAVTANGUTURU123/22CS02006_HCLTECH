"""Application settings, read from environment variables and the project-root .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]

ProviderName = Literal["mock", "groq", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "hackathon-starter"
    environment: str = "local"
    log_level: str = "INFO"
    log_json: bool = False
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    cors_origins: list[str] = Field(default_factory=list)

    # Storage / static files
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    # LLM: "mock" works with no API key. Model names come only from env/.env.
    llm_provider: ProviderName = "mock"
    llm_fallback_provider: ProviderName | None = None
    llm_timeout_seconds: float = Field(default=20.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1024, gt=0)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)

    groq_api_key: SecretStr | None = None
    groq_model: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Only for reasoning models such as openai/gpt-oss-*; leave blank for others.
    groq_reasoning_effort: str = ""

    gemini_api_key: SecretStr | None = None
    gemini_model: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    @field_validator("llm_fallback_provider", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return None if isinstance(value, str) and value.strip().lower() in ("", "none") else value

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _lowercase(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
