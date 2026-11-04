"""HTTP API over the engine. The server entry point is backend/main.py."""
from __future__ import annotations

import logging
import threading
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ai import Summarizer, facts_by_notification, llm_from_settings
from backend.engine import DEFAULT_CONFIG, AlertConfig, FleetReport, load_raw_file
from backend.notify import Sender, SentStore, dispatch, sender_from_settings
from backend.report import (
    alert_to_dict,
    evaluate_raw,
    notification_to_dict,
    report_to_dict,
    truck_to_dict,
)
from backend.settings import Settings, get_settings
from backend.store import SqlSentStore, make_engine

log = logging.getLogger(__name__)


class ReadingsUpload(BaseModel):
    # Each reading is validated by the engine's loader, which rejects bad ones one by one
    # with a reason instead of failing the whole batch.
    readings: list[dict[str, Any]] = Field(min_length=1)
    replace: bool = False  # true = start over with only these readings


class Fleet:
    """Current readings, the latest report and AI summaries. Thread-safe."""

    def __init__(self, raw: list[Any], config: AlertConfig, summarizer_llm) -> None:
        self._lock = threading.Lock()
        self._config = config
        self._llm = summarizer_llm
        self._summarizer: Summarizer | None = None
        self.raw: list[Any] = []
        self.report: FleetReport
        self.summaries: dict[str, str | None] = {}
        self.replace(raw)

    def _refresh(self) -> None:
        self.report = evaluate_raw(self.raw, self._config)
        facts = facts_by_notification(self.report)
        old_cache = self._summarizer._cache if self._summarizer else {}
        self._summarizer = Summarizer(self._llm, facts)
        self._summarizer._cache.update(old_cache)  # don't re-ask the LLM for old alerts
        self.summaries = {n.notification_id: self._summarizer(n) for n in self.report.notifications}

    def replace(self, raw: list[Any]) -> None:
        with self._lock:
            self.raw = list(raw)
            self._refresh()

    def add(self, raw: list[Any]) -> None:
        with self._lock:
            self.raw.extend(raw)
            self._refresh()

    def summary_for(self, notification) -> str | None:
        return self.summaries.get(notification.notification_id)


def create_app(
    settings: Settings | None = None,
    sender: Sender | None = None,
    store: SentStore | None = None,
    llm=None,
) -> FastAPI:
    settings = settings or get_settings()
    sender = sender or sender_from_settings(settings)
    store = store or SqlSentStore(make_engine(settings.database_url))
    llm = llm or llm_from_settings(settings)
    fleet = Fleet(load_raw_file(settings.readings_path), DEFAULT_CONFIG, llm)

    app = FastAPI(title="Cold-Chain Alerting", version="1.0.0")
    app.state.fleet = fleet
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"]
        )

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/report")
    def report() -> dict[str, Any]:
        data = report_to_dict(fleet.report)
        for n in data["notifications"]:
            n["ai_summary"] = fleet.summaries.get(n["notification_id"])
        return data

    @app.get("/api/trucks")
    def trucks() -> list[dict[str, Any]]:
        return [truck_to_dict(t) for t in fleet.report.trucks]

    @app.get("/api/trucks/{truck_id}")
    def truck(truck_id: str) -> dict[str, Any]:
        found = next((t for t in fleet.report.trucks if t.truck_id == truck_id), None)
        if found is None:
            raise HTTPException(404, "unknown truck")
        readings = fleet.report.clean.by_truck.get(truck_id, [])
        return {
            **truck_to_dict(found),
            "readings": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "temperature_c": r.temperature_c,
                    "humidity_pct": r.humidity_pct,
                }
                for r in readings
            ],
            "alerts": [alert_to_dict(a) for a in found.alerts],
        }

    @app.get("/api/alerts")
    def alerts(status: Literal["open", "recovering", "closed"] | None = None) -> list[dict]:
        items = [alert_to_dict(a) for a in fleet.report.alerts]
        return [a for a in items if status is None or a["status"] == status]

    @app.get("/api/notifications")
    def notifications() -> list[dict[str, Any]]:
        out = []
        for n in fleet.report.notifications:
            item = notification_to_dict(n)
            item["ai_summary"] = fleet.summary_for(n)
            item["sent"] = store.was_sent(n.notification_id)
            out.append(item)
        return out

    @app.post("/api/readings")
    def upload(body: ReadingsUpload) -> dict[str, Any]:
        if len(body.readings) > settings.max_upload_readings:
            raise HTTPException(413, f"at most {settings.max_upload_readings} readings per upload")
        if body.replace:
            fleet.replace(body.readings)
        else:
            fleet.add(body.readings)
        results = dispatch(fleet.report.notifications, sender, store, fleet.summary_for)
        clean = fleet.report.clean
        return {
            "accepted": clean.accepted_count,
            "rejected": [{"index": r.index, "reason": r.reason} for r in clean.rejected],
            "duplicates": len(clean.duplicates),
            "sensor_faults": len(clean.faults),
            "alerts": len(fleet.report.alerts),
            "sent": [r.notification_id for r in results if r.status == "sent"],
            "failed": [r.notification_id for r in results if r.status == "failed"],
        }

    @app.post("/api/reset")
    def reset() -> dict[str, int]:
        """Reload the seed data (for demos). Sends nothing."""
        fleet.replace(load_raw_file(settings.readings_path))
        return {"readings": len(fleet.raw)}

    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="ui")

    return app
