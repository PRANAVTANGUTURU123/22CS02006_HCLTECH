# Cold-Chain Alerting Engine — Nimbus Freight

Alerts the ops team **once per real, sustained temperature problem on a truck**: not on every warm reading, not on brief blips, and not over and over for the same problem. It stays correct when readings go missing or arrive out of order.

- **Design document:** `SOLUTION_DESIGN.pdf` (problem, assumptions, design, trade-offs, next steps, testing, screenshots). Markdown sources: `SOLUTION_DESIGN.md`, `docs/ASSUMPTIONS.md`, `docs/TESTING.md` on GitHub.
- **Live:** https://cold-chain-alerts-78623167451.asia-south1.run.app (Cloud Run; reads are open, changes need an admin key)
- **Code:** https://github.com/PRANAVTANGUTURU123/22CS02006_HCLTECH

## What I built

- **Rules engine** (pure Python): cleans and sorts readings, then runs one state machine per truck: OK → SUSPECT → ALERT → RECOVERING → OK. It alerts after **20 min out of 2–8 °C** (by sensor timestamps), closes after **10 min back in range**, bridges gaps up to 15 min, and flags silent sensors.
- **CLI report:** "what I'd alert on, and why", including why the other trucks did *not* alert.
- **Webhook notifier** (Discord/Slack): each alert is sent **once** (fixed ids, stored in the database), with a 5 s timeout and 3 retries.
- **AI summary (bonus):** an LLM words the alert in one line; the rules decide. Every number it writes is checked, and if it fails the plain message is sent.
- **FastAPI API + React dashboard**, deployed on **Google Cloud Run**, plus a read-only **MCP server** so an AI assistant can answer "which trucks are in trouble?".

## Requirement → where it is

| Requirement (brief) | Where |
|---|---|
| Alert only on a sustained issue, per truck | `backend/engine/state_machine.py`, thresholds in `backend/engine/config.py` |
| Ignore brief blips | `state_machine.py` (SUSPECT resets after 10 min in range); `tests/test_state_machine.py` |
| Don't re-alert the same ongoing issue | Fixed notification ids: `state_machine.py`, `backend/notify/dispatcher.py`, `backend/store.py` |
| Handle gaps in data | `state_machine.py` (gap bridging, "sensor silent") |
| Handle out-of-order data and duplicates | `backend/engine/loader.py` (validate, dedupe, sort by sensor time) |
| Runs end-to-end, prints "what I'd alert on, and why" | `backend/cli.py`, `backend/report.py` |
| Assumptions documented | `SOLUTION_DESIGN.pdf` §3 and appendix A |
| Bonus: LLM one-line summary | `backend/ai/summarizer.py`, `backend/ai/llm.py` |
| Security (admin key, size limits, secrets) | `backend/api.py`, `backend/settings.py` |
| Tests | `tests/` (212), `frontend/src/App.test.tsx` (7), `tests_e2e/` (2) |

**Key files, in reading order:** `backend/engine/state_machine.py`, `backend/engine/loader.py`, `backend/engine/config.py`, `backend/engine/models.py`, `backend/report.py`, `backend/cli.py`, `backend/notify/dispatcher.py`, `backend/ai/summarizer.py`, `backend/api.py`, `backend/mcp_server.py`, `tests/test_state_machine.py`, `tests/test_seed_data.py`.

## Run and test (quick)

```powershell
python -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m backend.cli                       # the alert report on the seed data
.\.venv\Scripts\python -m backend.cli --alert-after-min 25  # "what if": T-04 drops out
.\.venv\Scripts\python -m pytest                            # 212 tests, 97% coverage
```

No keys are needed: without `.env`, alerts print to the console and the AI uses mock mode.

## Trade-offs and gaps

- **Duration-based state machine** over a rolling average (hides spikes, still flaps) or ML (54 readings isn't enough, and ops can't see *why*). The cost: an alert can't fire before 20 min.
- **20 min, not 25:** T-04 is out of range for exactly 20 min, so 25 would miss a real failure. It's one config value.
- **Not built:** streaming ingestion and late-data handling (designed only), sensor-fault notifications, per-user logins, ranges per shipment. Readings live in memory in the API (sent ids are in the database). The cloud runs Python 3.13; tests were run on 3.12.

## What it decides on the seed data

