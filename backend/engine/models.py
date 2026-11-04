"""Data shapes used by the engine. Plain dataclasses, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TruckState(StrEnum):
    OK = "ok"
    SUSPECT = "suspect"  # out of range, timer running, nothing sent yet
    ALERT = "alert"  # alert open, still out of range
    RECOVERING = "recovering"  # alert open, back in range, waiting to confirm


class Severity(StrEnum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    RECOVERING = "recovering"
    CLOSED = "closed"


class NotificationKind(StrEnum):
    ALERT_OPENED = "alert_opened"
    ALERT_NOT_IMPROVING = "alert_not_improving"
    ALERT_ESCALATED = "alert_escalated"
    SENSOR_SILENT = "sensor_silent"


def minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60.0


def hhmm(ts: datetime) -> str:
    return ts.strftime("%H:%M")


@dataclass(frozen=True, slots=True)
class Reading:
    truck_id: str
    timestamp: datetime  # sensor time, always timezone-aware UTC
    temperature_c: float
    humidity_pct: float | None = None


@dataclass(frozen=True, slots=True)
class DataGap:
    """Readings missing between two real readings."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> float:
        return minutes_between(self.start, self.end)


@dataclass(slots=True)
class Alert:
    alert_id: str
    truck_id: str
    direction: str  # "above" or "below"
    limit_c: float
    started_at: datetime  # first out-of-range reading
    opened_at: datetime  # reading at which the alert fired
    last_out_at: datetime  # latest out-of-range reading
    peak_c: float  # worst reading in the episode
    severity: Severity
    status: AlertStatus
    reading_count: int = 0  # out-of-range readings in the episode
    updates_sent: int = 0  # "not improving" updates
    closed_at: datetime | None = None
    escalated_at: datetime | None = None
    gaps: list[DataGap] = field(default_factory=list)

    @property
    def duration_min(self) -> float:
        return minutes_between(self.started_at, self.last_out_at)

    @property
    def missing_data_min(self) -> float:
        return sum(g.minutes for g in self.gaps)

    def reason(self) -> str:
        text = (
            f"{self.direction.capitalize()} {self.limit_c:g} °C for "
            f"{self.duration_min:.0f} min ({hhmm(self.started_at)}–{hhmm(self.last_out_at)}), "
            f"peak {self.peak_c:g} °C."
        )
        if self.gaps:
            parts = ", ".join(
                f"{hhmm(g.start)}–{hhmm(g.end)} ({g.minutes:.0f} min)" for g in self.gaps
            )
            text += f" Includes missing data: {parts}."
        return text


@dataclass(frozen=True, slots=True)
class ShortEpisode:
    """Out of range, but not for long enough to alert (a blip, or still running)."""

    truck_id: str
    started_at: datetime
    last_out_at: datetime
    peak_c: float
    reading_count: int
    ongoing: bool  # still SUSPECT when the data ended

    @property
    def duration_min(self) -> float:
        return minutes_between(self.started_at, self.last_out_at)


@dataclass(frozen=True, slots=True)
class Silence:
    """A period with no readings from a truck, longer than silent_after_min."""

    truck_id: str
    last_reading_at: datetime
    resumed_at: datetime | None  # None = still silent at the evaluation time
    minutes: float
    was_out_of_range: bool  # truck was SUSPECT / ALERT / RECOVERING when it went quiet
    notify: bool

    @property
    def silence_id(self) -> str:
        return f"{self.truck_id}:silent:{self.last_reading_at.isoformat()}"


@dataclass(frozen=True, slots=True)
class Notification:
    """Something ops should be told about. The id is stable, so it is sent only once."""

    notification_id: str
    kind: NotificationKind
    truck_id: str
    at: datetime  # sensor time at which the condition was met
    severity: Severity
    message: str
    alert_id: str | None = None
