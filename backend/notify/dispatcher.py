"""Send each notification once. A failed send is logged and retried on the next run."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from backend.engine.models import Notification
from backend.notify.payload import build_payload
from backend.notify.senders import ConsoleSender, Sender, SendError, WebhookSender
from backend.settings import Settings

log = logging.getLogger(__name__)

Summarizer = Callable[[Notification], str | None]


class SentStore(Protocol):
    def was_sent(self, notification_id: str) -> bool: ...

    def mark_sent(self, notification_id: str) -> None: ...


class InMemorySentStore:
    """Remembers sent ids for this process. The database version comes with the API."""

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def was_sent(self, notification_id: str) -> bool:
        return notification_id in self._ids

    def mark_sent(self, notification_id: str) -> None:
        self._ids.add(notification_id)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    notification_id: str
    status: str  # "sent" | "skipped" (already sent) | "failed"
    detail: str = ""


def dispatch(
    notifications: Iterable[Notification],
    sender: Sender,
    store: SentStore,
    summarize: Summarizer | None = None,
) -> list[DispatchResult]:
    results: list[DispatchResult] = []
    for n in notifications:
        if store.was_sent(n.notification_id):
            results.append(DispatchResult(n.notification_id, "skipped"))
            continue
        summary = None
        if summarize is not None:
            try:
                summary = summarize(n)
            except Exception:  # the AI must never block an alert
                log.warning("summary failed for %s; sending plain message", n.notification_id)
        try:
            sender.send(build_payload(n, summary))
        except SendError as exc:
            log.error("notification %s not delivered: %s", n.notification_id, exc)
            results.append(DispatchResult(n.notification_id, "failed", str(exc)))
            continue
        store.mark_sent(n.notification_id)
        log.info("notification %s sent via %s", n.notification_id, sender.name)
        results.append(DispatchResult(n.notification_id, "sent"))
    return results


def sender_from_settings(settings: Settings) -> Sender:
    if settings.alert_webhook_url is None:
        return ConsoleSender()
    return WebhookSender(
        settings.alert_webhook_url.get_secret_value(),
        timeout_seconds=settings.webhook_timeout_seconds,
        max_attempts=settings.webhook_max_attempts,
    )