| Truck | What happens | Result |
|---|---|---|
| T-01 | Above 8 °C 08:10–08:40, peak 9.6 °C | **Alert** at 08:30, closed at 08:50 |
| T-02 | One reading of 8.9 °C | No alert: a single-reading blip |
| T-03 | Climbs to 7.0 °C | No alert: never out of range |
| T-04 | Above 8 °C 08:25–08:45, **08:35 reading missing** | **Alert** at 08:45 (20 min by timestamps); the starter code missed it |
| T-05 | Same as T-01 but **shuffled in the file** | **Alert** at 08:30 after sorting; the starter code missed it |

Three alerts, three notifications, and a stated reason for every truck, including why T-02 and T-03 did *not* alert.

## Demo commands (tested)

Run from the project folder in PowerShell. Expected results are shown under each command.

### 1. The 3 alerts we expect (seed data)

```powershell
.\.venv\Scripts\python -m backend.cli
```

```
ALERTS (3)
  [WARNING] T-01  Above 8 °C for 30 min (08:10–08:40), peak 9.6 °C.
  [WARNING] T-05  Above 8 °C for 30 min (08:10–08:40), peak 9.6 °C.
  [WARNING] T-04  Above 8 °C for 20 min (08:25–08:45), peak 9.1 °C. Includes missing data: 08:30–08:40 (10 min).
NO ALERT (2)
  T-02  one reading out of range (8.9 °C at 08:10), under the 20-min limit
  T-03  never outside 2–8 °C
```

Send the same 3 alerts to Discord `#ops-alerts` (AI-worded): `.\.venv\Scripts\python -m backend.cli --ai --send`

### 2. Change the range or the time limit: the alerts change

| Command | Range / limit | Alerts |
|---|---|---|
| `.\.venv\Scripts\python -m backend.cli` | 2–8 °C, 20 min (default) | **3**: T-01, T-05, T-04 |
| `.\.venv\Scripts\python -m backend.cli --alert-after-min 25` | 2–8 °C, 25 min | **2**: T-01, T-05 (T-04 was out of range for exactly 20 min, so it is missed) |
| `.\.venv\Scripts\python -m backend.cli --max-safe-c 9.5` | 2–9.5 °C | **0**: only one reading (9.6 °C) is above 9.5 |
| `.\.venv\Scripts\python -m backend.cli --min-safe-c 0 --max-safe-c 5` | 0–5 °C (chilled food) | **5**: every truck; T-01, T-02, T-04, T-05 Critical (≥ 3 °C over), T-03 Warning |

### 3. On Google Cloud: which truck has an issue

```powershell
$U = "https://cold-chain-alerts-78623167451.asia-south1.run.app"

# Every truck and its state (ok / suspect / alert / recovering)
Invoke-RestMethod "$U/api/trucks" | Select-Object truck_id, state, last_temperature_c, min_safe_c, max_safe_c | Format-Table

# Every alert with its reason
Invoke-RestMethod "$U/api/alerts" | Select-Object truck_id, status, severity, reason | Format-Table -Wrap

# Only the trucks that still need attention
Invoke-RestMethod "$U/api/alerts?status=recovering" | Select-Object truck_id, reason | Format-Table -Wrap
Invoke-RestMethod "$U/api/alerts?status=open" | Select-Object truck_id, reason | Format-Table -Wrap
```

Expected: T-01 closed, T-05 closed, **T-04 recovering** ("Above 8 °C for 20 min (08:25–08:45), peak 9.1 °C. Includes missing data: 08:30–08:40 (10 min).").

### 4. On Google Cloud: change one truck's range, see a new alert, undo it

```powershell
$key = @{ "X-API-Key" = "<your ADMIN_API_KEY>" }

# T-03 (5.8–7.0 °C) gets a chilled-food range of 0–5 °C
Invoke-RestMethod -Method Put "$U/api/trucks/T-03/range" -Headers $key -ContentType "application/json" -Body '{"min_safe_c": 0, "max_safe_c": 5}'
Invoke-RestMethod "$U/api/alerts?status=open" | Select-Object truck_id, reason | Format-Table -Wrap
#   -> T-03  Above 5 °C for 50 min (08:00–08:50), peak 7 °C.

# Undo: back to the default 2–8 °C
Invoke-RestMethod -Method Put "$U/api/trucks/T-03/range" -Headers $key -ContentType "application/json" -Body '{"min_safe_c": null, "max_safe_c": null}'
```

