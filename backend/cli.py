"""Command line: "here's what I'd alert on, and why".

    .\\.venv\\Scripts\\python -m backend.cli                       # seed data
    .\\.venv\\Scripts\\python -m backend.cli path\\to\\readings.json --json
    .\\.venv\\Scripts\\python -m backend.cli --alert-after-min 25   # try another limit
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from backend.engine import DEFAULT_CONFIG, AlertConfig, FleetReport, load_raw_file
from backend.engine.loader import parse_timestamp
from backend.report import evaluate_raw, report_to_dict

SEED_PATH = Path(__file__).resolve().parent.parent / "starter_kit" / "data" / "sensor_readings.json"


def _t(ts: datetime | None) -> str:
    return ts.strftime("%H:%M") if ts else "-"


def _why_no_alert(truck, config: AlertConfig) -> str:
    episodes = truck.short_episodes
    if not episodes:
        return f"never outside {config.min_safe_c:g}–{config.max_safe_c:g} °C"
    longest = max(episodes, key=lambda e: e.duration_min)
    if longest.reading_count == 1:
        what = f"one reading out of range ({longest.peak_c:.1f} °C at {_t(longest.started_at)})"
    else:
        what = (
            f"out of range at most {longest.duration_min:.0f} min "
            f"({_t(longest.started_at)}–{_t(longest.last_out_at)}, peak {longest.peak_c:.1f} °C)"
        )
    text = f"{what}, under the {config.alert_after_min:g}-min limit"
    if len(episodes) > 1:
        text += f" ({len(episodes)} short episodes)"
    if longest.ongoing:
        text += "; still out of range when the data ends (watching)"
    return text


def format_report(report: FleetReport, config: AlertConfig) -> str:
    clean = report.clean
    evaluated = report.evaluated_at.strftime("%Y-%m-%d %H:%M UTC") if report.evaluated_at else "-"
    lines = [
        "Cold-chain alert report",
        f"Evaluated at {evaluated} · safe range {config.min_safe_c:g}–{config.max_safe_c:g} °C · "
        f"alert after {config.alert_after_min:g} min out of range",
        f"Data: {clean.total_input} readings, {clean.accepted_count} used, "
        f"{len(clean.rejected)} rejected, {len(clean.duplicates)} duplicates, "
        f"{len(clean.faults)} sensor faults, {len(report.trucks)} trucks",
        "",
    ]

    alerts = report.alerts
    lines.append(f"ALERTS ({len(alerts)})")
    if not alerts:
        lines.append("  none")
    for a in alerts:
        status = {
            "open": "OPEN",
            "recovering": "RECOVERING (back in range, not yet confirmed)",
            "closed": f"CLOSED at {_t(a.closed_at)}",
        }[a.status.value]
        lines.append(f"  [{a.severity.upper()}] {a.truck_id}  {a.reason()}")
        detail = f"      alert fired at {_t(a.opened_at)} · {status}"
        if a.updates_sent:
            detail += f" · {a.updates_sent} not-improving update(s)"
        if a.escalated_at:
            detail += f" · Critical since {_t(a.escalated_at)}"
        lines.append(detail)
    lines.append("")

    quiet = [t for t in report.trucks if not t.alerts]
    lines.append(f"NO ALERT ({len(quiet)})")
    for t in quiet:
        lines.append(f"  {t.truck_id}  {_why_no_alert(t, config)}")
    lines.append("")

    lines.append("TRUCK STATUS NOW")
    for t in report.trucks:
        last = t.last_reading
        reading = f"{last.temperature_c:.1f} °C at {_t(last.timestamp)}" if last else "no data"
        silent = "  SENSOR SILENT" if t.silent_now else ""
        lines.append(f"  {t.truck_id}  {t.state.value.upper():<10}  last {reading}{silent}")
    lines.append("")

    if report.silences:
        lines.append(f"SENSOR SILENCES ({len(report.silences)})")
        for s in report.silences:
            until = _t(s.resumed_at) if s.resumed_at else "still silent"
            span = f"{_t(s.last_reading_at)}–{until}"
            lines.append(f"  {s.truck_id}  no data {span} ({s.minutes:.0f} min)")
        lines.append("")

    if clean.rejected or clean.faults:
        lines.append("DATA PROBLEMS")
        for r in clean.rejected:
            lines.append(f"  reading #{r.index} rejected: {r.reason}")
        for f in clean.faults:
            temp = f"{f.temperature_c:g} °C"
            lines.append(f"  {f.truck_id} at {_t(f.timestamp)}: {temp} ignored (sensor fault)")
        lines.append("")

    notes = report.notifications
    lines.append(f"NOTIFICATIONS THAT WOULD BE SENT ({len(notes)})")
    if not notes:
        lines.append("  none")
    for n in notes:
        lines.append(f"  {_t(n.at)}  {n.message}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli",
        description="Evaluate reefer sensor readings and list sustained temperature alerts.",
    )
    parser.add_argument("path", nargs="?", default=str(SEED_PATH), help="readings JSON file")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--now", help="evaluation time, ISO 8601 (default: latest reading)")
    parser.add_argument("--alert-after-min", type=float, help="override the 20-min alert limit")
    parser.add_argument("--max-safe-c", type=float, help="override the 8 °C upper limit")
    parser.add_argument("--min-safe-c", type=float, help="override the 2 °C lower limit")
    parser.add_argument(
        "--send",
        action="store_true",
        help="deliver the notifications (webhook if ALERT_WEBHOOK_URL is set, else console)",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="add AI-written summaries (LLM_PROVIDER in .env; falls back to the plain message)",
    )
    return parser


def _load_settings():
    from pydantic import ValidationError

    from backend.settings import Settings

    try:
        return Settings()
    except ValidationError as exc:
        # Pydantic echoes bad values, and some settings are secrets: print only why.
        reasons = "; ".join(e["msg"] for e in exc.errors(include_input=False))
        print(f"error: bad settings in .env: {reasons}", file=sys.stderr)
        return None


def ai_summaries(report: FleetReport, settings):
    """Return the summarizer and print each notification's AI summary."""
    from backend.ai import summarizer_for

    summarize = summarizer_for(report, settings)
    print(f"\nAI SUMMARIES (provider: {settings.llm_provider})")
    for n in report.notifications:
        summary = summarize(n)
        print(f"  {n.notification_id}")
        print(f"    {summary if summary else '(plain message used)'}")
    return summarize


