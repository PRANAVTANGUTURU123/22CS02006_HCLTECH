"""Alerting thresholds. Reasons for each default: docs/ASSUMPTIONS.md."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AlertConfig:
    # "default" or "custom" (a truck's own range)
    profile: str = "pharma"
    # 2-8 °C: refrigerated pharma, the strictest cargo
    min_safe_c: float = 2.0
    max_safe_c: float = 8.0

    alert_after_min: float = 20.0

    # back in range this long, over at least this many readings, before it counts as normal
    clear_after_min: float = 10.0
    clear_min_readings: int = 2

    critical_after_min: float = 60.0
    critical_excess_c: float = 3.0

    # "not improving" check; 0.3 °C is above normal sensor jitter
    update_every_min: float = 10.0
    improve_min_c: float = 0.3

    # gaps over 1.5x this are reported as missing data
    expected_interval_min: float = 5.0

    silent_after_min: float = 15.0
    # a truck that was already out of range is notified at silent_after_min instead
    silent_notify_min: float = 30.0

    # outside these limits = sensor fault
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