The same change can be made on the dashboard (open the live link, **Truck ranges** panel). Without the key the API answers 401.

## Demo: step by step

Windows PowerShell, from the project folder. About 10 minutes.

### 1. One-time setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
cd frontend; npm install; npm run build; cd ..
Copy-Item .env.example .env
```

Then open `.env` and fill in:

| Setting | Needed for | Without it |
|---|---|---|
| `ALERT_WEBHOOK_URL` | Alerts in Discord (`#ops-alerts` → Edit Channel → Integrations → Webhooks → Copy URL) | Messages print in the terminal |
| `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL=openai/gpt-oss-20b` | AI-worded alerts | `mock` wording |
| `ADMIN_API_KEY` | Protects changes (upload, truck ranges) | Changes need no key |
| `DATABASE_URL` | PostgreSQL | Local SQLite file in `data/` |

### 2. The problem and the answer (CLI)

```powershell
.\.venv\Scripts\python -m backend.cli
```

Point out: **3 alerts** (T-01, T-04, T-05) out of 21 warm readings, and a reason for every truck. T-02 was a one-reading blip; T-03 never left the range; T-04 alerted across a missing reading; T-05 was shuffled in the file.

### 3. A "what if" (tricky input)

```powershell
.\.venv\Scripts\python -m backend.cli --alert-after-min 25
```

T-04 drops out: it was out of range for exactly 20 minutes. This is why the limit is 20.

### 4. Alerts reach the ops team (Discord)

```powershell
.\.venv\Scripts\python -m backend.cli --ai --send
```

Show `#ops-alerts`: three messages, AI-worded with a likely cause and the exact facts underneath. Only one message per problem.

### 5. The ops dashboard

```powershell
.\.venv\Scripts\python -m uvicorn backend.main:app --port 8000
```

Open http://127.0.0.1:8000 (leave this terminal running and use a second one for the next steps).

- The red banner shows the open alert (T-04, recovering).
- Click **T-04** in the fleet table to see the chart: green band = safe range, red = alert period, with the 08:30–08:40 data gap.
- The alert cards show the reason and the labelled AI summary.

### 6. Each truck's own range

In **Truck ranges**: every truck was added automatically with an empty range (default 2–8 °C). Click **T-03**, enter min `0` and max `5` (chilled food), enter the admin key and click **Save range**. T-03 immediately gets an "Above 5 °C" alert. Click **Use default** to undo.

### 7. Live: a new truck has a problem

In a second terminal (use your `ADMIN_API_KEY`):

```powershell
$readings = 30,35,40,45,50 | ForEach-Object { @{ truck_id = "T-09"; timestamp = "2026-07-20T08:$($_):00"; temperature_c = 9.4; humidity_pct = 60 } }
$body = @{ readings = @($readings) } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/readings -Headers @{ "X-API-Key" = "<your ADMIN_API_KEY>" } -ContentType "application/json" -Body $body
```

