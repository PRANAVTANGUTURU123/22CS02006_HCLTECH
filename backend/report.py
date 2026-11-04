"""Turn engine results into plain JSON-ready dicts.

Shared by the CLI (--json), the API and the MCP server so they always agree.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from backend.engine import (
    DEFAULT_CONFIG,
    AlertConfig,
    FleetReport,
    TruckResult,
    clean_readings,
    evaluate_fleet,
)
from backend.engine.models import Alert, Notification, ShortEpisode, Silence


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def alert_to_dict(a: Alert) -> dict[str, Any]:
    return {
        "alert_id": a.alert_id,
        "truck_id": a.truck_id,
        "severity": a.severity.value,
        "status": a.status.value,
        "direction": a.direction,
        "limit_c": a.limit_c,
        "started_at": _iso(a.started_at),
        "opened_at": _iso(a.opened_at),
        "last_out_at": _iso(a.last_out_at),
        "closed_at": _iso(a.closed_at),
        "escalated_at": _iso(a.escalated_at),
        "duration_min": round(a.duration_min, 1),
        "peak_c": a.peak_c,
        "reading_count": a.reading_count,
        "updates_sent": a.updates_sent,
        "missing_data": [
            {"from": _iso(g.start), "to": _iso(g.end), "minutes": round(g.minutes, 1)}
            for g in a.gaps
        ],
        "reason": a.reason(),
    }


def short_episode_to_dict(e: ShortEpisode) -> dict[str, Any]:
    return {
        "started_at": _iso(e.started_at),
        "last_out_at": _iso(e.last_out_at),
        "duration_min": round(e.duration_min, 1),
        "peak_c": e.peak_c,
        "reading_count": e.reading_count,
        "ongoing": e.ongoing,
    }


def silence_to_dict(s: Silence) -> dict[str, Any]:
    return {
        "silence_id": s.silence_id,
        "truck_id": s.truck_id,
        "last_reading_at": _iso(s.last_reading_at),
        "resumed_at": _iso(s.resumed_at),
        "minutes": round(s.minutes, 1),
        "was_out_of_range": s.was_out_of_range,
        "notify": s.notify,
    }


def notification_to_dict(n: Notification) -> dict[str, Any]:
    return {
        "notification_id": n.notification_id,
        "kind": n.kind.value,
        "truck_id": n.truck_id,
        "at": _iso(n.at),
        "severity": n.severity.value,
        "message": n.message,
        "alert_id": n.alert_id,
    }


def truck_to_dict(t: TruckResult) -> dict[str, Any]:
    last = t.last_reading
    open_alert = t.open_alert
    return {
        "truck_id": t.truck_id,
        "state": t.state.value,
        "silent": t.silent_now,
        "last_reading_at": _iso(last.timestamp) if last else None,
        "last_temperature_c": last.temperature_c if last else None,
        "last_humidity_pct": last.humidity_pct if last else None,
        "open_alert_id": open_alert.alert_id if open_alert else None,
        "profile": t.config.profile,
        "min_safe_c": t.config.min_safe_c,
        "max_safe_c": t.config.max_safe_c,
        "alert_after_min": t.config.alert_after_min,
        "alert_count": len(t.alerts),
        "short_episodes": [short_episode_to_dict(e) for e in t.short_episodes],
    }


def config_to_dict(config: AlertConfig) -> dict[str, Any]:
    return {name: getattr(config, name) for name in config.__dataclass_fields__}


def report_to_dict(report: FleetReport, config: AlertConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    clean = report.clean
    return {
        "evaluated_at": _iso(report.evaluated_at),
        "config": config_to_dict(config),
        "data_quality": {
            "input_readings": clean.total_input,
            "accepted": clean.accepted_count,
            "rejected": [{"index": r.index, "reason": r.reason} for r in clean.rejected],
            "duplicates": len(clean.duplicates),
            "sensor_faults": [
                {"truck_id": r.truck_id, "at": _iso(r.timestamp), "temperature_c": r.temperature_c}
                for r in clean.faults
            ],
        },
        "trucks": [truck_to_dict(t) for t in report.trucks],
        "alerts": [alert_to_dict(a) for a in report.alerts],
        "silences": [silence_to_dict(s) for s in report.silences],
        "notifications": [notification_to_dict(n) for n in report.notifications],
    }


def evaluate_raw(
    raw_readings: list[Any],
    config: AlertConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
    config_for: Callable[[str], AlertConfig] | None = None,
) -> FleetReport:
    """Clean, sort and evaluate raw readings in one step."""
    return evaluate_fleet(clean_readings(raw_readings, config), config, now, config_for)
