import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from backend.cli import main
from backend.engine.models import Notification, NotificationKind, Severity
from backend.notify import (
    ConsoleSender,
    InMemorySentStore,
    SendError,
    WebhookSender,
    build_payload,
    dispatch,
    sender_from_settings,
)
from backend.settings import Settings
from tests.helpers import at

SECRET_URL = "https://discord.example/api/webhooks/123/very-secret-token"


def note(nid="T-04:start:opened", kind=NotificationKind.ALERT_OPENED, severity=Severity.WARNING):
    return Notification(
        notification_id=nid,
        kind=kind,
        truck_id="T-04",
        at=at(45),
        severity=severity,
        message="WARNING: Truck T-04 above 8 °C for 20 min (08:25–08:45), peak 9.1 °C.",
        alert_id="T-04:start",
    )


class Recorder:
    """httpx transport that returns queued responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else 204
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item)


def webhook(recorder, attempts=3):
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    return WebhookSender(SECRET_URL, client=client, max_attempts=attempts, wait_max_seconds=0)


# --- payload -------------------------------------------------------------------------


def test_payload_works_for_discord_and_slack():
    payload = build_payload(note())
    assert payload["content"] == payload["text"]
    assert payload["text"].startswith("🟠 WARNING: Truck T-04")
    assert payload["notification_id"] == "T-04:start:opened"
    assert payload["at"] == "2026-07-20T08:45:00+00:00"
    assert payload["ai_summary"] is False


def test_critical_gets_red_icon():
    assert build_payload(note(severity=Severity.CRITICAL))["text"].startswith("🔴")


def test_ai_summary_keeps_the_facts_underneath():
    payload = build_payload(note(), summary="T-04 is too warm; likely a reefer problem.")
    assert payload["text"].splitlines() == [
        "🟠 T-04 is too warm; likely a reefer problem.",
        "Facts: WARNING: Truck T-04 above 8 °C for 20 min (08:25–08:45), peak 9.1 °C.",
    ]
    assert payload["ai_summary"] is True


def test_long_text_is_cut_to_discord_limit():
    assert len(build_payload(note(), summary="x" * 5000)["content"]) == 1900


# --- webhook sender ----------------------------------------------------------------


def test_webhook_posts_json_once_on_success():
    rec = Recorder(204)
    webhook(rec).send({"text": "hi"})
    assert len(rec.requests) == 1
    assert rec.requests[0].method == "POST"
    assert json.loads(rec.requests[0].content) == {"text": "hi"}


@pytest.mark.parametrize("first", [500, 503, 429, httpx.ConnectError("down"), httpx.ReadTimeout("slow")])
def test_temporary_failures_are_retried(first):
    rec = Recorder(first, 204)
    webhook(rec).send({"text": "hi"})
    assert len(rec.requests) == 2


def test_gives_up_after_max_attempts_without_leaking_the_url():
    rec = Recorder(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    with pytest.raises(SendError) as exc:
        webhook(rec).send({"text": "hi"})
    assert len(rec.requests) == 3
    assert str(exc.value) == "webhook failed after 3 attempts: timeout"
    assert "secret" not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize("status", [400, 401, 404])
def test_rejected_webhook_is_not_retried(status):
    rec = Recorder(status)
    with pytest.raises(SendError, match=f"HTTP {status}"):
        webhook(rec).send({"text": "hi"})
    assert len(rec.requests) == 1


def test_only_https_urls_are_accepted():
    with pytest.raises(ValueError, match="https"):
        WebhookSender("http://insecure.example/hook")


def test_repr_hides_the_url():
    assert "secret" not in repr(webhook(Recorder()))


# --- dispatcher --------------------------------------------------------------------


class FakeSender:
    name = "fake"

    def __init__(self, fail_times=0):
        self.payloads = []
        self.fail_times = fail_times

    def send(self, payload):
        if self.fail_times:
            self.fail_times -= 1
            raise SendError("webhook failed after 3 attempts: HTTP 503")
        self.payloads.append(payload)


def test_each_notification_is_sent_only_once():
    sender, store = FakeSender(), InMemorySentStore()
    first = dispatch([note("a"), note("b")], sender, store)
    second = dispatch([note("a"), note("b"), note("c")], sender, store)
    assert [r.status for r in first] == ["sent", "sent"]
    assert [r.status for r in second] == ["skipped", "skipped", "sent"]
    assert [p["notification_id"] for p in sender.payloads] == ["a", "b", "c"]


def test_failed_send_is_retried_on_the_next_run_and_does_not_stop_others(caplog):
    sender, store = FakeSender(fail_times=1), InMemorySentStore()
    with caplog.at_level(logging.ERROR):
        results = dispatch([note("a"), note("b")], sender, store)
    assert [r.status for r in results] == ["failed", "sent"]
    assert "notification a not delivered" in caplog.text
    assert [r.status for r in dispatch([note("a")], sender, store)] == ["sent"]


def test_summary_is_used_when_available():
    sender = FakeSender()
    dispatch([note()], sender, InMemorySentStore(), summarize=lambda n: "Plain words.")
    assert sender.payloads[0]["text"].startswith("🟠 Plain words.\nFacts: WARNING")


def test_broken_summarizer_never_blocks_the_alert():
    def boom(_):
        raise RuntimeError("LLM down")

    sender = FakeSender()
    results = dispatch([note()], sender, InMemorySentStore(), summarize=boom)
    assert results[0].status == "sent"
    assert sender.payloads[0]["ai_summary"] is False


def test_webhook_failure_logs_never_contain_the_url(caplog):
    rec = Recorder(500, 500, 500)
    with caplog.at_level(logging.DEBUG):
        results = dispatch([note()], webhook(rec), InMemorySentStore())
    assert results[0].status == "failed"
    assert "very-secret-token" not in caplog.text


def test_console_sender_prints(capsys):
    ConsoleSender().send(build_payload(note()))
    assert capsys.readouterr().out.startswith("[notify] 🟠 WARNING: Truck T-04")


# --- settings ----------------------------------------------------------------------


def test_blank_webhook_means_console():
    settings = Settings(_env_file=None, alert_webhook_url="  ")
    assert settings.alert_webhook_url is None
    assert isinstance(sender_from_settings(settings), ConsoleSender)


def test_webhook_url_is_secret_and_https_only():
    settings = Settings(
        _env_file=None,
        alert_webhook_url=SECRET_URL,
        database_url="postgresql+psycopg://appuser:db-pass-123@host/appdb",
    )
    assert "very-secret-token" not in repr(settings)
    assert "db-pass-123" not in repr(settings)
    assert isinstance(sender_from_settings(settings), WebhookSender)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alert_webhook_url="http://insecure.example/hook")


# --- CLI --send ----------------------------------------------------------------------


def test_cli_send_without_webhook_prints_to_console(capsys):
    assert main(["--send"]) == 0
    out = capsys.readouterr().out
    assert out.count("[notify] ") == 3
    assert "Delivered 3 of 3 notifications via console." in out


def test_cli_send_with_bad_url_does_not_print_it(monkeypatch, capsys):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://insecure.example/very-secret-token")
    assert main(["--send"]) == 2
    captured = capsys.readouterr()
    assert "must start with https://" in captured.err
    assert "very-secret-token" not in captured.err + captured.out
