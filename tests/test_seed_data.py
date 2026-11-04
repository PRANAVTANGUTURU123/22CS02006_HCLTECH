"""End-to-end on the provided seed data: one test per truck story."""
import random
import sys
from pathlib import Path

import pytest

from backend.engine import (
    AlertConfig,
    AlertStatus,
    Severity,
    TruckState,
    clean_readings,
    evaluate_fleet,
    load_raw_file,
)
from tests.helpers import at

SEED = Path(__file__).resolve().parent.parent / "starter_kit" / "data" / "sensor_readings.json"


@pytest.fixture(scope="module")
def raw():
    return load_raw_file(SEED)


@pytest.fixture(scope="module")
def report(raw):
    return evaluate_fleet(clean_readings(raw))


def alerts_for(report, truck_id):
    return [a for a in report.alerts if a.truck_id == truck_id]


def truck(report, truck_id):
    return next(t for t in report.trucks if t.truck_id == truck_id)


def test_seed_file_is_clean(report):
    assert report.clean.total_input == 54
    assert report.clean.accepted_count == 54
    assert report.clean.rejected == report.clean.duplicates == report.clean.faults == []
    assert report.evaluated_at == at(50)


def test_exactly_three_trucks_alert(report):
    assert sorted(a.truck_id for a in report.alerts) == ["T-01", "T-04", "T-05"]
    assert all(a.severity is Severity.WARNING for a in report.alerts)


def test_t01_sustained_warming_alerts_then_closes(report):
    [alert] = alerts_for(report, "T-01")
    assert (alert.started_at, alert.opened_at, alert.last_out_at) == (at(10), at(30), at(40))
    assert alert.duration_min == 30
    assert alert.peak_c == 9.6
    assert alert.status is AlertStatus.CLOSED
    assert alert.closed_at == at(50)


def test_t02_single_blip_is_ignored(report):
    assert alerts_for(report, "T-02") == []
    assert truck(report, "T-02").state is TruckState.OK


def test_t03_warming_but_in_range_is_ignored(report):
    assert alerts_for(report, "T-03") == []
    assert truck(report, "T-03").state is TruckState.OK


def test_t04_alerts_across_the_missing_reading(report):
    [alert] = alerts_for(report, "T-04")
    assert (alert.started_at, alert.opened_at, alert.last_out_at) == (at(25), at(45), at(45))
    assert alert.duration_min == 20
    assert alert.peak_c == 9.1
    assert [(g.start, g.end) for g in alert.gaps] == [(at(30), at(40))]
    assert alert.status is AlertStatus.RECOVERING  # only one in-range reading before data ends
    assert truck(report, "T-04").state is TruckState.RECOVERING


def test_t05_shuffled_file_gives_the_same_alert_as_t01(report):
    [t01] = alerts_for(report, "T-01")
    [t05] = alerts_for(report, "T-05")
    for attr in ("started_at", "opened_at", "last_out_at", "closed_at", "peak_c", "status"):
        assert getattr(t05, attr) == getattr(t01, attr)


def test_one_notification_per_alert_and_no_silent_sensors(report):
    assert [(n.truck_id, n.at) for n in report.notifications] == [
        ("T-01", at(30)),
        ("T-05", at(30)),
        ("T-04", at(45)),
    ]
    assert report.silences == []


def test_result_does_not_depend_on_input_order(raw, report):
    def summary(r):
        return [(a.alert_id, a.opened_at, a.closed_at, a.peak_c) for a in r.alerts]

    for seed in range(5):
        shuffled = list(raw)
        random.Random(seed).shuffle(shuffled)
        assert summary(evaluate_fleet(clean_readings(shuffled))) == summary(report)


def test_25_minute_limit_drops_t04(raw):
    report = evaluate_fleet(clean_readings(raw), AlertConfig(alert_after_min=25))
    assert sorted(a.truck_id for a in report.alerts) == ["T-01", "T-05"]


def test_starter_code_misses_t04_and_t05(raw):
    """Documents why the starter utility was replaced."""
    sys.path.insert(0, str(SEED.parent.parent))
    try:
        import alert_utils
    finally:
        sys.path.pop(0)
    found = {ep[0]["truck_id"] for ep in alert_utils.find_alertable_episodes(raw)}
    assert found == {"T-01"}
