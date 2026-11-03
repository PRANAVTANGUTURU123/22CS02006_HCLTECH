# Hackathon Starter (FastAPI + React)

A reusable starter for a one-day build. It runs as **one app**: FastAPI serves the API and the built
React frontend. The LLM runs in **mock mode** by default, so the app and tests work without API keys.

| Layer | What's used |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic (pydantic-settings), SQLAlchemy 2 + SQLite |
| Frontend | React 19, TypeScript, Vite, built to `frontend/dist` and served by FastAPI |
| LLM | Small wrapper over Groq (default) and Gemini (backup) REST APIs, plus `mock` |
| Search (optional) | pypdf, Chroma, BM25 (`backend/app/search`, not used by the core app) |
| Quality | pytest, pytest-cov, ruff, pip-audit |
| Operations | structlog, request timeouts, tenacity retries, one error format, `/health` |

All commands below are **PowerShell**, run from the project root (`E:\Studies\HCL_HACKATHON`).

---

## 1. Setup (first time, or on a new machine)

Needs Python 3.12, Node.js LTS (20.19+), and Git.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
# Optional, only if the brief needs PDF/document search:
.\.venv\Scripts\python -m pip install -r requirements-search.txt

Copy-Item .env.example .env        # then edit .env yourself; never commit it

cd frontend; npm install; cd ..
```

> If PowerShell blocks `.ps1` scripts, run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## 2. Run

**As one app** (what gets deployed):

```powershell
cd frontend; npm run build; cd ..
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000 --no-access-log
```

Open http://127.0.0.1:8000. The API docs are at http://127.0.0.1:8000/docs.

**Frontend dev mode with hot reload** (two terminals):

```powershell
# Terminal 1
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000 --no-access-log
# Terminal 2
cd frontend; npm run dev
```

Open http://127.0.0.1:5173. Vite forwards `/api` and `/health` to port 8000.

**Quick checks while it's running:**

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/llm/complete -Method Post -ContentType application/json -Body '{"prompt":"Hello"}'
```

## 3. Test and lint

```powershell
.\.venv\Scripts\python -m pytest                 # tests + coverage report
.\.venv\Scripts\ruff check .                     # lint
.\.venv\Scripts\ruff format .                    # auto-format
.\.venv\Scripts\pip-audit -r requirements.lock.txt   # known vulnerabilities
cd frontend; npm run typecheck; cd ..
```

Tests **never** call a real LLM. They ignore `.env`, force `LLM_PROVIDER=mock`, use fake HTTP
responses for the Groq and Gemini adapters, and block outbound network connections.

## 4. Compare LLM providers (uses real quota)

1. Get keys: Groq at https://console.groq.com/keys, Gemini at https://aistudio.google.com/apikey
2. Open `.env` in your editor and set `GROQ_API_KEY=` and `GEMINI_API_KEY=`. Don't paste keys into chat or commands.
3. Run:

```powershell
.\.venv\Scripts\python -m scripts.compare_llms --list-models   # are the configured models on your account?
.\.venv\Scripts\python -m scripts.compare_llms                 # same prompt to both, with timings
.\.venv\Scripts\python -m scripts.compare_llms --prompt "Summarise RAG in one sentence."
```

For each provider it prints the response time and the answer. It also prints a clear message when a key is
missing (`SKIPPED`), rejected (`KEY REJECTED`), the model is not found (`NOT FOUND`), or the free tier is
used up (`RATE LIMITED`, with the retry-after time when the provider gives one).

To make the app use real providers, set these in `.env` and restart:

```
LLM_PROVIDER=groq
LLM_FALLBACK_PROVIDER=gemini
```

Model names live only in `.env` (`GROQ_MODEL`, `GEMINI_MODEL`). The defaults in `.env.example` were
current free-tier models on 2026-11-03: `openai/gpt-oss-20b` (Groq) and `gemini-3.5-flash-lite`
(Gemini). Check https://console.groq.com/docs/models and https://ai.google.dev/gemini-api/docs/models
before the event, because model names change often.

## 5. Configuration

All settings are in [.env.example](.env.example), with comments. Environment variables override `.env`.
The ones you're most likely to change:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `groq` or `gemini` |
| `LLM_FALLBACK_PROVIDER` | blank | Used when the primary fails (rate limit, timeout, outage, bad config) |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | 20 / 2 | Retries cover timeouts and 5xx only, never 429 |
| `REQUEST_TIMEOUT_SECONDS` | 30 | Whole-request limit, returns 504 `request_timeout` |
| `DATABASE_URL` | `./data/app.db` | Any SQLAlchemy URL |
| `LOG_JSON` | false | JSON logs for the cloud |

