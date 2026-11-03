"""Send the same prompt to Groq and Gemini and show each response and its response time.

Usage (from the project root):
    .\\.venv\\Scripts\\python -m scripts.compare_llms
    .\\.venv\\Scripts\\python -m scripts.compare_llms --prompt "Explain RAG in one sentence."
    .\\.venv\\Scripts\\python -m scripts.compare_llms --list-models

This makes real API calls (one request per provider) and uses your free-tier quota.
"""

import argparse
import asyncio
import sys
import time

from backend.app.core.config import Settings
from backend.app.llm.base import LLMConfigError, LLMError, LLMRateLimitError, LLMTimeoutError
from backend.app.llm.factory import build_provider, make_http_client

DEFAULT_PROMPT = "In two sentences, explain what a Forward Deployed Engineer does."
PROVIDERS = ("groq", "gemini")


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _explain(exc: LLMError, name: str) -> str:
    key_var = f"{name.upper()}_API_KEY"
    model_var = f"{name.upper()}_MODEL"
    if isinstance(exc, LLMConfigError):
        if "not set" in exc.message:
            return f"SKIPPED: {exc.message}. Add it to .env (see .env.example)."
        if "rejected" in exc.message:
            return f"KEY REJECTED: check {key_var} in .env.\n  {exc.message}"
        return f"NOT FOUND: {exc.message}\n  Check {model_var} in .env, or run with --list-models."
    if isinstance(exc, LLMRateLimitError):
        wait = (
            f" Try again in about {exc.retry_after_seconds:g}s." if exc.retry_after_seconds else ""
        )
        return f"RATE LIMITED: you've hit the free-tier limit.{wait}\n  {exc.message}"
    if isinstance(exc, LLMTimeoutError):
        return f"TIMEOUT: no reply within LLM_TIMEOUT_SECONDS. {exc.message}"
    return f"ERROR: {exc.message}"


async def run(prompt: str, list_models: bool) -> int:
    settings = Settings()
    ok = 0
    async with make_http_client(settings) as client:
        for name in PROVIDERS:
            model = getattr(settings, f"{name}_model") or "(GEMINI_MODEL/GROQ_MODEL not set)"
            _banner(f"{name.upper()}  model={model}")
            try:
                provider = build_provider(name, settings, client)
                if list_models:
                    models = await provider.list_models()
                    flag = "YES" if provider.model in models else "NO"
                    print(f"Configured model available on your account: {flag}")
                    print("Models your key can use:")
                    for m in models:
                        print(f"  - {m}")
                    ok += 1
                    continue
                start = time.perf_counter()
                result = await provider.complete(prompt)
                total_ms = (time.perf_counter() - start) * 1000
            except LLMError as exc:
                print(_explain(exc, name))
                continue
            ok += 1
            print(f"Response time: {total_ms:,.0f} ms   (model reported: {result.model})")
            if result.usage:
                print(f"Usage: {result.usage}")
            print("-" * 70)
            print(result.text)
    print()
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--list-models", action="store_true", help="list models your keys can use")
    args = parser.parse_args()
    if not args.list_models:
        print(f"Prompt: {args.prompt}")
    sys.exit(asyncio.run(run(args.prompt, args.list_models)))


if __name__ == "__main__":
    main()
