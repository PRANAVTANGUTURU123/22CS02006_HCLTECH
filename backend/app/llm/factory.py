"""Builds providers from Settings and adds retries plus primary-to-fallback switching."""

from dataclasses import dataclass

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from backend.app.core.config import ProviderName, Settings
from backend.app.llm.base import LLMConfigError, LLMError, LLMProvider, LLMResult
from backend.app.llm.providers import GeminiProvider, GroqProvider, MockProvider

log = structlog.get_logger(__name__)


def _secret(value) -> str:
    return value.get_secret_value().strip() if value is not None else ""


def build_provider(
    name: ProviderName, settings: Settings, client: httpx.AsyncClient
) -> LLMProvider:
    if name == "mock":
        return MockProvider()
    if name == "groq":
        return GroqProvider(
            api_key=_secret(settings.groq_api_key),
            model=settings.groq_model.strip(),
            client=client,
            base_url=settings.groq_base_url,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
            reasoning_effort=settings.groq_reasoning_effort.strip(),
        )
    if name == "gemini":
        return GeminiProvider(
            api_key=_secret(settings.gemini_api_key),
            model=settings.gemini_model.strip(),
            client=client,
            base_url=settings.gemini_base_url,
            max_output_tokens=settings.llm_max_output_tokens,
            temperature=settings.llm_temperature,
        )
    raise LLMConfigError(str(name), f"unknown provider {name!r}")


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10.0))


@dataclass
class CompletionOutcome:
    result: LLMResult
    fallback_used: bool


class LLMService:
    """Calls the primary provider (with retries) and, if it fails, the fallback provider."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self._settings = settings
        self._client = client

    async def _call_with_retry(
        self, provider: LLMProvider, prompt: str, system: str | None
    ) -> LLMResult:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._settings.llm_max_retries + 1),
            wait=wait_exponential_jitter(multiplier=0.5, max=4),
            retry=retry_if_exception(lambda e: isinstance(e, LLMError) and e.retryable),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await provider.complete(prompt, system)
        raise AssertionError("unreachable")  # pragma: no cover

    async def complete(self, prompt: str, system: str | None = None) -> CompletionOutcome:
        primary_name = self._settings.llm_provider
        fallback_name = self._settings.llm_fallback_provider
        try:
            primary = build_provider(primary_name, self._settings, self._client)
            return CompletionOutcome(await self._call_with_retry(primary, prompt, system), False)
        except LLMError as exc:
            if not fallback_name or fallback_name == primary_name:
                raise
            log.warning(
                "llm_primary_failed", provider=primary_name, code=exc.code, error=exc.message
            )
            fallback = build_provider(fallback_name, self._settings, self._client)
            return CompletionOutcome(await self._call_with_retry(fallback, prompt, system), True)
