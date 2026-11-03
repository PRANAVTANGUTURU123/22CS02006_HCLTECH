"""Mock, Groq and Gemini providers. Groq and Gemini are called over REST with httpx (no SDKs)."""

import time
from typing import Any

import httpx

from backend.app.llm.base import (
    LLMConfigError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResult,
    LLMTimeoutError,
)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:500]
        return str(err)[:500]
    except ValueError:
        return response.text[:500]


async def _send(
    provider: str, client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> dict[str, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise LLMTimeoutError(provider, f"timed out: {exc.__class__.__name__}") from exc
    except httpx.TransportError as exc:
        raise LLMProviderError(provider, f"connection error: {exc}", retryable=True) from exc

    if response.status_code == 429:
        raise LLMRateLimitError(
            provider, f"rate limit reached: {_error_text(response)}", _retry_after(response)
        )
    if response.status_code in (401, 403):
        raise LLMConfigError(
            provider, f"API key rejected (HTTP {response.status_code}): {_error_text(response)}"
        )
    if response.status_code == 404:
        raise LLMConfigError(provider, f"model or endpoint not found: {_error_text(response)}")
    if response.status_code >= 400:
        raise LLMProviderError(
            provider,
            f"HTTP {response.status_code}: {_error_text(response)}",
            retryable=response.status_code >= 500,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise LLMProviderError(provider, "response was not JSON") from exc


class MockProvider:
    """Deterministic and offline. The default, and what the tests use."""

    name = "mock"

    def __init__(self, model: str = "mock-echo"):
        self.model = model

    async def complete(self, prompt: str, system: str | None = None) -> LLMResult:
        start = time.perf_counter()
        preview = " ".join(prompt.split())[:200]
        text = f"[mock response] You asked: {preview}"
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=_elapsed_ms(start),
            usage={"prompt_chars": len(prompt), "completion_chars": len(text)},
        )

    async def list_models(self) -> list[str]:
        return [self.model]


class GroqProvider:
    """Groq's OpenAI-compatible chat completions API."""

    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
        base_url: str,
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str = "",
    ):
        if not api_key:
            raise LLMConfigError(self.name, "GROQ_API_KEY is not set in .env")
        if not model:
            raise LLMConfigError(self.name, "GROQ_MODEL is not set in .env")
        self.model = model
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort

    async def complete(self, prompt: str, system: str | None = None) -> LLMResult:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self._max_output_tokens,
            "temperature": self._temperature,
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        start = time.perf_counter()
        data = await _send(
            self.name,
            self._client,
            "POST",
            f"{self._base_url}/chat/completions",
            headers=self._headers,
            json=payload,
        )
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(self.name, "unexpected response shape") from exc
        if not text.strip():
            raise LLMProviderError(
                self.name,
                f"empty response (finish_reason={choice.get('finish_reason')}); "
                "try raising LLM_MAX_OUTPUT_TOKENS",
            )
        return LLMResult(
            text=text.strip(),
            provider=self.name,
            model=data.get("model", self.model),
            latency_ms=_elapsed_ms(start),
            usage=data.get("usage") or {},
        )

    async def list_models(self) -> list[str]:
        data = await _send(
            self.name, self._client, "GET", f"{self._base_url}/models", headers=self._headers
        )
        return sorted(m["id"] for m in data.get("data", []) if m.get("active", True))


class GeminiProvider:
    """Google Gemini API (generateContent)."""

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.AsyncClient,
        base_url: str,
        max_output_tokens: int,
        temperature: float,
    ):
        if not api_key:
            raise LLMConfigError(self.name, "GEMINI_API_KEY is not set in .env")
        if not model:
            raise LLMConfigError(self.name, "GEMINI_MODEL is not set in .env")
        self.model = model
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._headers = {"x-goog-api-key": api_key}
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    async def complete(self, prompt: str, system: str | None = None) -> LLMResult:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": self._max_output_tokens,
                "temperature": self._temperature,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        start = time.perf_counter()
        data = await _send(
            self.name,
            self._client,
            "POST",
            f"{self._base_url}/models/{self.model}:generateContent",
            headers=self._headers,
            json=payload,
        )
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMProviderError(self.name, f"no response returned ({reason})")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMProviderError(
                self.name,
                f"empty response (finishReason={candidate.get('finishReason')}); "
                "try raising LLM_MAX_OUTPUT_TOKENS",
            )
        return LLMResult(
            text=text.strip(),
            provider=self.name,
            model=data.get("modelVersion", self.model),
            latency_ms=_elapsed_ms(start),
            usage=data.get("usageMetadata") or {},
        )

    async def list_models(self) -> list[str]:
        data = await _send(
            self.name,
            self._client,
            "GET",
            f"{self._base_url}/models",
            headers=self._headers,
            params={"pageSize": 1000},
        )
        return sorted(
            m["name"].removeprefix("models/")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        )
