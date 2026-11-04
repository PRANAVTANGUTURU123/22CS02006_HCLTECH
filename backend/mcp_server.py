"""Read-only MCP server: lets an AI assistant answer "which trucks are in trouble right now?".

Run by an MCP client (Claude Code via .mcp.json, Claude Desktop, MCP Inspector):
    .venv\\Scripts\\python -m backend.mcp_server

Every answer comes from the same engine as the CLI and API. No tool can change, close or
silence an alert. Inputs are checked: known truck ids only, history window capped.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from backend.engine import load_raw_file
from backend.report import alert_to_dict, evaluate_raw, truck_to_dict
from backend.settings import get_settings

MAX_HISTORY_MINUTES = 24 * 60

mcp = FastMCP("cold-chain-alerts")


def _report():
    return evaluate_raw(load_raw_file(get_settings().readings_path))


def fleet_status() -> dict[str, Any]:
    report = _report()
    return {
        "evaluated_at": report.evaluated_at.isoformat() if report.evaluated_at else None,
        "states_explained": {
            "ok": "in the 2-8 °C range",
            "suspect": "out of range, under 20 min so far (no alert yet)",
            "alert": "out of range 20+ min, alert open",
            "recovering": "alert open, back in range, waiting 10 min to confirm",
        },
        "trucks": [
            {k: v for k, v in truck_to_dict(t).items() if k != "short_episodes"}
            for t in report.trucks
        ],
    }


def open_alerts() -> list[dict[str, Any]]:
    return [alert_to_dict(a) for a in _report().alerts if a.status.value != "closed"]


def all_alerts() -> list[dict[str, Any]]:
    return [alert_to_dict(a) for a in _report().alerts]


def truck_history(truck_id: str, minutes: int = 60) -> dict[str, Any]:
    if not isinstance(minutes, int) or not 1 <= minutes <= MAX_HISTORY_MINUTES:
        raise ValueError(f"minutes must be between 1 and {MAX_HISTORY_MINUTES}")
    report = _report()
    truck = next((t for t in report.trucks if t.truck_id == truck_id), None)
    if truck is None:
        known = ", ".join(t.truck_id for t in report.trucks)
        raise ValueError(f"unknown truck; known trucks: {known}")
    readings = report.clean.by_truck.get(truck_id, [])
    since = readings[-1].timestamp - timedelta(minutes=minutes) if readings else None
    return {
        **truck_to_dict(truck),
        "readings": [
            {
                "at": r.timestamp.isoformat(),
                "temperature_c": r.temperature_c,
                "humidity_pct": r.humidity_pct,
            }
            for r in readings
            if since is None or r.timestamp >= since
        ],
        "alerts": [alert_to_dict(a) for a in truck.alerts],
    }


def alert_details(alert_id: str) -> dict[str, Any]:
    for a in _report().alerts:
        if a.alert_id == alert_id:
            return alert_to_dict(a)
    raise ValueError("unknown alert id; call get_all_alerts to list them")


@mcp.tool()
def get_fleet_status() -> dict[str, Any]:
    """Current state of every truck (ok / suspect / alert / recovering), last temperature,
    and whether its sensor has gone silent. Start here for "which trucks are in trouble?"."""
    return fleet_status()


@mcp.tool()
def get_open_alerts() -> list[dict[str, Any]]:
    """Alerts that are still open or recovering, with severity, duration, peak and reason."""
    return open_alerts()


@mcp.tool()
def get_all_alerts() -> list[dict[str, Any]]:
    """Every alert in the current data, including closed ones."""
    return all_alerts()


@mcp.tool()
def get_truck_history(truck_id: str, minutes: int = 60) -> dict[str, Any]:
    """Recent readings and alerts for one truck, e.g. "T-04". `minutes` is 1-1440."""
    return truck_history(truck_id, minutes)


@mcp.tool()
def get_alert(alert_id: str) -> dict[str, Any]:
    """Full details of one alert, including missing-data gaps."""
    return alert_details(alert_id)


if __name__ == "__main__":
    mcp.run()  # stdio
