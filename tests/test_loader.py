import json
import math
from datetime import UTC, datetime

import pytest

from backend.engine.loader import clean_readings, load_raw_file, parse_reading


def raw(truck="T-01", ts="2026-07-20T08:00:00", temp=6.5, hum=55):
    return {"truck_id": truck, "timestamp": ts, "temperature_c": temp, "humidity_pct": hum}


def test_readings_are_sorted_per_truck_by_sensor_time():
    data = clean_readings(
        [raw(ts="2026-07-20T08:10:00"), raw(ts="2026-07-20T08:00:00"), raw(ts="2026-07-20T08:05:00")]
    )
    minutes = [r.timestamp.minute for r in data.by_truck["T-01"]]
    assert minutes == [0, 5, 10]


def test_naive_timestamp_is_treated_as_utc():
    reading = parse_reading(raw(ts="2026-07-20T08:00:00"))
    assert reading.timestamp == datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def test_timestamp_with_offset_is_converted_to_utc():
    reading = parse_reading(raw(ts="2026-07-20T13:30:00+05:30"))
    assert reading.timestamp == datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def test_same_time_in_different_zones_is_a_duplicate():
    data = clean_readings([raw(ts="2026-07-20T08:00:00Z", temp=6.5), raw(ts="2026-07-20T13:30:00+05:30", temp=9.9)])
    assert [r.temperature_c for r in data.by_truck["T-01"]] == [6.5]  # first one wins
    assert len(data.duplicates) == 1


def test_exact_duplicate_is_dropped_and_counted():
    data = clean_readings([raw(), raw()])
    assert data.accepted_count == 1
    assert len(data.duplicates) == 1


@pytest.mark.parametrize("temp", [-41.0, 50.1, 999])
def test_impossible_temperature_is_a_sensor_fault_not_a_reading(temp):
    data = clean_readings([raw(temp=temp)])
    assert data.accepted_count == 0
    assert [r.temperature_c for r in data.faults] == [float(temp)]


@pytest.mark.parametrize("temp", [-40.0, 50.0])
def test_fault_limits_themselves_are_valid(temp):
    assert clean_readings([raw(temp=temp)]).accepted_count == 1


@pytest.mark.parametrize(
    ("reading", "reason"),
    [
        ("not a dict", "JSON object"),
        ({"timestamp": "2026-07-20T08:00:00", "temperature_c": 5}, "truck_id"),
        (raw(truck="   "), "truck_id"),
        (raw(truck="T" * 65), "too long"),
        ({"truck_id": "T-01", "temperature_c": 5}, "timestamp is missing"),
        (raw(ts="yesterday"), "ISO 8601"),
        (raw(ts=1721462400), "ISO 8601"),
        (raw(temp=None), "temperature_c"),
        (raw(temp="7.5"), "temperature_c"),
        (raw(temp=True), "temperature_c"),
        (raw(temp=math.nan), "temperature_c"),
        (raw(temp=math.inf), "temperature_c"),
    ],
)
def test_invalid_readings_are_rejected_with_a_reason(reading, reason):
    data = clean_readings([raw(ts="2026-07-20T07:00:00"), reading])
    assert data.accepted_count == 1
    assert len(data.rejected) == 1
    assert data.rejected[0].index == 1
    assert reason in data.rejected[0].reason


@pytest.mark.parametrize("hum", [None, -1, 101, "high", True])
def test_bad_humidity_keeps_the_temperature_reading(hum):
    data = clean_readings([raw(hum=hum)])
    assert data.accepted_count == 1
    assert data.by_truck["T-01"][0].humidity_pct is None


def test_truck_id_whitespace_is_trimmed():
    data = clean_readings([raw(truck=" T-01 ")])
    assert list(data.by_truck) == ["T-01"]


def test_latest_timestamp_across_fleet():
    data = clean_readings([raw("T-01", "2026-07-20T08:00:00"), raw("T-02", "2026-07-20T08:50:00")])
    assert data.latest_timestamp() == datetime(2026, 7, 20, 8, 50, tzinfo=UTC)
    assert clean_readings([]).latest_timestamp() is None


def test_load_raw_file_accepts_object_or_list(tmp_path):
    obj = tmp_path / "obj.json"
    obj.write_text(json.dumps({"readings": [raw()]}), encoding="utf-8")
    lst = tmp_path / "list.json"
    lst.write_text(json.dumps([raw()]), encoding="utf-8")
    assert load_raw_file(obj) == load_raw_file(lst) == [raw()]


@pytest.mark.parametrize("payload", [{"rows": []}, {"readings": {}}, "text", 5])
def test_load_raw_file_rejects_wrong_shape(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="readings"):
        load_raw_file(path)
