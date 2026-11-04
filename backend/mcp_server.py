"""Read-only MCP server for asking about the fleet. Run: python -m backend.mcp_server"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

from backend.engine import load_raw_file
from backend.report import evaluate_raw, report_to_dict
from backend.settings import get_settings

MAX_HISTORY_MINUTES = 24 * 60

mcp = FastMCP("cold-chain-alerts")


def _api_url() -> str:
    # Set when the dashboard's Ask box starts this server, so answers match the live data.
    return os.environ.get("COLD_CHAIN_API_URL", "").rstrip("/")


def _local_report():
    return evaluate_raw(load_raw_file(get_settings().readings_path))


def _report_dict() -> dict[str, Any]:
    if base := _api_url():
        return httpx.get(f"{base}/api/report", timeout=5).json()
    return report_to_dict(_local_report())


def _truck_readings(truck_id: str) -> list[dict[str, Any]]:
    if base := _api_url():
        r = httpx.get(f"{base}/api/trucks/{quote(truck_id, safe='')}", timeout=5)
        r.raise_for_status()
        return [
            {
                "at": x["timestamp"],
                "temperature_c": x["temperature_c"],
                "humidity_pct": x["humidity_pct"],
            }
            for x in r.json()["readings"]
        ]
    return [
        {
            "at": x.timestamp.isoformat(),
            "temperature_c": x.temperature_c,
            "humidity_pct": x.humidity_pct,
        }
        for x in _local_report().clean.by_truck.get(truck_id, [])
    ]


def fleet_status() -> dict[str, Any]:
    data = _report_dict()
    return {
        "evaluated_at": data["evaluated_at"],
        "states_explained": {
            "ok": "inside its safe range",
            "suspect": "out of range, under the alert time so far (no alert yet)",
            "alert": "out of range past the alert time, alert open",
            "recovering": "alert open, back in range, waiting 10 min to confirm",
        },
        "trucks": [{k: v for k, v in t.items() if k != "short_episodes"} for t in data["trucks"]],
    }


def open_alerts() -> list[dict[str, Any]]:
    return [a for a in _report_dict()["alerts"] if a["status"] != "closed"]


def all_alerts() -> list[dict[str, Any]]:
    return _report_dict()["alerts"]


def truck_history(truck_id: str, minutes: int = 60) -> dict[str, Any]:
    if not isinstance(minutes, int) or not 1 <= minutes <= MAX_HISTORY_MINUTES:
        raise ValueError(f"minutes must be between 1 and {MAX_HISTORY_MINUTES}")
    data = _report_dict()
    truck = next((t for t in data["trucks"] if t["truck_id"] == truck_id), None)
    if truck is None:
        known = ", ".join(t["truck_id"] for t in data["trucks"])
        raise ValueError(f"unknown truck; known trucks: {known}")
    readings = _truck_readings(truck_id)
    if readings:
        since = datetime.fromisoformat(readings[-1]["at"]) - timedelta(minutes=minutes)
        readings = [r for r in readings if datetime.fromisoformat(r["at"]) >= since]
    return {
        **truck,
        "readings": readings,
        "alerts": [a for a in data["alerts"] if a["truck_id"] == truck_id],
    }


def alert_details(alert_id: str) -> dict[str, Any]:
    for a in _report_dict()["alerts"]:
        if a["alert_id"] == alert_id:
            return a
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
