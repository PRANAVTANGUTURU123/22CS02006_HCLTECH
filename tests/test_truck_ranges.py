import pytest
from fastapi.testclient import TestClient

from backend.ai import MockLLM
from backend.api import create_app
from backend.engine import clean_readings, evaluate_fleet
from backend.engine.config import DEFAULT_CONFIG
from backend.notify import InMemorySentStore
from backend.settings import Settings
from backend.store import make_engine
from backend.trucks import TruckRegistry, build_config

KEY = {"X-API-Key": "k"}


@pytest.fixture
def registry(tmp_path):
    return TruckRegistry(make_engine(f"sqlite:///{(tmp_path / 'r.db').as_posix()}"))


# --- registry -----------------------------------------------------------------------------


def test_new_truck_is_registered_with_an_empty_range_and_uses_the_default(registry):
    assert registry.register(["T-NEW"]) == ["T-NEW"]
    row = registry.get("T-NEW")
    assert (row.min_safe_c, row.max_safe_c, row.alert_after_min) == (None, None, None)
    assert not row.is_custom
    cfg = registry.config_for("T-NEW")
    assert (cfg.min_safe_c, cfg.max_safe_c, cfg.profile) == (2.0, 8.0, "default")
    assert registry.register(["T-NEW"]) == []  # only the first time


def test_unregistered_truck_also_gets_the_default(registry):
    assert registry.config_for("NEVER-SEEN").max_safe_c == 8.0


def test_fill_in_a_truck_range_later_and_clear_it(registry):
    registry.register(["T-07"])
    registry.set_range("T-07", -25, -18, 15)
    cfg = registry.config_for("T-07")
    assert (cfg.min_safe_c, cfg.max_safe_c, cfg.alert_after_min, cfg.profile) == (-25, -18, 15, "custom")
    registry.set_range("T-07", None, None, None)  # back to the default
    assert registry.config_for("T-07").max_safe_c == 8.0


def test_alert_time_alone_can_be_set(registry):
    registry.set_range("T-08", None, None, 10)
    cfg = registry.config_for("T-08")
    assert (cfg.max_safe_c, cfg.alert_after_min) == (8.0, 10)


def test_ranges_survive_a_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'r.db').as_posix()}"
    TruckRegistry(make_engine(url)).set_range("T-03", 0, 5)
    assert TruckRegistry(make_engine(url)).config_for("T-03").max_safe_c == 5


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("T-1", 8, 2), "min_safe_c must be below"),
        (("T-1", 0, None), "set both"),
        (("T-1", -45, 5), "sensor fault limits"),
        (("T-1", 0, 5, 0), "alert_after_min must be positive"),
        (("T" * 65, 0, 5), "truck id"),
    ],
)
def test_bad_ranges_are_refused(registry, args, error):
    with pytest.raises(ValueError, match=error):
        registry.set_range(*args)


def test_same_temperature_means_different_things_per_truck():
    raw = [
        {"truck_id": t, "timestamp": f"2026-07-20T08:{m:02d}:00", "temperature_c": -10.0}
        for t in ("PHARMA", "FROZEN")
        for m in range(0, 25, 5)
    ]
    frozen = build_config(-25, -18, None, DEFAULT_CONFIG)
    report = evaluate_fleet(clean_readings(raw), config_for=lambda t: frozen if t == "FROZEN" else DEFAULT_CONFIG)
    alerts = {a.truck_id: a for a in report.alerts}
    assert (alerts["FROZEN"].direction, alerts["FROZEN"].limit_c) == ("above", -18)
    assert (alerts["PHARMA"].direction, alerts["PHARMA"].limit_c) == ("below", 2)


# --- API -----------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui", admin_api_key="k")
    return TestClient(create_app(settings, store=InMemorySentStore(), llm=MockLLM()))


def test_seed_trucks_are_registered_with_empty_ranges(client):
    data = client.get("/api/ranges").json()
    assert data["default"] == {"min_safe_c": 2.0, "max_safe_c": 8.0, "alert_after_min": 20.0}
    assert [t["truck_id"] for t in data["trucks"]] == ["T-01", "T-02", "T-03", "T-04", "T-05"]
    assert all(t["min_safe_c"] is None and not t["custom"] for t in data["trucks"])
    assert all(t["added_at"] for t in data["trucks"])  # when each was added to the DB
    assert {t["profile"] for t in client.get("/api/trucks").json()} == {"default"}


