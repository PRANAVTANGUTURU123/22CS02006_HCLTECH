# CLAUDE.md

Standing instructions for every Claude Code session in this project.

## Source of truth

- Keep **CLAUDE.md**, **SOLUTION_DESIGN.md** and **PROGRESS.md** up to date. They are the source of truth for what we're building, why, and how far we've got.
- If code and these files disagree, fix whichever is wrong in the same change.
- SOLUTION_DESIGN.md is created during the analysis phase, before any code is written.

## How we work

- Work on **one feature at a time**. Don't start the next feature until the current one is done.
- A feature is **done** only when its relevant tests have been run and pass. Report what actually ran and its result; never claim something works without running it.
- When a feature passes:
  1. Update PROGRESS.md (status, tests run and results, notes).
  2. Ask the user whether to commit it. Don't commit without a yes.
- Before writing each important file, post 2–3 plain lines saying what it does and why.

## Always ask first

Ask before installing software, creating or deleting cloud resources, deploying, creating repos, committing or pushing.

## Secrets and attribution

- API keys live only in `.env` (ignored by Git). Never print, commit or ask the user to paste secrets into chat.
- Never add "Co-Authored-By: Claude", "Generated with Claude Code" or any other Claude/Anthropic attribution to commits, PRs or release notes.

## Environment

- Windows 11; give PowerShell commands (most also work in cmd).
- Python 3.12 in `.venv`: `.\.venv\Scripts\python -m pytest`, `.\.venv\Scripts\ruff check .`
- Frontend in `frontend/`: `npm run build`, `npm test` (vitest), `npm run typecheck`
- Automated tests use the LLM mock mode, never real API calls.
- Database: SQLAlchemy only, no SQLite- or Postgres-specific SQL, always bound parameters. Local dev uses PostgreSQL 16 (`DATABASE_URL` in `.env`). Cloud PostgreSQL is ready on Google Cloud: project `hcl-hackathon-513621`, Cloud SQL instance `hcl-hackathon-513621:asia-south1:hcl-db`, database `appdb`, user `appuser`; the full URL is in Secret Manager as `database-url`. Deploy to Cloud Run in `asia-south1` with `--add-cloudsql-instances hcl-hackathon-513621:asia-south1:hcl-db --set-secrets DATABASE_URL=database-url:latest` (the default compute service account already has cloudsql.client and secretAccessor). The campus Wi-Fi blocks laptop → Cloud SQL, so test DB code locally. Fallback: the Azure App Service app with SQLite. gcloud lives at `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd`.
- Deploy target: Google Cloud Run (primary). The Azure/DB helper scripts were removed from the tree; restore them from commit `452bf0f` (`git checkout 452bf0f -- scripts`) if the Azure fallback is needed. Dev tools stay in `requirements-dev.txt`.
- A previous starter (LLM wrapper, error handling, /health, tests) can be restored from git commit `4d6f67c` if useful.