def send_notifications(report: FleetReport, settings, summarize=None) -> int:
    from backend.notify import InMemorySentStore, dispatch, sender_from_settings

    sender = sender_from_settings(settings)
    results = dispatch(report.notifications, sender, InMemorySentStore(), summarize)
    sent = sum(r.status == "sent" for r in results)
    failed = [r for r in results if r.status == "failed"]
    print(f"\nDelivered {sent} of {len(results)} notifications via {sender.name}.")
    for r in failed:
        print(f"  not delivered: {r.notification_id}: {r.detail}", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if sys.stdout is sys.__stdout__ and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # "°C" and "–" on Windows consoles

    overrides = {
        name: value
        for name, value in (
            ("alert_after_min", args.alert_after_min),
            ("max_safe_c", args.max_safe_c),
            ("min_safe_c", args.min_safe_c),
        )
        if value is not None
    }
    try:
        config = replace(DEFAULT_CONFIG, **overrides)
        now = parse_timestamp(args.now) if args.now else None
        raw = load_raw_file(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = evaluate_raw(raw, config, now)
    if args.json:
        print(json.dumps(report_to_dict(report, config), indent=2, ensure_ascii=False))
    else:
        print(format_report(report, config))
    if not (args.send or args.ai):
        return 0
    settings = _load_settings()
    if settings is None:
        return 2
    summarize = ai_summaries(report, settings) if args.ai else None
    if args.send:
        return send_notifications(report, settings, summarize)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
