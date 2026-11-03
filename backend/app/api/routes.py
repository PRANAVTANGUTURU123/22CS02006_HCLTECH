from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app import __version__
from backend.app.db.session import check_db
from backend.app.llm.factory import LLMService

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    llm_provider: str
    database: str
    time: str


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    db_ok = check_db(request.app.state.engine)
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        app=settings.app_name,
        version=__version__,
        environment=settings.environment,
        llm_provider=settings.llm_provider,
        database="ok" if db_ok else "unavailable",
        time=datetime.now(UTC).isoformat(),
    )


class CompletionRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    system: str | None = Field(default=None, max_length=4000)


class CompletionResponse(BaseModel):
    text: str
    provider: str
    model: str
    latency_ms: float
    fallback_used: bool


@router.post("/api/llm/complete", response_model=CompletionResponse, tags=["llm"])
async def llm_complete(body: CompletionRequest, request: Request) -> CompletionResponse:
    service = LLMService(request.app.state.settings, request.app.state.http_client)
    outcome = await service.complete(body.prompt, body.system)
    r = outcome.result
    return CompletionResponse(
        text=r.text,
        provider=r.provider,
        model=r.model,
        latency_ms=r.latency_ms,
        fallback_used=outcome.fallback_used,
    )
