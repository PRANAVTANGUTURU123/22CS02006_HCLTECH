"""The JSON body we POST to the webhook."""

from __future__ import annotations

from typing import Any

from backend.engine.models import Notification, NotificationKind, Severity

# Discord rejects "content" over 2000 characters.
MAX_TEXT_CHARS = 1900

_ICONS = {
    NotificationKind.ALERT_OPENED: "🟠",
    NotificationKind.ALERT_NOT_IMPROVING: "🟠",
    NotificationKind.ALERT_ESCALATED: "🔴",
    NotificationKind.SENSOR_SILENT: "📡",
}


def build_text(notification: Notification, summary: str | None = None) -> str:
    """The message ops reads. With an AI summary, the rule-based facts stay underneath."""
    icon = "🔴" if notification.severity is Severity.CRITICAL else _ICONS[notification.kind]
    if summary:
        text = f"{icon} {summary}\nFacts: {notification.message}"
    else:
        text = f"{icon} {notification.message}"
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - 1] + "…"
    return text


def build_payload(notification: Notification, summary: str | None = None) -> dict[str, Any]:
    text = build_text(notification, summary)
    return {
        "content": text,  # Discord
        "text": text,  # Slack
        "notification_id": notification.notification_id,
        "kind": notification.kind.value,
        "severity": notification.severity.value,
        "truck_id": notification.truck_id,
        "alert_id": notification.alert_id,
        "at": notification.at.isoformat(),
        "ai_summary": summary is not None,
    }
