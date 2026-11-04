"""Truck registry. A new truck gets a row with an empty range, which means the default applies."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, String, Table, insert, select, update
from sqlalchemy.engine import Engine

from backend.engine.config import DEFAULT_CONFIG, AlertConfig
from backend.store import metadata

MAX_TRUCK_ID = 64

trucks_table = Table(
    "trucks",
    metadata,
    Column("truck_id", String(MAX_TRUCK_ID), primary_key=True),
    # NULL = not set, default applies
    Column("min_safe_c", Float, nullable=True),
    Column("max_safe_c", Float, nullable=True),
    Column("alert_after_min", Float, nullable=True),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True, slots=True)
class TruckRange:
    truck_id: str
    min_safe_c: float | None
    max_safe_c: float | None
    alert_after_min: float | None
    first_seen_at: datetime | None = None  # when the truck was added to the database

    @property
    def is_custom(self) -> bool:
        return self.min_safe_c is not None and self.max_safe_c is not None


def build_config(
    min_c: float | None, max_c: float | None, alert_after: float | None, default: AlertConfig
) -> AlertConfig:
    """Raises ValueError for a bad range (e.g. min above max, outside sensor limits)."""
    if (min_c is None) != (max_c is None):
        raise ValueError("set both min_safe_c and max_safe_c, or neither")
    changes: dict = {}
    if min_c is not None and max_c is not None:
        changes.update(profile="custom", min_safe_c=float(min_c), max_safe_c=float(max_c))
    if alert_after is not None:
        changes["alert_after_min"] = float(alert_after)
    return replace(default, **changes) if changes else default


class TruckRegistry:
    def __init__(self, engine: Engine, default: AlertConfig = DEFAULT_CONFIG) -> None:
        self._engine = engine
        self._default = replace(default, profile="default")
        self._lock = threading.Lock()
        metadata.create_all(engine)
        self._rows: dict[str, TruckRange] = {}
        self._load()

    @property
    def default(self) -> AlertConfig:
        return self._default

    def _load(self) -> None:
        with self._engine.connect() as conn:
            rows = {
                r.truck_id: TruckRange(
                    r.truck_id, r.min_safe_c, r.max_safe_c, r.alert_after_min, r.first_seen_at
                )
                for r in conn.execute(select(trucks_table))
            }
        with self._lock:
            self._rows = rows

    def all(self) -> list[TruckRange]:
        return sorted(self._rows.values(), key=lambda t: t.truck_id)

    def get(self, truck_id: str) -> TruckRange | None:
        return self._rows.get(truck_id)

    def config_for(self, truck_id: str) -> AlertConfig:
        row = self._rows.get(truck_id)
        if row is None:
            return self._default
        return build_config(row.min_safe_c, row.max_safe_c, row.alert_after_min, self._default)

    def register(self, truck_ids: list[str] | set[str]) -> list[str]:
        """Add trucks seen for the first time, with an empty range. Returns the new ids."""
        new = [
            t for t in sorted(set(truck_ids)) if t not in self._rows and 0 < len(t) <= MAX_TRUCK_ID
        ]
        if new:
            now = datetime.now(UTC)
            with self._engine.begin() as conn:
                existing = set(conn.execute(select(trucks_table.c.truck_id)).scalars())
                for truck_id in new:
                    if truck_id not in existing:
                        conn.execute(
                            insert(trucks_table).values(truck_id=truck_id, first_seen_at=now)
                        )
            self._load()
        return new

    def set_range(
        self,
        truck_id: str,
        min_c: float | None,
        max_c: float | None,
        alert_after: float | None = None,
    ) -> TruckRange:
        """Set (or, with all None, clear) a truck's own range. Registers the truck if new."""
        if not truck_id or len(truck_id) > MAX_TRUCK_ID:
            raise ValueError("truck id must be 1-64 characters")
        build_config(min_c, max_c, alert_after, self._default)  # validate before saving
        self.register([truck_id])
        values = {"min_safe_c": min_c, "max_safe_c": max_c, "alert_after_min": alert_after}
        with self._engine.begin() as conn:
            conn.execute(
                update(trucks_table).where(trucks_table.c.truck_id == truck_id).values(**values)
            )
        self._load()
        return self._rows[truck_id]
