"""The only data the LLM ever sees: numbers about one alert. No customer or cargo details."""
from __future__ import annotations

from typing import Any

from backend.engine import FleetReport
from backend.engine.models import Alert, Notification, NotificationKind, Reading, hhmm

SUMMARIZED_KINDS = {
    NotificationKind.ALERT_OPENED,
    NotificationKind.ALERT_NOT_IMPROVING,
    NotificationKind.ALERT_ESCALATED,
}


def _trend(first: float, last: float) -> str:
    if last - first >= 0.3:
        return "rising"
    if first - last >= 0.3:
        return "falling"
    return "flat"


def alert_facts(n: Notification, alert: Alert, readings: list[Reading]) -> dict[str, Any]:
    """Facts as they stood when the notification was raised (not later readings)."""
    window = [r for r in readings if alert.started_at <= r.timestamp <= n.at]
    temps = [r.temperature_c for r in window]
    hums = [r.humidity_pct for r in window if r.humidity_pct is not None]
    gaps = [g for g in alert.gaps if g.end <= n.at]
    facts: dict[str, Any] = {
        "truck_id": n.truck_id,
        "event": n.kind.value,
        "severity": n.severity.value,
        "direction": alert.direction,
        "limit_c": alert.limit_c,
        "out_of_range_since": hhmm(alert.started_at),
        "now": hhmm(n.at),
        "minutes_out_of_range": round((n.at - alert.started_at).total_seconds() / 60),
        "first_temp_c": temps[0] if temps else None,
        "latest_temp_c": temps[-1] if temps else None,
        "peak_temp_c": max(temps) if alert.direction == "above" else min(temps),
        "temperature_trend": _trend(temps[0], temps[-1]) if len(temps) > 1 else "flat",
        "missing_data": [
            {"from": hhmm(g.start), "to": hhmm(g.end), "minutes": round(g.minutes)} for g in gaps
        ],
    }
    if len(hums) > 1:
        facts["humidity_start_pct"] = hums[0]
        facts["humidity_latest_pct"] = hums[-1]
    return facts


def facts_by_notification(report: FleetReport) -> dict[str, dict[str, Any]]:
    alerts = {a.alert_id: a for a in report.alerts}
    out: dict[str, dict[str, Any]] = {}
    for n in report.notifications:
        alert = alerts.get(n.alert_id or "")
        if n.kind in SUMMARIZED_KINDS and alert is not None:
            readings = report.clean.by_truck.get(n.truck_id, [])
            out[n.notification_id] = alert_facts(n, alert, readings)
    return out
