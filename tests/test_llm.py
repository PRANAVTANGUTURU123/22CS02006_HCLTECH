"""Provider adapters tested against fake HTTP responses (httpx.MockTransport). No real calls."""

import httpx
import pytest

from backend.app.llm.base import (
    LLMConfigError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from backend.app.llm.factory import LLMService
from backend.app.llm.providers import GeminiProvider, GroqProvider
from tests.conftest import make_settings


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _groq(client, **kw):
    args = dict(
        api_key="k",
        model="m",
        client=client,
        base_url="https://groq.test/v1",
        max_output_tokens=64,
        temperature=0,
    )
    args.update(kw)
    return GroqProvider(**args)


def _gemini(client, **kw):
    args = dict(
        api_key="k",
        model="m",
        client=client,
        base_url="https://gemini.test/v1beta",
        max_output_tokens=64,
        temperature=0,
    )
    args.update(kw)
    return GeminiProvider(**args)


async def test_groq_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer k"
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": " hi "}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 3},
            },
        )

    async with _client(handler) as c:
        r = await _groq(c).complete("hello", system="be brief")
    assert r.text == "hi"
    assert r.provider == "groq"
    assert r.usage == {"total_tokens": 3}


async def test_gemini_parses_response_and_skips_thoughts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "k"
        assert request.url.path.endswith("/models/m:generateContent")
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "thinking...", "thought": True},
                                {"text": "answer"},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": "m-001",
            },
        )

    async with _client(handler) as c:
        r = await _gemini(c).complete("hello")
    assert r.text == "answer"
    assert r.model == "m-001"


@pytest.mark.parametrize("factory", [_groq, _gemini])
async def test_rate_limit_is_reported(factory):
    def handler(request):
        return httpx.Response(
            429, headers={"retry-after": "12"}, json={"error": {"message": "slow down"}}
        )

    async with _client(handler) as c:
        with pytest.raises(LLMRateLimitError) as info:
            await factory(c).complete("x")
    assert info.value.retry_after_seconds == 12
    assert "slow down" in info.value.message


@pytest.mark.parametrize("factory", [_groq, _gemini])
async def test_bad_key_is_config_error(factory):
    async with _client(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})) as c:
        with pytest.raises(LLMConfigError):
            await factory(c).complete("x")


def test_missing_key_and_model_raise_config_error():
    c = httpx.AsyncClient()
    with pytest.raises(LLMConfigError, match="GROQ_API_KEY"):
        _groq(c, api_key="")
    with pytest.raises(LLMConfigError, match="GEMINI_MODEL"):
        _gemini(c, model="")


async def test_timeout_maps_to_timeout_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as c:
        with pytest.raises(LLMTimeoutError):
            await _groq(c).complete("x")


async def test_empty_response_is_provider_error():
    body = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        with pytest.raises(LLMProviderError, match="LLM_MAX_OUTPUT_TOKENS"):
            await _groq(c).complete("x")


async def test_service_retries_5xx_then_succeeds(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    settings = make_settings(
        tmp_path,
        llm_provider="groq",
        groq_api_key="k",
        groq_base_url="https://groq.test/v1",
        llm_max_retries=2,
    )
    async with _client(handler) as c:
        outcome = await LLMService(settings, c).complete("x")
    assert outcome.result.text == "ok"
    assert calls["n"] == 2


async def test_service_falls_back_on_rate_limit(tmp_path):
    def handler(request):
        if "groq.test" in str(request.url):
            return httpx.Response(429, json={"error": {"message": "limit"}})
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "from gemini"}]}}]}
        )

    settings = make_settings(
        tmp_path,
        llm_provider="groq",
        llm_fallback_provider="gemini",
        groq_api_key="k",
        gemini_api_key="k",
        groq_base_url="https://groq.test/v1",
        gemini_base_url="https://gemini.test/v1beta",
    )
    async with _client(handler) as c:
        outcome = await LLMService(settings, c).complete("x")
    assert outcome.fallback_used is True
    assert outcome.result.provider == "gemini"
    assert outcome.result.text == "from gemini"


def test_blank_fallback_env_means_none(tmp_path):
    assert make_settings(tmp_path, llm_fallback_provider="").llm_fallback_provider is None
