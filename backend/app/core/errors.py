"""One error shape for every failure:

{"error": {"code": "...", "message": "...", "request_id": "...", "details": ...}}
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.llm.base import (
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)

log = structlog.get_logger(__name__)


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(
    request: Request | None, status_code: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = jsonable_encoder(details)
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(status_code=status_code, content={"error": body}, headers=headers)


def _llm_status(exc: LLMError) -> int:
    if isinstance(exc, LLMConfigError):
        return 503
    if isinstance(exc, LLMRateLimitError):
        return 429
    if isinstance(exc, LLMTimeoutError):
        return 504
    return 502


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(LLMError)
    async def _llm_error(request: Request, exc: LLMError) -> JSONResponse:
        log.warning("llm_error", provider=exc.provider, code=exc.code, message=exc.message)
        return error_response(
            request, _llm_status(exc), exc.code, exc.message, {"provider": exc.provider}
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "validation_error", "Invalid request.", exc.errors())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        return error_response(request, 500, "internal_error", "An unexpected error occurred.")
