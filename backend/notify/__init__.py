"""Deliver engine notifications: console by default, Discord/Slack webhook if configured."""
from backend.notify.dispatcher import (
    DispatchResult,
    InMemorySentStore,
    SentStore,
    dispatch,
    sender_from_settings,
)
from backend.notify.payload import build_payload, build_text
from backend.notify.senders import ConsoleSender, Sender, SendError, WebhookSender

__all__ = [
    "ConsoleSender",
    "DispatchResult",
    "InMemorySentStore",
    "SendError",
    "Sender",
    "SentStore",
    "WebhookSender",
    "build_payload",
    "build_text",
    "dispatch",
    "sender_from_settings",
]
