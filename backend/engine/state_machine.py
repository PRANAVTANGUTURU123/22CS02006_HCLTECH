"""Per-truck state machine: OK -> SUSPECT -> ALERT -> RECOVERING -> OK.

The rules and the reasons for each number are in docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from backend.engine.config import DEFAULT_CONFIG, AlertConfig
from backend.engine.loader import CleanData
from backend.engine.models import (
    Alert,
    AlertStatus,
    DataGap,
    Notification,
    NotificationKind,
    Reading,
    Severity,
    ShortEpisode,
    Silence,
    TruckState,
    hhmm,
    minutes_between,
)


@dataclass(slots=True)
class _Episode:
    started_at: datetime
    last_out_at: datetime
    peak: Reading
    peak_excess: float
    out_count: int = 0
    in_count: int = 0  # in-range readings since the last out-of-range one
    gaps: list[DataGap] = field(default_factory=list)
    alert: Alert | None = None
    next_check_at: datetime | None = None  # next "is it improving?" check
    check_ref: Reading | None = None  # reading used at the previous check

    @property
    def duration_min(self) -> float:
        return minutes_between(self.started_at, self.last_out_at)


@dataclass(slots=True)
class TruckResult:
    truck_id: str
    state: TruckState
    last_reading: Reading | None
    silent_now: bool
    alerts: list[Alert]
    silences: list[Silence]
    notifications: list[Notification]
    short_episodes: list[ShortEpisode]  # out of range, but not long enough to alert
    config: AlertConfig = DEFAULT_CONFIG  # the profile this truck was checked against

    @property
    def open_alert(self) -> Alert | None:
        for alert in self.alerts:
            if alert.status is not AlertStatus.CLOSED:
                return alert
        return None


@dataclass(slots=True)
class FleetReport:
    evaluated_at: datetime | None
    trucks: list[TruckResult]
    clean: CleanData

    @property
    def alerts(self) -> list[Alert]:
        return sorted((a for t in self.trucks for a in t.alerts), key=lambda a: a.opened_at)

    @property
    def notifications(self) -> list[Notification]:
        return sorted((n for t in self.trucks for n in t.notifications), key=lambda n: n.at)

    @property
    def silences(self) -> list[Silence]:
        return [s for t in self.trucks for s in t.silences]


def _alert_message(alert: Alert) -> str:
    reason = alert.reason()
    return f"{alert.severity.upper()}: Truck {alert.truck_id} {reason[0].lower()}{reason[1:]}"


def _is_critical(ep: _Episode, config: AlertConfig) -> bool:
    return (
        ep.duration_min >= config.critical_after_min or ep.peak_excess >= config.critical_excess_c
    )


def evaluate_truck(
    truck_id: str,
    readings: list[Reading],
    config: AlertConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> TruckResult:
    """Run the state machine over one truck's readings (must be sorted by timestamp)."""
    state = TruckState.OK
    ep: _Episode | None = None
    alerts: list[Alert] = []
    silences: list[Silence] = []
    notifications: list[Notification] = []
    short_episodes: list[ShortEpisode] = []
    prev: Reading | None = None

    def record_short(episode: _Episode, ongoing: bool) -> None:
        short_episodes.append(
            ShortEpisode(
                truck_id=truck_id,
                started_at=episode.started_at,
                last_out_at=episode.last_out_at,
                peak_c=episode.peak.temperature_c,
                reading_count=episode.out_count,
                ongoing=ongoing,
            )
        )

    def record_silence(last: Reading, resumed_at: datetime | None, until: datetime) -> None:
        minutes = minutes_between(last.timestamp, until)
        was_out = state is not TruckState.OK
        notify_after = config.silent_after_min if was_out else config.silent_notify_min
        notify = minutes >= notify_after
        silence = Silence(
            truck_id=truck_id,
            last_reading_at=last.timestamp,
            resumed_at=resumed_at,
            minutes=minutes,
            was_out_of_range=was_out,
            notify=notify,
        )
        silences.append(silence)
        if notify:
            context = " while out of range" if was_out else ""
            notifications.append(
                Notification(
                    notification_id=silence.silence_id,
                    kind=NotificationKind.SENSOR_SILENT,
                    truck_id=truck_id,
                    at=last.timestamp + timedelta(minutes=notify_after),
                    severity=Severity.CRITICAL if was_out else Severity.WARNING,
                    message=(
                        f"SENSOR SILENT: Truck {truck_id} has sent no data for "
                        f"{notify_after:.0f}+ min{context} (last reading "
                        f"{last.temperature_c:.1f} °C at {hhmm(last.timestamp)})."
                    ),
                    alert_id=ep.alert.alert_id if ep and ep.alert else None,
                )
            )

    for reading in readings:
        out = config.is_out_of_range(reading.temperature_c)

        if prev is not None:
            gap_min = minutes_between(prev.timestamp, reading.timestamp)
            if gap_min > config.silent_after_min:
                record_silence(prev, reading.timestamp, reading.timestamp)
                both_out = out and config.is_out_of_range(prev.temperature_c)
                if state is TruckState.SUSPECT and not both_out and ep is not None:
                    record_short(ep, ongoing=False)
                    ep, state = None, TruckState.OK  # can't prove it stayed out of range
            if ep is not None and gap_min > 1.5 * config.expected_interval_min:
                ep.gaps.append(DataGap(prev.timestamp, reading.timestamp))

        if out:
            excess = config.excess_c(reading.temperature_c)
            if ep is None:
                ep = _Episode(
                    started_at=reading.timestamp,
                    last_out_at=reading.timestamp,
                    peak=reading,
                    peak_excess=excess,
                )
                state = TruckState.SUSPECT
            ep.last_out_at = reading.timestamp
            ep.out_count += 1
            ep.in_count = 0
            if excess > ep.peak_excess:
                ep.peak, ep.peak_excess = reading, excess

            if state is TruckState.RECOVERING:
                state = TruckState.ALERT  # same episode, same alert

            if state is TruckState.SUSPECT and ep.duration_min >= config.alert_after_min:
                state = TruckState.ALERT
                above = ep.peak.temperature_c > config.max_safe_c
                ep.alert = Alert(
                    alert_id=f"{truck_id}:{ep.started_at.isoformat()}",
                    truck_id=truck_id,
                    direction="above" if above else "below",
                    limit_c=config.max_safe_c if above else config.min_safe_c,
                    started_at=ep.started_at,
                    opened_at=reading.timestamp,
                    last_out_at=ep.last_out_at,
                    peak_c=ep.peak.temperature_c,
                    severity=Severity.CRITICAL if _is_critical(ep, config) else Severity.WARNING,
                    status=AlertStatus.OPEN,
                    reading_count=ep.out_count,
                    gaps=list(ep.gaps),
                )
                alerts.append(ep.alert)
                ep.check_ref = reading
                ep.next_check_at = reading.timestamp + timedelta(minutes=config.update_every_min)
                notifications.append(
                    Notification(
                        notification_id=f"{ep.alert.alert_id}:opened",
                        kind=NotificationKind.ALERT_OPENED,
                        truck_id=truck_id,
                        at=reading.timestamp,
                        severity=ep.alert.severity,
                        message=_alert_message(ep.alert),
                        alert_id=ep.alert.alert_id,
                    )
                )

            if ep.alert is not None:
                alert = ep.alert
                alert.status = AlertStatus.OPEN
                alert.last_out_at = ep.last_out_at
                alert.peak_c = ep.peak.temperature_c
                alert.reading_count = ep.out_count
                alert.gaps = list(ep.gaps)
                if alert.severity is Severity.WARNING and _is_critical(ep, config):
                    alert.severity = Severity.CRITICAL
                    alert.escalated_at = reading.timestamp
                    notifications.append(
                        Notification(
                            notification_id=f"{alert.alert_id}:critical",
                            kind=NotificationKind.ALERT_ESCALATED,
                            truck_id=truck_id,
                            at=reading.timestamp,
                            severity=Severity.CRITICAL,
                            message=_alert_message(alert),
                            alert_id=alert.alert_id,
                        )
                    )
                elif (
                    alert.severity is Severity.WARNING
                    and ep.next_check_at is not None
                    and ep.check_ref is not None
                    and reading.timestamp >= ep.next_check_at
                ):
                    ref = ep.check_ref
                    # 9.6 -> 9.3 must count as 0.3, not 0.2999...
                    improved_by = round(config.excess_c(ref.temperature_c) - excess, 6)
                    if improved_by < config.improve_min_c:
                        alert.updates_sent += 1
                        notifications.append(
                            Notification(
                                notification_id=f"{alert.alert_id}:update:{alert.updates_sent}",
                                kind=NotificationKind.ALERT_NOT_IMPROVING,
                                truck_id=truck_id,
                                at=reading.timestamp,
                                severity=Severity.WARNING,
                                message=(
                                    f"UPDATE: Truck {truck_id} still {alert.direction} "
                                    f"{alert.limit_c:g} °C after {ep.duration_min:.0f} min and "
                                    f"not improving ({ref.temperature_c:.1f} → "
                                    f"{reading.temperature_c:.1f} °C since "
                                    f"{hhmm(ref.timestamp)})."
                                ),
                                alert_id=alert.alert_id,
                            )
                        )
                    # from this check, so a late check after a gap isn't followed by another
                    ep.check_ref = reading
                    ep.next_check_at = reading.timestamp + timedelta(
                        minutes=config.update_every_min
                    )

        elif ep is not None:
            ep.in_count += 1
            if state is TruckState.ALERT:
                state = TruckState.RECOVERING
                assert ep.alert is not None
                ep.alert.status = AlertStatus.RECOVERING
            if ep.alert is not None:
                ep.alert.gaps = list(ep.gaps)
            recovered = (
                minutes_between(ep.last_out_at, reading.timestamp) >= config.clear_after_min
                and ep.in_count >= config.clear_min_readings
            )
            if recovered:
                if ep.alert is not None:
                    ep.alert.status = AlertStatus.CLOSED
                    ep.alert.closed_at = reading.timestamp
                else:
                    record_short(ep, ongoing=False)
                ep, state = None, TruckState.OK

        prev = reading

    if state is TruckState.SUSPECT and ep is not None:
        record_short(ep, ongoing=True)

    silent_now = False
    if prev is not None and now is not None:
        if minutes_between(prev.timestamp, now) > config.silent_after_min:
            silent_now = True
            record_silence(prev, None, now)

    return TruckResult(
        truck_id=truck_id,
        state=state,
        last_reading=prev,
        silent_now=silent_now,
        alerts=alerts,
        short_episodes=short_episodes,
        config=config,
        silences=silences,
        notifications=notifications,
    )


def evaluate_fleet(
    clean: CleanData,
    config: AlertConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
    config_for: Callable[[str], AlertConfig] | None = None,
) -> FleetReport:
    """Evaluate every truck.

    `now` is the wall clock in live use. For a batch file it defaults to the latest
    timestamp in the data, so a truck counts as silent only relative to the rest of
    the fleet. `config_for(truck_id)` gives each truck its own cargo profile; without
    it (or for a truck it doesn't know) every truck uses `config`.
    """
    evaluated_at = now if now is not None else clean.latest_timestamp()
    trucks = [
        evaluate_truck(
            truck_id, readings, config_for(truck_id) if config_for else config, evaluated_at
        )
        for truck_id, readings in clean.by_truck.items()
    ]
    return FleetReport(evaluated_at=evaluated_at, trucks=trucks, clean=clean)
