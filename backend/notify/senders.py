"""Where notifications go. Swap senders without touching the engine."""
from __future__ import annotations

import logging
import sys
from typing import Any, Protocol, TextIO

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

# httpx logs every request URL at INFO. A webhook URL *is* the secret (its token is in the
# path), so keep httpx/httpcore quiet below WARNING. Found by test_notify's log check.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class SendError(Exception):
    """Delivery failed. The message never contains the webhook URL."""


class Sender(Protocol):
    name: str

    def send(self, payload: dict[str, Any]) -> None: ...


class ConsoleSender:
    """Default when no webhook is configured, and in tests."""

    name = "console"

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream

    def send(self, payload: dict[str, Any]) -> None:
        print(f"[notify] {payload['text']}", file=self.stream or sys.stdout)


class _Retryable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WebhookSender:
    """POST JSON to a Discord / Slack incoming webhook (HTTPS only)."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        client: httpx.Client | None = None,
        wait_max_seconds: float = 4.0,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("webhook URL must start with https://")
        self._url = url
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._max_attempts = max_attempts
        self._wait_max = wait_max_seconds

    def __repr__(self) -> str:  # never show the URL
        return "WebhookSender(url=***)"

    def _post_once(self, payload: dict[str, Any]) -> None:
        try:
            response = self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise _Retryable("timeout") from exc
        except httpx.TransportError as exc:
            raise _Retryable(f"network error ({type(exc).__name__})") from exc
        status = response.status_code
        if status == 429 or status >= 500:
            raise _Retryable(f"HTTP {status}")
        if status >= 400:
            # Bad or revoked webhook: retrying won't help.
            raise SendError(f"webhook rejected the message (HTTP {status})")

    def send(self, payload: dict[str, Any]) -> None:
        attempt = retry(
            retry=retry_if_exception(lambda e: isinstance(e, _Retryable)),
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=0.5, max=self._wait_max),
            reraise=True,
        )(self._post_once)
        try:
            attempt(payload)
        except _Retryable as exc:
            raise SendError(
                f"webhook failed after {self._max_attempts} attempts: {exc.reason}"
            ) from None
