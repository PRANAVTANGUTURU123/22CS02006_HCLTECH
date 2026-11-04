import pytest
from fastapi.testclient import TestClient

from backend.ai import MockLLM
from backend.api import create_app
from backend.engine import clean_readings, evaluate_fleet
from backend.notify import InMemorySentStore
from backend.profiles import PRESETS, ProfileStore, make_config
from backend.settings import Settings
from backend.store import make_engine

KEY = {"X-API-Key": "k"}


@pytest.fixture
def store(tmp_path):
    return ProfileStore(make_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}"))


# --- store ------------------------------------------------------------------------------


def test_presets_are_seeded_and_new_trucks_get_the_default(store):
    assert set(store.profiles()) == set(PRESETS)
    cfg = store.config_for("T-NEW")  # never seen before
    assert (cfg.profile, cfg.min_safe_c, cfg.max_safe_c) == ("pharma", 2.0, 8.0)


def test_assign_and_unassign(store):
    store.assign("T-03", "frozen")
    assert store.config_for("T-03").max_safe_c == -18.0
    assert store.assignments() == {"T-03": "frozen"}
    store.assign("T-03", "pharma")  # back to the default removes the override
    assert store.assignments() == {}


def test_custom_profile_and_edit(store):
    store.upsert_profile("vaccines", 2.0, 8.0, 10.0)
    store.upsert_profile("vaccines", 2.0, 6.0, 10.0)
    assert store.profiles()["vaccines"].max_safe_c == 6.0


def test_settings_survive_a_restart(tmp_path):
    url = f"sqlite:///{(tmp_path / 'p.db').as_posix()}"
    ProfileStore(make_engine(url)).assign("T-03", "chilled")
    assert ProfileStore(make_engine(url)).config_for("T-03").profile == "chilled"


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (("bad name!", 0, 5, 20), "profile name"),
        (("x", 8, 2, 20), "min_safe_c must be below"),
        (("x", -45, 5, 20), "sensor fault limits"),
        (("x", 0, 5, 0), "alert_after_min must be positive"),
    ],
)
def test_bad_profiles_are_refused(store, args, error):
    with pytest.raises(ValueError, match=error):
        store.upsert_profile(*args)


def test_unknown_profile_or_bad_truck_id_is_refused(store):
    with pytest.raises(ValueError, match="unknown profile"):
        store.assign("T-01", "nope")
    with pytest.raises(ValueError, match="truck id"):
        store.assign("T" * 65, "frozen")


def test_unknown_default_profile_is_refused(tmp_path):
    with pytest.raises(ValueError, match="default profile"):
        ProfileStore(make_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}"), "nope")


# --- engine: each truck checked against its own range ----------------------------------


def test_same_temperature_means_different_things_per_cargo():
    raw = [
        {"truck_id": t, "timestamp": f"2026-07-20T08:{m:02d}:00", "temperature_c": -10.0}
        for t in ("PHARMA", "FROZEN")
        for m in range(0, 25, 5)
    ]
    frozen = make_config("frozen", -25, -18, 20)
    report = evaluate_fleet(
        clean_readings(raw), config_for=lambda t: frozen if t == "FROZEN" else make_config("pharma", 2, 8, 20)
    )
    alerts = {a.truck_id: a for a in report.alerts}
    assert alerts["FROZEN"].direction == "above" and alerts["FROZEN"].limit_c == -18
    assert alerts["PHARMA"].direction == "below" and alerts["PHARMA"].limit_c == 2
    assert "above -18 °C" in report.notifications[0].message or "above -18 °C" in report.notifications[1].message


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, frontend_dist=tmp_path / "no-ui", admin_api_key="k")
    return TestClient(create_app(settings, store=InMemorySentStore(), llm=MockLLM()))


def test_list_profiles(client):
    data = client.get("/api/profiles").json()
    assert data["default"] == "pharma"
    assert [p["name"] for p in data["profiles"]] == ["ambient", "chilled", "frozen", "pharma"]
    trucks = client.get("/api/trucks").json()
    assert {t["profile"] for t in trucks} == {"pharma"}


def test_assigning_a_stricter_range_reevaluates_immediately(client):
    # T-03 sits at 5.8-7.0 °C: fine for pharma (2-8), too warm for chilled food (0-5).
    assert not any(a["truck_id"] == "T-03" for a in client.get("/api/alerts").json())
    r = client.put("/api/trucks/T-03/profile", json={"profile": "chilled"}, headers=KEY)
    assert r.status_code == 200 and r.json()["assignments"] == {"T-03": "chilled"}
    t03 = next(t for t in client.get("/api/trucks").json() if t["truck_id"] == "T-03")
    assert (t03["profile"], t03["max_safe_c"]) == ("chilled", 5.0)
    [alert] = [a for a in client.get("/api/alerts").json() if a["truck_id"] == "T-03"]
    assert alert["limit_c"] == 5.0
    assert alert["reason"].startswith("Above 5 °C")


def test_editing_a_profile_applies_to_its_trucks(client):
    r = client.put("/api/profiles/pharma", json={"min_safe_c": 2, "max_safe_c": 9.7}, headers=KEY)
    assert r.status_code == 200
    assert client.get("/api/alerts").json() == []  # peak 9.6 °C is now in range


def test_profile_changes_need_the_key_and_valid_input(client):
    assert client.put("/api/trucks/T-03/profile", json={"profile": "chilled"}).status_code == 401
    assert client.put("/api/profiles/x", json={"min_safe_c": 0, "max_safe_c": 5}).status_code == 401
    bad = client.put("/api/profiles/x", json={"min_safe_c": 9, "max_safe_c": 5}, headers=KEY)
    assert bad.status_code == 422 and "min_safe_c must be below" in bad.json()["detail"]
    assert client.put("/api/trucks/T-03/profile", json={"profile": "nope"}, headers=KEY).status_code == 422
    assert client.put("/api/profiles/x", json={"min_safe_c": -99, "max_safe_c": 5}, headers=KEY).status_code == 422