def test_setting_a_truck_range_reevaluates_immediately(client):
    # T-03 sits at 5.8-7.0 °C: fine for 2-8 °C, too warm for a 0-5 °C cargo.
    r = client.put("/api/trucks/T-03/range", json={"min_safe_c": 0, "max_safe_c": 5}, headers=KEY)
    assert r.status_code == 200 and r.json()["custom"] is True
    t03 = next(t for t in client.get("/api/trucks").json() if t["truck_id"] == "T-03")
    assert (t03["profile"], t03["max_safe_c"]) == ("custom", 5.0)
    [alert] = [a for a in client.get("/api/alerts").json() if a["truck_id"] == "T-03"]
    assert alert["reason"].startswith("Above 5 °C")
    cleared = client.put("/api/trucks/T-03/range", json={"min_safe_c": None, "max_safe_c": None}, headers=KEY)
    assert cleared.json()["custom"] is False
    assert not any(a["truck_id"] == "T-03" for a in client.get("/api/alerts").json())


def test_new_truck_in_an_upload_is_registered_and_can_bring_its_range(client):
    body = {
        "readings": [
            {"truck_id": "T-20", "timestamp": f"2026-07-20T08:{m}:00", "temperature_c": -10.0}
            for m in range(30, 55, 5)
        ]
        + [{"truck_id": "T-21", "timestamp": "2026-07-20T08:50:00", "temperature_c": 5.0}],
        "trucks": [{"truck_id": "T-20", "min_safe_c": -25, "max_safe_c": -18}],
    }
    data = client.post("/api/readings", json=body, headers=KEY).json()
    assert data["ranges_set"] == ["T-20"]
    rows = {t["truck_id"]: t for t in client.get("/api/ranges").json()["trucks"]}
    assert rows["T-20"]["max_safe_c"] == -18
    assert rows["T-21"]["custom"] is False  # new, no range given: empty, default applies
    t20 = [a for a in client.get("/api/alerts").json() if a["truck_id"] == "T-20"]
    assert t20 and t20[0]["reason"].startswith("Above -18 °C")


def test_upload_never_overwrites_a_range_ops_already_set(client):
    client.put("/api/trucks/T-03/range", json={"min_safe_c": 0, "max_safe_c": 5}, headers=KEY)
    body = {
        "readings": [{"truck_id": "T-03", "timestamp": "2026-07-20T08:50:00", "temperature_c": 6.1}],
        "trucks": [{"truck_id": "T-03", "min_safe_c": 2, "max_safe_c": 8}],
    }
    assert client.post("/api/readings", json=body, headers=KEY).json()["ranges_set"] == []
    rows = {t["truck_id"]: t for t in client.get("/api/ranges").json()["trucks"]}
    assert rows["T-03"]["max_safe_c"] == 5


def test_range_changes_need_the_key_and_valid_input(client):
    body = {"min_safe_c": 0, "max_safe_c": 5}
    assert client.put("/api/trucks/T-03/range", json=body).status_code == 401
    bad = client.put("/api/trucks/T-03/range", json={"min_safe_c": 9, "max_safe_c": 5}, headers=KEY)
    assert bad.status_code == 422 and "min_safe_c must be below" in bad.json()["detail"]
    assert client.put("/api/trucks/T-03/range", json={"min_safe_c": -99, "max_safe_c": 5}, headers=KEY).status_code == 422
    assert client.put("/api/trucks/T-03/range", json={"min_safe_c": 0}, headers=KEY).status_code == 422


def test_range_change_sends_new_alerts_once(tmp_path):
    sent = []

    class Sender:
        name = "fake"

        def send(self, payload):
            sent.append(payload["notification_id"])

    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui", admin_api_key="k")
    client = TestClient(create_app(settings, sender=Sender(), store=InMemorySentStore(), llm=MockLLM()))
    body = {"min_safe_c": 0, "max_safe_c": 5}
    first = client.put("/api/trucks/T-03/range", json=body, headers=KEY).json()
    assert any(s.startswith("T-03:") for s in first["sent"])
    assert any(s.startswith("T-03:") for s in sent)
    client.put("/api/trucks/T-03/range", json={"min_safe_c": None, "max_safe_c": None}, headers=KEY)
    again = client.put("/api/trucks/T-03/range", json=body, headers=KEY).json()
    assert again["sent"] == []
