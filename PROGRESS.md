# PROGRESS

What's done, what's in progress, and what's verified. Update it every time a feature passes its tests.

**Status key:** Not started · In progress · Done (tests pass) · Blocked

## Current focus

Case Study 2, Cold-Chain Alerting Engine. Phase 1: analysis and SOLUTION_DESIGN.md.

Analysis done. `SOLUTION_DESIGN.md` and `docs/ASSUMPTIONS.md` written; the rules were checked by hand-simulating the state machine on the seed data (T-01 and T-05 alert 08:30, close 08:50; T-04 alerts 08:45; T-02 and T-03 no alert). Design approved; Phase 2 (build) in progress. Decided: data-based "not improving" update every 10 min (no acknowledge button); webhook for push (no SMS), AI alert summary and read-only MCP server in v1; cargo profiles and sensor-fault notification are future work for now. Next: feature 5, FastAPI API + PostgreSQL.

## Features

| # | Feature | Status | Tests run (command → result) | Committed | Notes |
|---|---------|--------|------------------------------|-----------|-------|
| 1 | Core engine: config, loader (validate, fault-flag, dedupe, sort), per-truck state machine (OK/SUSPECT/ALERT/RECOVERING, gaps, silence, Critical, one-time notifications with stable ids) in `backend/engine/` | Done (tests pass) | `.\.venv\Scripts\python -m pytest` → 79 passed, 99% coverage of `backend`; `.\.venv\Scripts\ruff check .` → all checks passed | Yes | Seed data: T-01 & T-05 alert 08:30, close 08:50; T-04 alerts 08:45, still recovering; T-02/T-03 none; no silences. Shuffling the input 5 ways gives the same result. Test confirms the starter code finds only T-01. Late-data (>60 min) handling is live-mode only, not built in v1 batch. |
| 2 | CLI `python -m backend.cli [file] [--json] [--now] [--alert-after-min] [--min/max-safe-c]`: alerts with reasons, why each other truck didn't alert, truck status, silences, data problems, notifications that would be sent. Shared `backend/report.py` (dict form for CLI/API/MCP); engine now keeps short episodes | Done (tests pass) | `.\.venv\Scripts\python -m pytest` → 86 passed, 99% coverage; `ruff check .` → all checks passed; ran `python -m backend.cli` on the seed data → 3 alerts as expected | Yes | Clean exit code 2 with a message for a missing file, bad `--now` or a bad config override. |
| 2b | "Not improving" update (rule A11): every 10 min while a Warning alert is out of range, compare with the previous check; if not back toward the safe range by >= 0.3 °C, one update (id `alert:update:N`). Config `update_every_min`, `improve_min_c`. Shown in CLI | Done (tests pass) | `.\.venv\Scripts\python -m pytest` -> 98 passed, 99% coverage; `ruff check .` -> all checks passed; CLI on seed data -> still 3 notifications | Yes | Tests found two real bugs, both fixed: float rounding (9.6->9.3 counted as 0.2999 and missed), and a late check after a gap followed by another only 5 min later. |
| 3 | Webhook notifier `backend/notify/` + `backend/settings.py`: console sender (default) and webhook sender (HTTPS only, 5 s timeout, 3 attempts with backoff on timeout/network/429/5xx, no retry on 4xx); payload with `content` (Discord) + `text` (Slack); dispatcher skips already-sent ids, logs failures and keeps going, optional summarizer hook for feature 4; CLI `--send` | Done (tests pass) | `.\.venv\Scripts\python -m pytest` -> 124 passed, 99% coverage; `ruff check .` -> all checks passed | Yes | A test found that httpx logs the full webhook URL (the token is in the path) at INFO; httpx/httpcore logging is now held at WARNING. Bad URL errors never echo the URL. Tests force ALERT_WEBHOOK_URL empty (tests/conftest.py), so no real HTTP. Sent ids are in memory until the database lands with the API. |
| 4 | AI alert summary `backend/ai/`: facts (numbers only, as of the notification), sync Groq/Gemini/mock clients with fallback inside a 5 s budget, answer check (right truck, <= 300 chars, no time or number not in the facts), one call per notification (cached); CLI `--ai` (with `--send` the AI text goes out with the facts underneath) | Done (tests pass) | `pytest` -> 152 passed (junit: 0 failures, 0 errors), ~98% coverage; `ruff check .` -> all checks passed; live: `python -m backend.cli --ai` with Groq -> 3 summaries, all passed the check; `--send` delivered 3/3 to Discord #ops-alerts | Yes | Wrote a small sync client instead of restoring the old async wrapper (it depended on removed modules). API keys only in headers, never in URLs or errors. README added. |

