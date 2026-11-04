import pytest
from fastapi.testclient import TestClient

from backend.ai import MockLLM
from backend.api import create_app
from backend.notify import InMemorySentStore
from backend.settings import Settings
from backend.store import SqlSentStore, make_engine


class FakeSender:
    name = "fake"

    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def client(tmp_path, sender):
    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui", max_upload_readings=100)
    store = SqlSentStore(make_engine(f"sqlite:///{(tmp_path / 'db' / 'app.db').as_posix()}"))
    return TestClient(create_app(settings, sender=sender, store=store, llm=MockLLM()))


def reading(truck, minute, temp):
    return {"truck_id": truck, "timestamp": f"2026-07-20T09:{minute:02d}:00", "temperature_c": temp}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_report_on_seed_data(client):
    data = client.get("/api/report").json()
    assert [a["truck_id"] for a in data["alerts"]] == ["T-01", "T-05", "T-04"]
    assert all(n["ai_summary"] for n in data["notifications"])


def test_trucks_and_truck_detail(client):
    trucks = {t["truck_id"]: t for t in client.get("/api/trucks").json()}
    assert trucks["T-04"]["state"] == "recovering"
    assert trucks["T-02"]["short_episodes"][0]["reading_count"] == 1
    detail = client.get("/api/trucks/T-04").json()
    assert len(detail["readings"]) == 10
    assert detail["alerts"][0]["duration_min"] == 20
    assert client.get("/api/trucks/T-99").status_code == 404


def test_alert_filter(client):
    assert [a["truck_id"] for a in client.get("/api/alerts?status=recovering").json()] == ["T-04"]
    assert client.get("/api/alerts?status=bogus").status_code == 422


def test_startup_sends_nothing(client, sender):
    assert sender.payloads == []
    assert not any(n["sent"] for n in client.get("/api/notifications").json())


def test_upload_alerts_once_and_never_resends(client, sender):
    # 08:30-08:50, inside the seed hour, so no other truck looks silent.
    warm = [
        {"truck_id": "T-09", "timestamp": f"2026-07-20T08:{m}:00", "temperature_c": 9.4}
        for m in range(30, 55, 5)
    ]  # 20 min above 8 °C
    first = client.post("/api/readings", json={"readings": warm}).json()
    # The 3 seed alerts go out too on the first dispatch, plus the new T-09 alert.
    assert len(first["sent"]) == 4
    assert any(s.startswith("T-09:") for s in first["sent"])
    t09 = [p for p in sender.payloads if p["truck_id"] == "T-09"]
    assert t09[0]["ai_summary"] is True
    again = client.post("/api/readings", json={"readings": warm}).json()
    assert again["sent"] == [] and again["duplicates"] == 5
    assert all(n["sent"] for n in client.get("/api/notifications").json())


def test_sent_ids_survive_a_restart(tmp_path, sender):
    url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui")
    body = {"readings": [reading("T-09", 0, 6.0)]}
    first = TestClient(create_app(settings, sender, SqlSentStore(make_engine(url)), MockLLM()))
    assert len(first.post("/api/readings", json=body).json()["sent"]) == 3
    second = TestClient(create_app(settings, sender, SqlSentStore(make_engine(url)), MockLLM()))
    assert second.post("/api/readings", json=body).json()["sent"] == []


def test_bad_readings_are_rejected_one_by_one(client):
    body = {"readings": [reading("T-09", 0, 6.0), {"truck_id": "T-09"}, reading("T-09", 5, 99)]}
    data = client.post("/api/readings", json=body).json()
    assert data["rejected"] == [{"index": 55, "reason": "timestamp is missing"}]
    assert data["sensor_faults"] == 1


def test_replace_and_reset(client):
    data = client.post("/api/readings", json={"readings": [reading("T-09", 0, 6.0)], "replace": True}).json()
    assert data["accepted"] == 1 and data["alerts"] == 0
    assert client.post("/api/reset").json() == {"readings": 54}
    assert len(client.get("/api/alerts").json()) == 3


@pytest.mark.parametrize(
    "body",
    [{}, {"readings": []}, {"readings": "nope"}, {"readings": [1, 2]}],
)
def test_malformed_upload_is_422(client, body):
    assert client.post("/api/readings", json=body).status_code == 422


def test_upload_limits(client):
    too_many = {"readings": [reading("T-09", 0, 6.0)] * 101}
    assert client.post("/api/readings", json=too_many).status_code == 413
    huge = b'{"readings": [' + b" " * 2_000_001 + b"]}"
    response = client.post("/api/readings", content=huge, headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_in_memory_store_also_works(tmp_path, sender):
    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui")
    app = create_app(settings, sender, InMemorySentStore(), MockLLM())
    assert TestClient(app).get("/health").status_code == 200


def test_upload_that_moves_time_forward_reports_silent_sensors(client):
    data = client.post("/api/readings", json={"readings": [reading("T-09", 20, 6.0)]}).json()
    # Now is 09:20: the five seed trucks last reported at 08:50, 30 min ago.
    assert len([s for s in data["sent"] if ":silent:" in s]) == 5
