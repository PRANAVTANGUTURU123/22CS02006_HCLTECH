"""HTTP API over the engine. The server entry point is backend/main.py."""

from __future__ import annotations

import hmac
import logging
import threading
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ai import Summarizer, facts_by_notification, llm_from_settings
from backend.engine import DEFAULT_CONFIG, AlertConfig, FleetReport, load_raw_file
from backend.notify import Sender, SentStore, dispatch, sender_from_settings
from backend.profiles import ProfileStore
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


class ProfileIn(BaseModel):
    min_safe_c: float = Field(ge=-60, le=60)
    max_safe_c: float = Field(ge=-60, le=60)
    alert_after_min: float = Field(default=20, gt=0, le=1440)


class AssignIn(BaseModel):
    profile: str = Field(min_length=1, max_length=32)


class Fleet:
    """Current readings, the latest report and AI summaries. Thread-safe."""

    def __init__(
        self, raw: list[Any], config: AlertConfig, summarizer_llm, config_for=None
    ) -> None:
        self._lock = threading.Lock()
        self._config = config
        self._config_for = config_for
        self._llm = summarizer_llm
        self._summarizer: Summarizer | None = None
        self.raw: list[Any] = []
        self.report: FleetReport
        self.summaries: dict[str, str | None] = {}
        self.replace(raw)

    def _refresh(self) -> None:
        self.report = evaluate_raw(self.raw, self._config, config_for=self._config_for)
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
        """Re-evaluate after a profile change."""
        with self._lock:
            self._refresh()

    def summary_for(self, notification) -> str | None:
        return self.summaries.get(notification.notification_id)


def create_app(
    settings: Settings | None = None,
    sender: Sender | None = None,
    store: SentStore | None = None,
    llm=None,
    profiles: ProfileStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    sender = sender or sender_from_settings(settings)
    db = (
        make_engine(settings.database_url.get_secret_value())
        if store is None or profiles is None
        else None
    )
    store = store or SqlSentStore(db)
    profiles = profiles or ProfileStore(db, settings.default_profile)
    llm = llm or llm_from_settings(settings)
    fleet = Fleet(load_raw_file(settings.readings_path), DEFAULT_CONFIG, llm, profiles.config_for)

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

    def profiles_view() -> dict[str, Any]:
        return {
            "default": profiles.default_profile,
            "profiles": [
                {
                    "name": name,
                    "min_safe_c": cfg.min_safe_c,
                    "max_safe_c": cfg.max_safe_c,
                    "alert_after_min": cfg.alert_after_min,
                }
                for name, cfg in sorted(profiles.profiles().items())
            ],
            "assignments": profiles.assignments(),
        }

    @app.get("/api/profiles")
    def get_profiles() -> dict[str, Any]:
        return profiles_view()

    @app.put("/api/profiles/{name}", dependencies=[Depends(require_key)])
    def put_profile(name: str, body: ProfileIn) -> dict[str, Any]:
        try:
            profiles.upsert_profile(name, body.min_safe_c, body.max_safe_c, body.alert_after_min)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        fleet.refresh()
        return profiles_view()

    @app.put("/api/trucks/{truck_id}/profile", dependencies=[Depends(require_key)])
    def put_truck_profile(truck_id: str, body: AssignIn) -> dict[str, Any]:
        try:
            profiles.assign(truck_id, body.profile)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        fleet.refresh()
        return profiles_view()

    @app.post("/api/reset", dependencies=[Depends(require_key)])
    def reset() -> dict[str, int]:
        """Reload the seed data (for demos). Sends nothing."""
        fleet.replace(load_raw_file(settings.readings_path))
        return {"readings": len(fleet.raw)}

    if settings.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="ui")

    return app
