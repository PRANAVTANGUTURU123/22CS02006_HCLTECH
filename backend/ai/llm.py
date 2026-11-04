"""Tiny synchronous LLM clients: mock, Groq (OpenAI-compatible) and Gemini, over httpx."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

import httpx

for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class LLMError(Exception):
    """Any LLM failure. Messages never include API keys."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, timeout: float) -> str: ...


def _post(client: httpx.Client, url: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
    try:
        response = client.post(url, timeout=timeout, **kwargs)
    except httpx.TimeoutException as exc:
        raise LLMError("timed out") from exc
    except httpx.TransportError as exc:
        raise LLMError(f"network error ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        raise LLMError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise LLMError("response was not JSON") from exc


class MockLLM:
    """Offline and deterministic. Writes a valid summary from the facts in the prompt."""

    name = "mock"

    def complete(self, system: str, prompt: str, timeout: float) -> str:
        f = json.loads(prompt)
        cause = {
            "rising": "likely a reefer cooling problem",
            "flat": "likely a reefer cooling problem",
            "falling": "it is cooling down again",
        }[f["temperature_trend"]]
        return (
            f"Truck {f['truck_id']} has been {f['direction']} {f['limit_c']:g} °C for "
            f"{f['minutes_out_of_range']} min (peak {f['peak_temp_c']:g} °C); {cause}."
        )

    def chat(self, messages: list[dict], tools: list[dict], timeout: float) -> dict[str, Any]:
        """Offline stand-in for tool calling: ask for open alerts, then answer from them."""
        results = [m for m in messages if m.get("role") == "tool"]
        if not results:
            call = {"name": "get_open_alerts", "arguments": "{}"}
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": call}],
            }
        alerts = json.loads(results[-1]["content"] or "[]")
        if isinstance(alerts, dict):
            alerts = [alerts]
        if not alerts:
            return {"role": "assistant", "content": "No truck has an open alert right now."}
        parts = [f"{a['truck_id']} ({a['status']}, peak {a['peak_c']:g} °C)" for a in alerts]
        return {
            "role": "assistant",
            "content": "Trucks with open alerts: " + ", ".join(parts) + ".",
        }


class GroqLLM:
    name = "groq"

    def __init__(self, api_key: str, model: str, client: httpx.Client, reasoning_effort: str = ""):
        if not api_key or not model:
            raise LLMError("GROQ_API_KEY and GROQ_MODEL must be set")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._client = client
        self._effort = reasoning_effort

    def complete(self, system: str, prompt: str, timeout: float) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": 600,
            "temperature": 0.2,
        }
        if self._effort:
            body["reasoning_effort"] = self._effort
        data = _post(
            self._client,
            "https://api.groq.com/openai/v1/chat/completions",
            timeout,
            headers=self._headers,
            json=body,
        )
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape") from exc

    def chat(self, messages: list[dict], tools: list[dict], timeout: float) -> dict[str, Any]:
        """One tool-calling turn. Returns the assistant message (may contain tool_calls)."""
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_completion_tokens": 800,
            "temperature": 0.1,
        }
        if self._effort:
            body["reasoning_effort"] = self._effort
        data = _post(
            self._client,
            "https://api.groq.com/openai/v1/chat/completions",
            timeout,
            headers=self._headers,
            json=body,
        )
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape") from exc


class GeminiLLM:
    name = "gemini"

    def __init__(self, api_key: str, model: str, client: httpx.Client):
        if not api_key or not model:
            raise LLMError("GEMINI_API_KEY and GEMINI_MODEL must be set")
        self._headers = {"x-goog-api-key": api_key}
        self._model = model
        self._client = client

    def complete(self, system: str, prompt: str, timeout: float) -> str:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 300, "temperature": 0.2},
        }
        data = _post(
            self._client,
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent",
            timeout,
            headers=self._headers,
            json=body,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected response shape") from exc


class WithFallback:
    """Try the primary; on any error, try the fallback within the same time budget."""

    def __init__(self, primary: LLM, fallback: LLM | None):
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name if fallback is None else f"{primary.name}+{fallback.name}"
        self.last_used: str | None = None

    def complete(self, system: str, prompt: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        try:
            text = self.primary.complete(system, prompt, timeout)
            self.last_used = self.primary.name
            return text
        except LLMError:
            remaining = deadline - time.monotonic()
            if self.fallback is None or remaining < 1.0:
                raise
            text = self.fallback.complete(system, prompt, remaining)
            self.last_used = self.fallback.name
            return text
