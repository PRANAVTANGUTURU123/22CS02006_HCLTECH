from fastapi.testclient import TestClient

from backend.app.main import create_app
from tests.conftest import make_settings


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm_provider"] == "mock"
    assert r.headers["X-Request-ID"]


def test_request_id_is_echoed(client):
    r = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_llm_complete_mock(client):
    r = client.post("/api/llm/complete", json={"prompt": "Hello there"})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "mock"
    assert "Hello there" in body["text"]
    assert body["fallback_used"] is False


def test_validation_error_shape(client):
    r = client.post("/api/llm/complete", json={"prompt": ""})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["request_id"]
    assert err["details"]


def test_unknown_api_route_is_json_404(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_missing_key_gives_clear_503(tmp_path):
    settings = make_settings(tmp_path, llm_provider="groq")
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/llm/complete", json={"prompt": "hi"})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "llm_not_configured"
    assert "GROQ_API_KEY" in err["message"]


def test_fallback_to_mock_when_primary_unconfigured(tmp_path):
    settings = make_settings(tmp_path, llm_provider="gemini", llm_fallback_provider="mock")
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/llm/complete", json={"prompt": "hi"})
    assert r.status_code == 200
    assert r.json()["provider"] == "mock"
    assert r.json()["fallback_used"] is True


def test_frontend_not_built_message(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Frontend not built" in r.json()["message"]


def test_serves_built_frontend(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    with TestClient(create_app(make_settings(tmp_path))) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/some/client/route").text == "<html>app</html>"
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/../../etc/passwd").text == "<html>app</html>"
