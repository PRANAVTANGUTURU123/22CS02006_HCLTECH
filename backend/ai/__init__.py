"""Optional AI wording for alerts. Never decides whether to alert."""

from __future__ import annotations

import httpx

from backend.ai.facts import facts_by_notification
from backend.ai.llm import LLM, GeminiLLM, GroqLLM, LLMError, MockLLM, WithFallback
from backend.ai.summarizer import Summarizer, check_summary
from backend.engine import FleetReport
from backend.settings import Settings


def _build(name: str, settings: Settings, client: httpx.Client) -> LLM:
    def secret(value) -> str:
        return value.get_secret_value().strip() if value is not None else ""

    if name == "mock":
        return MockLLM()
    if name == "groq":
        return GroqLLM(
            secret(settings.groq_api_key),
            settings.groq_model.strip(),
            client,
            settings.groq_reasoning_effort.strip(),
        )
    if name == "gemini":
        return GeminiLLM(secret(settings.gemini_api_key), settings.gemini_model.strip(), client)
    raise LLMError(f"unknown LLM provider {name!r}")


def llm_from_settings(settings: Settings, client: httpx.Client | None = None) -> LLM:
    client = client or httpx.Client()
    primary = _build(settings.llm_provider, settings, client)
    fallback = None
    if settings.llm_fallback_provider and settings.llm_fallback_provider != settings.llm_provider:
        try:
            fallback = _build(settings.llm_fallback_provider, settings, client)
        except LLMError:
            fallback = None  # a misconfigured fallback must not break the primary
    return WithFallback(primary, fallback)


def summarizer_for(report: FleetReport, settings: Settings) -> Summarizer:
    return Summarizer(
        llm_from_settings(settings),
        facts_by_notification(report),
        timeout_seconds=settings.ai_summary_timeout_seconds,
    )


__all__ = [
    "LLMError",
    "MockLLM",
    "Summarizer",
    "WithFallback",
    "check_summary",
    "facts_by_notification",
    "llm_from_settings",
    "summarizer_for",
]
