"""End-to-end: real server + built dashboard + real browser (installed Chrome).

    cd frontend; npm run build; cd ..
    .\\.venv\\Scripts\\python -m pytest tests_e2e --browser-channel chrome --no-cov

Uses a mock LLM and a fake webhook sender, so nothing leaves the machine.
"""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from backend.ai import MockLLM
from backend.api import create_app
from backend.settings import PROJECT_ROOT, Settings
from backend.store import SqlSentStore, make_engine

DIST = PROJECT_ROOT / "frontend" / "dist"
pytestmark = pytest.mark.skipif(not DIST.is_dir(), reason="run `npm run build` in frontend/ first")


class FakeSender:
    name = "fake"

    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    db = tmp_path_factory.mktemp("db") / "app.db"
    sender = FakeSender()
    app = create_app(
        Settings(_env_file=None, frontend_dist=DIST),
        sender=sender,
        store=SqlSentStore(make_engine(f"sqlite:///{db.as_posix()}")),
        llm=MockLLM(),
    )
    srv = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health").status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.1)
    yield base, sender
    srv.should_exit = True
    thread.join(timeout=5)


def test_dashboard_shows_seed_alerts_and_chart(server, page):
    base, _ = server
    page.goto(base)
    page.get_by_role("alert").wait_for()
    assert "1 open alert: T-04 (recovering, peak 9.1 °C)" in page.get_by_role("alert").inner_text()
    assert page.locator(".alert-card").count() == 3
    assert page.locator(".ai-label").count() == 3
    page.locator("tbody tr", has_text="T-04").click()
    page.get_by_role("img", name="Temperature chart for T-04").wait_for()
    assert page.locator("svg .alert-span").count() == 1


def test_upload_to_alert_to_notification_to_screen(server, page):
    base, sender = server
    warm = [
        {"truck_id": "T-09", "timestamp": f"2026-07-20T08:{m}:00", "temperature_c": 9.4,
         "humidity_pct": 60}
        for m in range(30, 55, 5)
    ]
    result = httpx.post(f"{base}/api/readings", json={"readings": warm}).json()
    assert any(s.startswith("T-09:") for s in result["sent"])
    t09 = [p for p in sender.payloads if p["truck_id"] == "T-09"]
    assert len(t09) == 1 and t09[0]["ai_summary"] is True  # one push, AI-worded

    page.goto(base)
    row = page.locator("tbody tr", has_text="T-09")
    row.wait_for()
    assert "Alert" in row.inner_text()
    assert "T-09" in page.get_by_role("alert").inner_text()

    # Re-uploading the same readings changes nothing and sends nothing.
    again = httpx.post(f"{base}/api/readings", json={"readings": warm}).json()
    assert again["sent"] == [] and again["duplicates"] == 5
