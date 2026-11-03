"""FastAPI entry point. Serves the API and, if it has been built, the React app from frontend/dist.

Run locally:   python -m uvicorn backend.app.main:app --reload --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import sessionmaker

from backend.app import __version__
from backend.app.api.routes import router
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, register_error_handlers
from backend.app.core.logging import configure_logging
from backend.app.core.middleware import register_middleware
from backend.app.db.session import create_db_engine, init_db
from backend.app.llm.factory import make_http_client

log = structlog.get_logger(__name__)

API_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine(settings.database_url)
        init_db(engine)
        app.state.engine = engine
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        app.state.http_client = make_http_client(settings)
        log.info(
            "startup",
            environment=settings.environment,
            llm_provider=settings.llm_provider,
            llm_fallback=settings.llm_fallback_provider,
            frontend_built=(Path(settings.frontend_dist) / "index.html").is_file(),
        )
        try:
            yield
        finally:
            await app.state.http_client.aclose()
            engine.dispose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    register_middleware(app, settings.request_timeout_seconds)
    register_error_handlers(app)
    app.include_router(router)
    _mount_frontend(app, Path(settings.frontend_dist))
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve built files from dist/, falling back to index.html so client-side routes work."""
    dist = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith(API_PREFIXES):
            raise AppError(404, "not_found", f"No route for /{full_path}")
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse(
                {
                    "message": "API is running. Frontend not built yet: "
                    "run `npm run build` in frontend/.",
                    "health": "/health",
                    "docs": "/docs",
                }
            )
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
