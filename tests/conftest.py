import pytest


@pytest.fixture(autouse=True)
def _no_real_webhook(monkeypatch):
    """Tests must never post to a real channel, whatever is in the developer's .env."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