Every error response looks like this:

```json
{"error": {"code": "llm_rate_limited", "message": "...", "request_id": "...", "details": {...}}}
```

## 6. Project layout

```
backend/app/
  main.py            app factory, lifespan, serves frontend/dist
  api/routes.py      /health, POST /api/llm/complete
  core/              config, logging, error handlers, middleware (request id, timeout)
  db/session.py      SQLAlchemy engine/session, Base for models
  llm/               base types, providers (mock/groq/gemini), LLMService (retry + fallback)
  search/            OPTIONAL: PDF chunking + BM25/Chroma hybrid search
frontend/src/        React app (App.tsx, api.ts)
scripts/             compare_llms.py, package_azure.ps1, azure_set_settings.ps1
tests/               pytest (mock-only)
```

**Adding a feature:** add a router in `backend/app/api/`, include it in `main.py`, put models in
`backend/app/db/models.py`, add tests in `tests/`, and add UI in `frontend/src/`. If the brief
provides starter code or a different stack, keep the parts that still fit: the LLM wrapper, error
handling, logging and test setup.

**Using search:**

```python
from backend.app.search.documents import load_pdf_chunks
from backend.app.search.hybrid import HybridIndex

index = HybridIndex(persist_dir="data/chroma")  # default embedder downloads ~80 MB on first use
index.add(load_pdf_chunks("docs/policy.pdf"))
hits = index.search("refund window", k=5)
```

## 7. Deploy

Both options upload source code, and the cloud installs Python packages from `requirements.txt`.
**Build the frontend first.** Git ignores `frontend/dist`, but each deploy path still uploads it:

- **Azure:** `scripts/package_azure.ps1` puts `frontend/dist` into `deploy/app.zip` explicitly.
- **Cloud Run:** `.gcloudignore` is used instead of `.gitignore` and does not exclude `frontend/dist`.

Start command (both clouds): `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port <PORT> --no-access-log`

### Option A: Azure App Service (Linux, Python 3.12)

```powershell
az login                                   # opens a browser; sign in yourself

$RG   = "rg-hackathon"
$PLAN = "plan-hackathon"
$APP  = "hclhack-<yourname>"               # must be globally unique: becomes <APP>.azurewebsites.net
$LOC  = "centralindia"                     # if a region is refused, try southindia / eastus / westeurope

# One-time: create the resources (F1 = free tier)
az group create --name $RG --location $LOC
az appservice plan create --name $PLAN --resource-group $RG --is-linux --sku F1
az webapp create --name $APP --resource-group $RG --plan $PLAN --runtime "PYTHON:3.12"
az webapp config set --name $APP --resource-group $RG --startup-file "python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --no-access-log"

# Copy LLM settings and keys from .env into App Service (values are never printed)
.\scripts\azure_set_settings.ps1 -ResourceGroup $RG -AppName $APP

# Every deploy: build, package, upload
.\scripts\package_azure.ps1
az webapp deploy --name $APP --resource-group $RG --src-path deploy\app.zip --type zip

# Check it
Invoke-RestMethod "https://$APP.azurewebsites.net/health"
az webapp log tail --name $APP --resource-group $RG
```

Delete everything afterwards: `az group delete --name $RG --yes`

### Option B: Google Cloud Run

Needs the Google Cloud CLI (not installed yet) and a project with billing enabled.

```powershell
gcloud auth login
gcloud config set project <PROJECT_ID>
cd frontend; npm run build; cd ..
gcloud run deploy hackathon-app --source . --region asia-south1 --allow-unauthenticated `
  --set-env-vars "LLM_PROVIDER=mock,LOG_JSON=true,ENVIRONMENT=cloudrun"
```

Cloud Run uses the `Procfile` start command and `.python-version`. Store API keys in Secret Manager
(`--set-secrets GROQ_API_KEY=groq-key:latest`), not in `--set-env-vars`. Note that SQLite on Cloud Run is
lost on restart.

## 8. Updating dependencies

```powershell
# After editing requirements.txt, rebuild the lock file from a clean venv:
py -3.12 -m venv $env:TEMP\lockvenv
& "$env:TEMP\lockvenv\Scripts\python" -m pip install -r requirements.txt
& "$env:TEMP\lockvenv\Scripts\python" -m pip freeze --exclude pip | Out-File -Encoding ascii requirements.lock.txt
Remove-Item -Recurse -Force $env:TEMP\lockvenv
```

## Secrets checklist

- `.env` is in `.gitignore`. Only `.env.example`, which has blank keys, is tracked.
- Keys go into the cloud through app settings (Azure) or Secret Manager (GCP), never in code.
- `/health` and logs never include keys. Settings use `SecretStr`.
