"""Tests always run in mock mode. They ignore .env, so they never use your API keys or quota."""

import socket

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly if any test tries to reach the internet (loopback is allowed for asyncio)."""
    real_connect = socket.socket.connect

    def guard(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise RuntimeError(
                f"Network access to {host} is blocked in tests. "
                "Use mock mode or httpx.MockTransport."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "frontend_dist": tmp_path / "dist",
        "llm_provider": "mock",
        "llm_fallback_provider": None,
        "groq_api_key": None,
        "gemini_api_key": None,
        "groq_model": "test-groq-model",
        "gemini_model": "test-gemini-model",
        "llm_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
