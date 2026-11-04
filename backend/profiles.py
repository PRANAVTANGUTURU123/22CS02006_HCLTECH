"""Cargo profiles: each truck's safe range, editable at runtime (no code changes).

- Profiles (name, safe range, alert time) and truck -> profile assignments live in the DB.
- A truck with no assignment (e.g. a brand-new truck) uses the default profile, so it is
  never unprotected. The default is the strictest one: pharma 2-8 °C.
"""

from __future__ import annotations

import re
import threading
from dataclasses import replace

from sqlalchemy import Column, Float, String, Table, delete, insert, select, update
from sqlalchemy.engine import Engine

from backend.engine.config import DEFAULT_CONFIG, AlertConfig
from backend.store import metadata

NAME_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_TRUCK_ID = 64

# name: (min °C, max °C, alert after minutes)
PRESETS: dict[str, tuple[float, float, float]] = {
    "pharma": (2.0, 8.0, 20.0),
    "chilled": (0.0, 5.0, 20.0),
    "frozen": (-25.0, -18.0, 20.0),
    "ambient": (15.0, 25.0, 30.0),
}

cargo_profiles = Table(
    "cargo_profiles",
    metadata,
    Column("name", String(32), primary_key=True),
    Column("min_safe_c", Float, nullable=False),
    Column("max_safe_c", Float, nullable=False),
    Column("alert_after_min", Float, nullable=False),
)

truck_profiles = Table(
    "truck_profiles",
    metadata,
    Column("truck_id", String(MAX_TRUCK_ID), primary_key=True),
    Column("profile", String(32), nullable=False),
)


def make_config(name: str, min_c: float, max_c: float, alert_after: float) -> AlertConfig:
    """Raises ValueError for a bad range (e.g. min above max)."""
    return replace(
        DEFAULT_CONFIG,
        profile=name,
        min_safe_c=float(min_c),
        max_safe_c=float(max_c),
        alert_after_min=float(alert_after),
    )


class ProfileStore:
    def __init__(self, engine: Engine, default_profile: str = "pharma") -> None:
        self._engine = engine
        self._lock = threading.Lock()
        metadata.create_all(engine)
        with engine.begin() as conn:
            have = set(conn.execute(select(cargo_profiles.c.name)).scalars())
            for name, (lo, hi, after) in PRESETS.items():
                if name not in have:
                    conn.execute(
                        insert(cargo_profiles).values(
                            name=name, min_safe_c=lo, max_safe_c=hi, alert_after_min=after
                        )
                    )
        self._load()
        if default_profile not in self._profiles:
            raise ValueError(f"default profile {default_profile!r} does not exist")
        self.default_profile = default_profile

    def _load(self) -> None:
        with self._engine.connect() as conn:
            profiles = {
                row.name: make_config(row.name, row.min_safe_c, row.max_safe_c, row.alert_after_min)
                for row in conn.execute(select(cargo_profiles))
            }
            assignments = {
                row.truck_id: row.profile for row in conn.execute(select(truck_profiles))
            }
        with self._lock:
            self._profiles, self._assignments = profiles, assignments

    # --- reads (from memory, so evaluating the fleet never waits on the DB) ---

    def profiles(self) -> dict[str, AlertConfig]:
        return dict(self._profiles)

    def assignments(self) -> dict[str, str]:
        return dict(self._assignments)

    def config_for(self, truck_id: str) -> AlertConfig:
        name = self._assignments.get(truck_id, self.default_profile)
        return self._profiles.get(name, self._profiles[self.default_profile])

    # --- writes ---

    def upsert_profile(self, name: str, min_c: float, max_c: float, alert_after: float) -> None:
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError("profile name must be 1-32 chars: a-z, 0-9, _ or -")
        make_config(name, min_c, max_c, alert_after)  # validate before saving
        values = {"min_safe_c": min_c, "max_safe_c": max_c, "alert_after_min": alert_after}
        with self._engine.begin() as conn:
            found = conn.execute(
                select(cargo_profiles.c.name).where(cargo_profiles.c.name == name)
            ).first()
            if found:
                conn.execute(
                    update(cargo_profiles).where(cargo_profiles.c.name == name).values(**values)
                )
            else:
                conn.execute(insert(cargo_profiles).values(name=name, **values))
        self._load()

    def assign(self, truck_id: str, profile: str) -> None:
        """Assign a profile; assigning the default profile removes the override."""
        if not truck_id or len(truck_id) > MAX_TRUCK_ID:
            raise ValueError("truck id must be 1-64 characters")
        if profile not in self._profiles:
            raise ValueError(f"unknown profile; known: {', '.join(sorted(self._profiles))}")
        with self._engine.begin() as conn:
            conn.execute(delete(truck_profiles).where(truck_profiles.c.truck_id == truck_id))
            if profile != self.default_profile:
                conn.execute(insert(truck_profiles).values(truck_id=truck_id, profile=profile))
        self._load()
