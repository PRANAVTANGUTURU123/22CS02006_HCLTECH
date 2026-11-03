"""Provider-independent LLM types. Every provider returns an LLMResult or raises an LLMError."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: float
    usage: dict[str, Any] = field(default_factory=dict)


class LLMError(Exception):
    code = "llm_error"
    retryable = False

    def __init__(self, provider: str, message: str, retry_after_seconds: float | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class LLMConfigError(LLMError):
    """Missing API key or model name. Fix .env; retrying won't help."""

    code = "llm_not_configured"


class LLMRateLimitError(LLMError):
    """HTTP 429 / quota exhausted. Not retried (that burns quota); triggers the fallback."""

    code = "llm_rate_limited"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    retryable = True


class LLMProviderError(LLMError):
    """Any other upstream failure. Retryable when it's a 5xx or a connection error."""

    code = "llm_provider_error"

    def __init__(self, provider: str, message: str, retryable: bool = False):
        super().__init__(provider, message)
        self.retryable = retryable


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, prompt: str, system: str | None = None) -> LLMResult: ...

    async def list_models(self) -> list[str]: ...
