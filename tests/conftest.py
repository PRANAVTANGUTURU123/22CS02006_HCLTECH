import pytest


@pytest.fixture(autouse=True)
def _no_real_webhook(monkeypatch, tmp_path):
    """Tests must never post to a real channel, whatever is in the developer's .env."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    # Never touch the developer's real database.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
