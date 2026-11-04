"""Every alerting threshold in one place.

The reasons behind each default are in docs/ASSUMPTIONS.md. Change numbers here,
never in the engine code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertConfig:
    # Cargo profile this config belongs to (per-truck ranges: backend/profiles.py).
    profile: str = "pharma"
    # Safe range for the cargo (2-8 °C: refrigerated pharma, the strictest Nimbus cargo).
    min_safe_c: float = 2.0
    max_safe_c: float = 8.0

    # Out of range this long (by sensor timestamps) before we alert.
    alert_after_min: float = 20.0

    # Back in range this long since the last out-of-range reading, seen by at least this
    # many in-range readings, before a truck counts as normal again.
    clear_after_min: float = 10.0
    clear_min_readings: int = 2

    # An open alert becomes Critical when the episode lasts this long or the worst reading
    # is this far outside the safe range.
    critical_after_min: float = 60.0
    critical_excess_c: float = 3.0

    # While a Warning alert stays out of range, check every this many minutes whether the
    # temperature has moved back toward the safe range by at least improve_min_c. If not,
    # send one "not improving" update. More than sensor jitter (0.1-0.2 °C).
    update_every_min: float = 10.0
    improve_min_c: float = 0.3

    # Normal reading cadence; a gap over 1.5x this is reported as missing data.
    expected_interval_min: float = 5.0

    # A gap longer than this is a "sensor silent" period. It is only bridged inside an
    # episode when the readings on both sides are out of range.
    silent_after_min: float = 15.0
    # Notify about a silence after this long (or right at silent_after_min if the truck
    # was already out of range when it went quiet).
    silent_notify_min: float = 30.0

    # Readings outside this range are physically impossible for a reefer: sensor fault.
    sensor_min_c: float = -40.0
    sensor_max_c: float = 50.0

    def __post_init__(self) -> None:
        if not self.min_safe_c < self.max_safe_c:
            raise ValueError("min_safe_c must be below max_safe_c")
        if not self.sensor_min_c < self.min_safe_c or not self.max_safe_c < self.sensor_max_c:
            raise ValueError("sensor fault limits must lie outside the safe range")
        for name in (
            "alert_after_min",
            "clear_after_min",
            "critical_after_min",
            "critical_excess_c",
            "update_every_min",
            "expected_interval_min",
            "silent_after_min",
            "silent_notify_min",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.improve_min_c < 0:
            raise ValueError("improve_min_c must not be negative")
        if self.clear_min_readings < 1:
            raise ValueError("clear_min_readings must be at least 1")
        if self.silent_notify_min < self.silent_after_min:
            raise ValueError("silent_notify_min must be >= silent_after_min")

    def is_out_of_range(self, temperature_c: float) -> bool:
        """Exactly on a limit (e.g. 8.0 °C) counts as in range."""
        return temperature_c > self.max_safe_c or temperature_c < self.min_safe_c

    def excess_c(self, temperature_c: float) -> float:
        """How far outside the safe range a reading is (0 when in range)."""
        if temperature_c > self.max_safe_c:
            return temperature_c - self.max_safe_c
        if temperature_c < self.min_safe_c:
            return self.min_safe_c - temperature_c
        return 0.0

    def is_sensor_fault(self, temperature_c: float) -> bool:
        return temperature_c < self.sensor_min_c or temperature_c > self.sensor_max_c


DEFAULT_CONFIG = AlertConfig()
