"""HTTP API over the engine. The server entry point is backend/main.py."""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ai import (
    Summarizer,
    chat_llm_from_settings,
    facts_by_notification,
    llm_from_settings,
)
from backend.ai.ask import Answer, ask_via_mcp
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
from backend.trucks import TruckRange, TruckRegistry

log = logging.getLogger(__name__)


class TruckRangeIn(BaseModel):
    """A truck's own safe range. Both limits null = use the default range."""

    min_safe_c: float | None = Field(default=None, ge=-60, le=60)
    max_safe_c: float | None = Field(default=None, ge=-60, le=60)
    alert_after_min: float | None = Field(default=None, gt=0, le=1440)


class NewTruck(TruckRangeIn):
    truck_id: str = Field(min_length=1, max_length=64)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class ReadingsUpload(BaseModel):
    # validated one by one by the loader, so one bad reading doesn't fail the batch
    readings: list[dict[str, Any]] = Field(min_length=1)
    replace: bool = False  # true = start over with only these readings
    # ranges for new trucks; never overwrites one already set
    trucks: list[NewTruck] = Field(default_factory=list, max_length=1000)


class Fleet:
    """Current readings, the latest report and AI summaries. Thread-safe."""

    def __init__(
        self, raw: list[Any], config: AlertConfig, summarizer_llm, registry: TruckRegistry
    ) -> None:
        self._lock = threading.Lock()
        self._config = config
        self._registry = registry
        self._llm = summarizer_llm
        self._summarizer: Summarizer | None = None
        self.raw: list[Any] = []
        self.report: FleetReport
        self.summaries: dict[str, str | None] = {}
        self.replace(raw)

    def _refresh(self) -> None:
        self.report = evaluate_raw(self.raw, self._config, config_for=self._registry.config_for)
        self._registry.register(list(self.report.clean.by_truck))
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

    def refresh(self) -> None:
        """Re-evaluate after a range change."""
        with self._lock:
            self._refresh()

    def summary_for(self, notification) -> str | None:
        return self.summaries.get(notification.notification_id)


def create_app(
    settings: Settings | None = None,
    sender: Sender | None = None,
    store: SentStore | None = None,
    llm=None,
    registry: TruckRegistry | None = None,
    asker: Callable[[str, str | None], Answer] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    sender = sender or sender_from_settings(settings)
    db = (
        make_engine(settings.database_url.get_secret_value())
        if store is None or registry is None
        else None
    )
    store = store or SqlSentStore(db)
    registry = registry or TruckRegistry(db, DEFAULT_CONFIG)
    llm = llm or llm_from_settings(settings)
    fleet = Fleet(load_raw_file(settings.readings_path), DEFAULT_CONFIG, llm, registry)
    if asker is None:
        chat_llm = llm if hasattr(llm, "chat") else chat_llm_from_settings(settings)

        def asker(question: str, api_url: str | None) -> Answer:
            return asyncio.run(ask_via_mcp(question, chat_llm, api_url))

    ask_times: deque[float] = deque()

    app = FastAPI(title="Cold-Chain Alerting", version="1.0.0")
    app.state.fleet = fleet
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT"],
        )

    admin_key = (
        settings.admin_api_key.get_secret_value() if settings.admin_api_key is not None else ""
    )

    def require_key(x_api_key: str | None = Header(default=None)) -> None:
        if admin_key and not hmac.compare_digest(x_api_key or "", admin_key):
            raise HTTPException(401, "missing or wrong X-API-Key")

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

    @app.post("/api/readings", dependencies=[Depends(require_key)])
    def upload(body: ReadingsUpload) -> dict[str, Any]:
        if len(body.readings) > settings.max_upload_readings:
            raise HTTPException(413, f"at most {settings.max_upload_readings} readings per upload")
        ranges_set = []
        for t in body.trucks:
            existing = registry.get(t.truck_id)
            if existing is None or not (existing.is_custom or existing.alert_after_min):
                try:
                    registry.set_range(t.truck_id, t.min_safe_c, t.max_safe_c, t.alert_after_min)
                except ValueError as exc:
                    raise HTTPException(422, f"{t.truck_id}: {exc}") from None
                ranges_set.append(t.truck_id)
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
            "ranges_set": ranges_set,
        }

    def range_dict(row: TruckRange) -> dict[str, Any]:
        cfg = registry.config_for(row.truck_id)
        return {
            "truck_id": row.truck_id,
            "min_safe_c": row.min_safe_c,  # null = not set, default applies
            "max_safe_c": row.max_safe_c,
            "alert_after_min": row.alert_after_min,
            "custom": row.is_custom,
            "added_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
            "effective": {
                "min_safe_c": cfg.min_safe_c,
                "max_safe_c": cfg.max_safe_c,
                "alert_after_min": cfg.alert_after_min,
            },
        }

    @app.get("/api/ranges")
    def ranges() -> dict[str, Any]:
        d = registry.default
        return {
            "default": {
                "min_safe_c": d.min_safe_c,
                "max_safe_c": d.max_safe_c,
                "alert_after_min": d.alert_after_min,
            },
            "trucks": [range_dict(r) for r in registry.all()],
        }

    @app.put("/api/trucks/{truck_id}/range", dependencies=[Depends(require_key)])
    def put_range(truck_id: str, body: TruckRangeIn) -> dict[str, Any]:
        try:
            row = registry.set_range(
                truck_id, body.min_safe_c, body.max_safe_c, body.alert_after_min
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        fleet.refresh()
        results = dispatch(fleet.report.notifications, sender, store, fleet.summary_for)
        return {
            **range_dict(row),
            "sent": [r.notification_id for r in results if r.status == "sent"],
        }

    @app.post("/api/ask")
    def ask(body: AskIn, request: Request) -> dict[str, Any]:
        now = time.monotonic()
        while ask_times and now - ask_times[0] > 60:
            ask_times.popleft()
        if len(ask_times) >= settings.ask_per_minute:
            raise HTTPException(429, "too many questions, try again in a minute")
        ask_times.append(now)
        port = (request.scope.get("server") or (None, None))[1]
        api_url = f"http://127.0.0.1:{port}" if settings.ask_live_data and port else None
        answer = asker(body.question.strip(), api_url)
        return {"answer": answer.text, "ok": answer.ok, "tools_used": answer.tools_used}

    @app.post("/api/reset", dependencies=[Depends(require_key)])
    def reset() -> dict[str, int]:
        """Reload the seed data (for demos). Sends nothing."""
        fleet.replace(load_raw_file(settings.readings_path))
        return {"readings": len(fleet.raw)}

    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="ui")

    return app
