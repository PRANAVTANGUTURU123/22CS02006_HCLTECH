import json

from backend.cli import main


def run_cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_text_report_on_seed_data(capsys):
    code, out, _ = run_cli(capsys)
    assert code == 0
    assert "ALERTS (3)" in out
    assert "[WARNING] T-01  Above 8 °C for 30 min (08:10–08:40), peak 9.6 °C." in out
    assert "Includes missing data: 08:30–08:40 (10 min)." in out
    assert "RECOVERING (back in range, not yet confirmed)" in out
    assert "T-02  one reading out of range (8.9 °C at 08:10), under the 20-min limit" in out
    assert "T-03  never outside 2–8 °C" in out
    assert "NOTIFICATIONS THAT WOULD BE SENT (3)" in out


def test_json_report_on_seed_data(capsys):
    code, out, _ = run_cli(capsys, "--json")
    assert code == 0
    data = json.loads(out)
    assert [a["truck_id"] for a in data["alerts"]] == ["T-01", "T-05", "T-04"]
    t04 = data["alerts"][2]
    assert t04["duration_min"] == 20
    assert t04["missing_data"][0]["minutes"] == 10
    assert data["data_quality"]["accepted"] == 54
    assert data["config"]["alert_after_min"] == 20


def test_limit_override(capsys):
    _, out, _ = run_cli(capsys, "--alert-after-min", "25")
    assert "ALERTS (2)" in out
    assert "T-04  out of range at most 20 min (08:25–08:45, peak 9.1 °C), under the 25-min limit (2 short episodes)" in out


def test_now_override_shows_silent_sensors(capsys):
    _, out, _ = run_cli(capsys, "--now", "2026-07-20T09:30:00")
    status_lines = [line for line in out.splitlines() if line.endswith("  SENSOR SILENT")]
    assert len(status_lines) == 5
    assert "T-05 has sent no data for 30+ min (last reading 7.0 °C at 08:50)" in out
    assert "NOTIFICATIONS THAT WOULD BE SENT (8)" in out  # 3 alerts + 5 silences


def test_not_improving_updates_are_shown(tmp_path, capsys):
    path = tmp_path / "stuck.json"
    readings = [
        {"truck_id": "T-09", "timestamp": f"2026-07-20T08:{m:02d}:00", "temperature_c": 9.5}
        for m in range(0, 45, 5)
    ]
    path.write_text(json.dumps(readings), encoding="utf-8")
    _, out, _ = run_cli(capsys, str(path))
    assert "2 not-improving update(s)" in out
    assert "08:30  UPDATE: Truck T-09 still above 8 °C after 30 min and not improving" in out


def test_data_problems_are_listed(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps(
            [
                {"truck_id": "T-09", "timestamp": "2026-07-20T08:00:00", "temperature_c": 5},
                {"truck_id": "T-09", "timestamp": "2026-07-20T08:05:00", "temperature_c": "hot"},
                {"truck_id": "T-09", "timestamp": "2026-07-20T08:10:00", "temperature_c": 85},
            ]
        ),
        encoding="utf-8",
    )
    code, out, _ = run_cli(capsys, str(path))
    assert code == 0
    assert "reading #1 rejected: temperature_c is missing or not a number" in out
    assert "T-09 at 08:10: 85 °C ignored (sensor fault)" in out


def test_missing_file_is_a_clean_error(capsys):
    code, out, err = run_cli(capsys, "does-not-exist.json")
    assert code == 2
    assert err.startswith("error:")
    assert out == ""


def test_bad_now_and_bad_config_are_clean_errors(capsys):
    assert run_cli(capsys, "--now", "soon")[0] == 2
    assert run_cli(capsys, "--min-safe-c", "9")[0] == 2
