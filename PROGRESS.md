# PROGRESS

What's done, what's in progress, and what's verified. Update it every time a feature passes its tests.

**Status key:** Not started · In progress · Done (tests pass) · Blocked

## Current focus

Case Study 2, Cold-Chain Alerting Engine. Phase 1: analysis and SOLUTION_DESIGN.md.

## Features

| # | Feature | Status | Tests run (command → result) | Committed | Notes |
|---|---------|--------|------------------------------|-----------|-------|
| — | — | — | — | — | — |

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
