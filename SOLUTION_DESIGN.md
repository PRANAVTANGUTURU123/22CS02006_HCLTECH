# Solution Design: <Project name>

> Template. Replace every `<...>` and delete guidance lines (in *italics*) before submitting.
> Keep it short: judges read this in a few minutes.

**Team / author:** <name>  **Date:** <date>  **Live URL:** <url>  **Repo:** <url>

---

## 1. Problem restatement

*Restate the brief in your own words: who the user is, what hurts today, and what "done" looks like.*

- **User:** <who>
- **Problem:** <one or two sentences>
- **Goal / success criteria:** <measurable outcomes, e.g. "answers policy questions with a source citation in under 5 s">
- **Out of scope:** <what you deliberately won't build today>

## 2. Assumptions

*List the things you decided without being told. Each one should be easy to check with the client.*

| # | Assumption | Why it's reasonable | Impact if wrong |
|---|---|---|---|
| A1 | <e.g. under 1,000 documents> | <...> | <...> |
| A2 | | | |

## 3. Architecture

*One diagram and a few sentences. Show the request path from the user to the data and back.*

```
Browser (React, served by FastAPI)
   │  HTTPS / JSON
   ▼
FastAPI ── /health
   │       /api/...  ──►  LLMService ──► Groq (primary) ──► Gemini (fallback) / mock
   │                      │
   │                      └─► Search (optional): pypdf → chunks → BM25 + Chroma → RRF
   ▼
SQLAlchemy ──► SQLite (swap for Postgres via DATABASE_URL)
```

- **Components:** <what each piece does>
- **Data flow:** <step-by-step for the main use case>
- **Deployment:** <Azure App Service / Cloud Run, region, tier>

## 4. Alternatives and trade-offs

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Deployment shape | Single app (FastAPI serves the static React build) | Separate frontend host + API | One URL, no CORS, one deploy |
| LLM access | Thin REST wrapper, Groq → Gemini fallback | Vendor SDKs, LangChain | Fewer dependencies, easy to switch providers, testable with fakes |
| Storage | SQLite | Postgres, Cosmos DB / Firestore | Zero setup for a demo; change `DATABASE_URL` to scale |
| Retrieval | <BM25 + vectors / none> | <...> | <...> |
| <your decision> | | | |

## 5. Requirements mapping

*Trace every requirement in the brief to where it's met and how it was checked.*

| Req ID | Requirement (from brief) | Implementation (file / endpoint) | Verified by | Status |
|---|---|---|---|---|
| R1 | <...> | <...> | <test name / manual demo step> | Done / Partial / Not started |
| R2 | | | | |

## 6. Security and privacy

- **Secrets:** kept in `.env` locally and in App Service settings / Secret Manager in the cloud. Never in code or Git.
- **Data sent to LLMs:** <what user data leaves the system, to which provider, and why that's acceptable>
- **PII handling:** <masking or redaction, retention period, logging policy (no prompts or keys in logs)>
- **Input validation:** Pydantic schemas with length limits; consistent error format with no stack traces.
- **Auth:** <none for the demo / API key / Entra ID / IAP>. <What production would need.>
- **Prompt injection / output safety:** <guardrails, if any>
- **Dependencies:** `pip-audit` result: <clean / issues and how they were handled>

## 7. Testing

- **Automated:** `pytest` (<N> tests, <X>% coverage). They use mock LLM mode and never call real APIs.
- **What's covered:** <health, API contract, provider adapters (fake HTTP), retry/fallback, error format, ...>
- **Manual / demo script:** <the steps you'll show the judges>
- **Evaluation of LLM quality:** <small golden set, accuracy numbers, failure examples>

## 8. Operations

- **Health:** `GET /health` returns app version, DB status and the active LLM provider.
- **Logging:** structlog, JSON lines in the cloud, a request ID on every log line and response (`X-Request-ID`).
- **Resilience:** request timeout (`REQUEST_TIMEOUT_SECONDS`), LLM timeout and retries with jitter (tenacity), fallback provider on rate limit or outage.
- **Cost:** <free tiers used; expected cost at N users/day>
- **Monitoring next step:** <App Insights / Cloud Logging alerts on 5xx and LLM failures>

## 9. Limitations

*Be honest. Judges trust teams that know where their solution breaks.*

- <e.g. SQLite is single-instance; not suitable for scaling out>
- <e.g. free-tier LLM rate limits cap throughput at ~N requests/min>
- <e.g. no authentication>

## 10. Next steps

| Priority | Next step | Effort |
|---|---|---|
| 1 | <...> | <S/M/L> |
| 2 | | |
| 3 | | |
