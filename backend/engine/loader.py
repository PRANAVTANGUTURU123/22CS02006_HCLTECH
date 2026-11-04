"""Turn raw sensor JSON into clean, per-truck, time-sorted readings.

File order means nothing: trucks lose signal and upload in bursts, so readings are
always sorted by sensor timestamp before any rule runs (T-05 in the seed data).
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.engine.config import DEFAULT_CONFIG, AlertConfig
from backend.engine.models import Reading

MAX_TRUCK_ID_LENGTH = 64


@dataclass(frozen=True, slots=True)
class Rejected:
    index: int  # position in the input
    reason: str


@dataclass(slots=True)
class CleanData:
    by_truck: dict[str, list[Reading]] = field(default_factory=dict)  # each list time-sorted
    rejected: list[Rejected] = field(default_factory=list)
    duplicates: list[Reading] = field(default_factory=list)
    faults: list[Reading] = field(default_factory=list)  # impossible temperatures
    total_input: int = 0

    @property
    def accepted_count(self) -> int:
        return sum(len(rs) for rs in self.by_truck.values())

    def latest_timestamp(self) -> datetime | None:
        latest = [rs[-1].timestamp for rs in self.by_truck.values() if rs]
        return max(latest) if latest else None


def _is_number(value: Any) -> bool:
    # bool is a subclass of int in Python; "true" is not a temperature.
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    try:
        ts = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp is not valid ISO 8601") from exc
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)  # assumption: timestamps without a zone are UTC
    return ts.astimezone(UTC)


def parse_reading(raw: Any) -> Reading:
    """Validate one raw reading. Raises ValueError with a short reason."""
    if not isinstance(raw, dict):
        raise ValueError("reading must be a JSON object")

    truck_id = raw.get("truck_id")
    if not isinstance(truck_id, str) or not truck_id.strip():
        raise ValueError("truck_id is missing or empty")
    truck_id = truck_id.strip()
    if len(truck_id) > MAX_TRUCK_ID_LENGTH:
        raise ValueError("truck_id is too long")

    if "timestamp" not in raw:
        raise ValueError("timestamp is missing")
    timestamp = parse_timestamp(raw["timestamp"])

    temperature = raw.get("temperature_c")
    if not _is_number(temperature):
        raise ValueError("temperature_c is missing or not a number")

    # Humidity never drives alerts, so a bad value is dropped rather than losing the
    # temperature reading with it.
    humidity = raw.get("humidity_pct")
    if not (_is_number(humidity) and 0 <= humidity <= 100):
        humidity = None

    return Reading(
        truck_id=truck_id,
        timestamp=timestamp,
        temperature_c=float(temperature),
        humidity_pct=None if humidity is None else float(humidity),
    )


def clean_readings(raw_readings: Iterable[Any], config: AlertConfig = DEFAULT_CONFIG) -> CleanData:
    data = CleanData()
    seen: set[tuple[str, datetime]] = set()
    for index, raw in enumerate(raw_readings):
        data.total_input += 1
        try:
            reading = parse_reading(raw)
        except ValueError as exc:
            data.rejected.append(Rejected(index=index, reason=str(exc)))
            continue
        if config.is_sensor_fault(reading.temperature_c):
            data.faults.append(reading)
            continue
        key = (reading.truck_id, reading.timestamp)
        if key in seen:  # first one wins
            data.duplicates.append(reading)
            continue
        seen.add(key)
        data.by_truck.setdefault(reading.truck_id, []).append(reading)

    for readings in data.by_truck.values():
        readings.sort(key=lambda r: r.timestamp)
    data.by_truck = dict(sorted(data.by_truck.items()))
    return data


def load_raw_file(path: str | Path) -> list[Any]:
    """Read a JSON file shaped like the seed data ({"readings": [...]}) or a bare list."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        payload = payload.get("readings")
    if not isinstance(payload, list):
        raise ValueError('expected a JSON list or an object with a "readings" list')
    return payload