## Environment and setup (done before the brief)

| Item | Status | Verified by |
|------|--------|-------------|
| Python 3.12 venv with runtime and dev dependencies | Done | All packages import; `pip-audit -r requirements.lock.txt` → no known vulnerabilities |
| Frontend packages (React, lucide-react, vitest, Testing Library, jsdom) | Done | `npm test` runs; `npm audit` → 0 vulnerabilities |
| Local embedding model (all-MiniLM-L6-v2) pre-downloaded | Done | Generated a 384-dim embedding; stored outside the repo |
| Azure App Service (F1, indiasouthcentral) | Done | Live `/health` OK; real Groq call from the deployed app |
| GitHub CLI and repo `22CS02006_HCLTECH` | Done | `gh auth status`; repo created |
| Playwright MCP | Done | Opened the live site, sent a prompt, screenshot captured |
| Local PostgreSQL 16 (`appdb`, limited user `appuser`) + psycopg 3.3.7 driver | Done | Python/SQLAlchemy connect OK; injection string stored as plain text via bound params, table intact; `CREATE DATABASE` / `CREATE ROLE` refused (InsufficientPrivilege) |

## Decisions log

| Date | Decision | Why |
|------|----------|-----|
| 2026-11-04 | Removed the test starter app; kept dependencies, config and Azure scripts | Start the hackathon from a clean repo; old starter recoverable from commit `4d6f67c` |
| 2026-11-04 | Brief received (Case Study 2, cold-chain alerting). Added brief and starter kit; removed `requirements-search.txt` and `scripts/` | No document search needed; Cloud Run is the primary deploy. Both recoverable from commit `452bf0f` |
| 2026-11-04 | PostgreSQL instead of SQLite, via SQLAlchemy + `DATABASE_URL` | Concurrent writes and multi-instance scaling; SQLite remains a one-line fallback. Reused the existing local PostgreSQL 16 (works offline). Azure PostgreSQL not created yet (B1ms ≈ $0.0245/h in indiasouthcentral) |
| 2026-11-04 | Live Azure app uses SQLite (`sqlite:////home/data/app.db`); local dev uses PostgreSQL; switch the cloud to Azure PostgreSQL only if needed | No cloud DB cost or setup risk for a single-instance demo; same SQLAlchemy code works on both, so the switch is a `DATABASE_URL` change |

## Cloud database status (end of 2026-11-03)

| Item | Status | Notes |
|------|--------|-------|
| Google Cloud project `hcl-hackathon-513621` (free trial) with Cloud Run, Cloud SQL, Cloud Build, Artifact Registry, Secret Manager enabled | Done | Verified via `gcloud services list` |
| Cloud SQL `hcl-db` (PostgreSQL 16, db-f1-micro, Mumbai) with `appdb` + limited `appuser` | Done | Verified from inside Google Cloud with a one-off Cloud Run job via the Cloud SQL connector: connected as appuser; superuser/createdb/createrole all false; injection string stored as text, table intact; `CREATE DATABASE` refused. Connection string stored as secret `database-url` |
| Laptop → Cloud SQL from campus Wi-Fi | Blocked by network | TLS resets on 3307 and 5432; not needed, because Cloud Run connects inside Google. Develop against local PostgreSQL |

## Known issues / next up

- Upload, frontend-component and end-to-end browser tests: to be written with the first real feature.
- Configurable embedding provider (local vs Gemini) for cloud deployment: build if the brief needs document search.
