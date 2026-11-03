"""Request ID, access logging and a whole-request timeout."""

import asyncio
import time
import uuid

import structlog
from fastapi import FastAPI, Request
from starlette.responses import Response

from backend.app.core.errors import error_response

log = structlog.get_logger("http")


def register_middleware(app: FastAPI, request_timeout_seconds: float) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        try:
            response = await asyncio.wait_for(call_next(request), timeout=request_timeout_seconds)
        except TimeoutError:
            response = error_response(
                request,
                504,
                "request_timeout",
                f"Request took longer than {request_timeout_seconds:g}s.",
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
