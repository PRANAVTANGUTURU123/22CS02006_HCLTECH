import asyncio

import pytest

from backend import mcp_server as srv


def test_fleet_status_answers_which_trucks_are_in_trouble():
    status = srv.fleet_status()
    states = {t["truck_id"]: t["state"] for t in status["trucks"]}
    assert states == {"T-01": "ok", "T-02": "ok", "T-03": "ok", "T-04": "recovering", "T-05": "ok"}
    assert status["evaluated_at"] == "2026-07-20T08:50:00+00:00"


def test_open_alerts_only_t04():
    [alert] = srv.open_alerts()
    assert alert["truck_id"] == "T-04"
    assert "Includes missing data: 08:30–08:40" in alert["reason"]
    assert len(srv.all_alerts()) == 3


def test_truck_history_window():
    full = srv.truck_history("T-04", 60)
    assert len(full["readings"]) == 10
    recent = srv.truck_history("T-04", 10)
    assert [r["at"][11:16] for r in recent["readings"]] == ["08:40", "08:45", "08:50"]


@pytest.mark.parametrize("minutes", [0, -5, 1441])
def test_history_window_is_capped(minutes):
    with pytest.raises(ValueError, match="between 1 and 1440"):
        srv.truck_history("T-04", minutes)


def test_unknown_truck_and_alert_are_refused():
    with pytest.raises(ValueError, match="unknown truck"):
        srv.truck_history("'; DROP TABLE trucks; --")
    with pytest.raises(ValueError, match="unknown alert"):
        srv.alert_details("nope")


def test_alert_details():
    alert_id = srv.open_alerts()[0]["alert_id"]
    assert srv.alert_details(alert_id)["peak_c"] == 9.1


def test_server_exposes_exactly_the_read_only_tools():
    tools = asyncio.run(srv.mcp.list_tools())
    assert sorted(t.name for t in tools) == [
        "get_alert",
        "get_all_alerts",
        "get_fleet_status",
        "get_open_alerts",
        "get_truck_history",
    ]
    assert all(t.description for t in tools)


def test_tool_call_through_mcp():
    result = asyncio.run(srv.mcp.call_tool("get_open_alerts", {}))
    assert "T-04" in str(result)