T-09 (never seen before) is registered automatically, warm for 20 minutes, so it alerts. It appears on the dashboard within 20 seconds and in Discord. (The first upload also delivers the 3 seed alerts, because the server sends each alert once and hasn't sent those yet.) Run the same command again: nothing new is sent.

To start over: `Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/reset -Headers @{ "X-API-Key" = "<your ADMIN_API_KEY>" }`

### 8. Ask an AI about the fleet (MCP)

In the dashboard's **Ask about the fleet** box, type *"Which trucks are in trouble right now?"*. The answer comes from Groq calling our MCP tools on the live data; the tools it used are shown under the answer. Then try *"What happened to T-04 in the last 30 minutes?"*: it picks a different tool.

The same MCP server also works from Claude Code: 
open a **new** Claude Code chat in this folder (it reads `.mcp.json` at start), type `/mcp` and approve **cold-chain**, then ask *"Which trucks are in trouble right now?"*. It answers from the real data through read-only tools.

Backup without AI: `npx @modelcontextprotocol/inspector .venv\Scripts\python -m backend.mcp_server`, then click **get_fleet_status**.

### 9. Proof it works (tests)

```powershell
.\.venv\Scripts\python -m pytest
cd frontend; npm test; cd ..
.\.venv\Scripts\python -m pytest tests_e2e --browser-channel chrome --no-cov
```

221 tests: unit, integration and end-to-end. See [docs/TESTING.md](docs/TESTING.md).

## How it works

```
readings ─► Loader (validate, flag impossible temps, dedupe, SORT by sensor time)
         ─► State machine per truck: OK → SUSPECT → ALERT → RECOVERING → OK
         ─► alerts + notifications (fixed ids, so each is sent once)
         ─► CLI report · Discord/Slack webhook · optional AI wording
```

- **Alert** after **20 min** out of the **2–8 °C** range, measured from sensor timestamps.
- **Recovered** only after **10 min back in range**, seen by 2 readings. A single dip doesn't reset or close anything.
- **Gaps** of up to 15 min are bridged and reported in the alert. A longer silence gets its own "sensor silent" notification.
- **No repeats:** after the first alert, an update goes out only if the temperature **isn't improving** (checked every 10 min), and once more if it turns **Critical** (60 min, or ≥ 11 °C).
- **The rules decide; the AI only words the message.** Every number in an AI summary is checked against the alert, and if anything is off the plain message is sent.

## Run it

Needs Python 3.12 locally (Windows PowerShell shown). The Cloud Run build uses Python 3.13, because Google's buildpacks no longer offer 3.12; the tests have been run on 3.12 only.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt

# Report on the seed data: what I'd alert on, and why
.\.venv\Scripts\python -m backend.cli

# Same as JSON, or try another limit
.\.venv\Scripts\python -m backend.cli --json
.\.venv\Scripts\python -m backend.cli --alert-after-min 25

# Any readings file; --now sets the evaluation time (shows silent sensors)
.\.venv\Scripts\python -m backend.cli path\to\readings.json --now 2026-07-20T09:30:00
```

### Send alerts to Discord (or Slack)

1. In Discord: your server → channel `#ops-alerts` → ⚙️ Edit Channel → Integrations → Webhooks → New Webhook → **Copy Webhook URL**.
2. `Copy-Item .env.example .env`, then set `ALERT_WEBHOOK_URL=<the URL>` in `.env`. The URL is a secret: `.env` is git-ignored.
3. Run:

```powershell
.\.venv\Scripts\python -m backend.cli --send        # plain rule-based messages
.\.venv\Scripts\python -m backend.cli --ai --send   # AI-worded, with the facts underneath
```

Without a URL, `--send` prints the messages to the console instead.

### AI summaries (optional)

Set `LLM_PROVIDER=groq` (and optionally `LLM_FALLBACK_PROVIDER=gemini`) with the matching API key and model in `.env`. The default, `mock`, needs no key. Only the alert's numbers are sent to the LLM. It has 5 s in total, and on any timeout, error or invented number the plain message is used. Example from the seed data (Groq):

> Truck T-01 temperature rose above 8.0 °C, reaching 9.6 °C with a rising trend and increasing humidity from 58 % to 61 %. Likely a warm air ingress or a failing reefer unit.

### Dashboard + API

```powershell
cd frontend; npm install; npm run build; cd ..
.\.venv\Scripts\python -m uvicorn backend.main:app --port 8000
# open http://127.0.0.1:8000   (API docs: http://127.0.0.1:8000/docs)
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /api/report` | Everything: trucks, alerts, notifications (with AI summaries), data quality |
| `GET /api/trucks`, `GET /api/trucks/{id}` | Fleet states; one truck's readings and alerts (for the chart) |
| `GET /api/alerts?status=open\|recovering\|closed` | Alerts with reasons |
| `POST /api/readings` | Upload `{"readings": [...], "replace": false}`. Re-evaluates and pushes new notifications once. Max 5,000 readings / 2 MB. |
| `POST /api/reset` | Reload the seed data (demo) |
| `GET /api/ranges`, `PUT /api/trucks/{id}/range` | Each truck's own range; empty = default (writes need `X-API-Key`) |

Sent notification ids are stored through SQLAlchemy (PostgreSQL via `DATABASE_URL`, otherwise a local SQLite file), so a restart never resends. The page refreshes every 20 s and shows a banner (and an optional desktop popup) when a new alert arrives.

### Each truck's own range (no code changes)

The first time a truck reports, it is **added to the database automatically with an empty range**, and an empty range means the default 2–8 °C applies, so a new truck is never unprotected. The dashboard's **Truck ranges** panel lists every registered truck with the time it was added. With the admin key, ops can fill in a truck's own range and alert time, or press "Use default" to clear it. Alerts are re-checked immediately.

Through the API: `GET /api/ranges`, `PUT /api/trucks/{id}/range` with `X-API-Key`, or send ranges for new trucks with an upload: `{"readings": [...], "trucks": [{"truck_id": "T-20", "min_safe_c": -25, "max_safe_c": -18}]}`. An upload never overwrites a range that is already set.

Example: give T-03 (5.8–7.0 °C) the range 0–5 °C and it alerts ("Above 5 °C…"), because that temperature is fine for medicine but too warm for chilled food.

### Ask about the fleet (MCP, in the dashboard)

Type a question in the dashboard's **Ask about the fleet** box, e.g. *"Which trucks are in trouble right now?"*. Our backend is an **MCP client**: it starts our MCP server (reading the live API), gives Groq the list of MCP tools, runs the tools Groq picks **through MCP**, and shows the answer with the tools it used. Every number and time in the answer must appear in the tool results, or the answer is withheld. Read-only, no key needed, 20 questions a minute. Endpoint: `POST /api/ask {"question": "..."}`.

Real answers from the seed data: *"T-04 is in recovering state. No other trucks are in trouble."* (tool: `get_fleet_status`) and *"Trucks with alerts today: T-01 (closed), T-05 (closed), T-04 (recovering)."* (tool: `get_all_alerts`).

### Ask an AI assistant (MCP)

`backend/mcp_server.py` is a **read-only** MCP server, registered in `.mcp.json` as `cold-chain`. In Claude Code, ask *"which trucks are in trouble right now?"*: it calls `get_fleet_status` / `get_open_alerts` and answers from the real data. No tool can close or silence an alert. The backup demo needs no AI at all: `npx @modelcontextprotocol/inspector .venv\Scripts\python -m backend.mcp_server`.

## Tests

**221 automated tests:** 212 Python (unit + integration), 7 frontend and 2 end-to-end in real Chrome. 97% Python coverage, plus live checks against Discord and Groq. Details, and what each level proves: **[docs/TESTING.md](docs/TESTING.md)**.

```powershell
.\.venv\Scripts\python -m pytest                       # unit + integration
cd frontend; npm run typecheck; npm test; cd ..        # UI
.\.venv\Scripts\python -m pytest tests_e2e --browser-channel chrome --no-cov   # end-to-end
.\.venv\Scripts\ruff check .; .\.venv\Scripts\pip-audit -r requirements.txt
```

Tests never make real network calls: the webhook is forced off and the LLM to mock. They caught 5 real bugs, including the HTTP library logging the secret webhook URL and the settings object printing the database password.

## Project layout

```
backend/engine/   loader, config (all thresholds), state machine — pure Python, no I/O
backend/notify/   console + webhook senders, send-once dispatcher, payload
backend/ai/       facts sent to the LLM, Groq/Gemini/mock clients, answer check
backend/cli.py    command-line report, --send, --ai
backend/report.py JSON form of the results (shared by CLI, API and MCP)
backend/api.py    FastAPI app (backend/main.py starts it); backend/store.py = send-once table
backend/mcp_server.py  read-only MCP tools for AI assistants
frontend/src/     React + TypeScript dashboard (SVG chart, no chart library)
tests/            unit + integration (pytest)   tests_e2e/  browser end-to-end
docs/             assumptions, testing, screenshots
```

## Known limitations

- **Batch, not streaming:** v1 evaluates a whole file. Live ingestion with late-data handling is designed (SOLUTION_DESIGN.md §4) but not built.
- **The API keeps readings in memory** (the send-once ids are in the database). A restart reloads the seed file, and a multi-instance deployment would need readings in the database too.
- **CLI `--send` remembers sent ids only for that run** (the API uses the database).
- **Ranges are per truck, set by ops**, not per shipment from a dispatch system.
- **Sensor-fault readings** (< −40 / > 50 °C) are flagged and ignored but don't notify anyone yet.
- **Viewing is open; changes need the admin key** (`X-API-Key`). There are no per-user logins yet; a real deployment would add SSO (e.g. an identity proxy in front of Cloud Run).
- The MCP server reads the seed file, not readings uploaded to the running API.
