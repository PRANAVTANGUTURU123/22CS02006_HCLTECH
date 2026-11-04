from datetime import UTC, datetime, timedelta

from backend.engine.models import Reading

BASE = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def at(minute: float) -> datetime:
    return BASE + timedelta(minutes=minute)


def readings(points: list[tuple[float, float]], truck_id: str = "T-99") -> list[Reading]:
    """Build sorted readings from (minute offset, temperature) pairs."""
    return [Reading(truck_id, at(m), t) for m, t in sorted(points)]


def every_5_min(temps: list[float], start: int = 0) -> list[tuple[float, float]]:
    return [(start + 5 * i, t) for i, t in enumerate(temps)]
