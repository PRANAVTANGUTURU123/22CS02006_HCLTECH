# Cold-Chain Alerting Engine

Nimbus Freight's ops team gets an alert for every warm reading, so they ignore them and miss real failures. This engine sends **one alert per real, sustained temperature problem on a truck**: no alerts for brief blips, no repeats for the same problem, and correct even when readings are missing or arrive out of order.

- **Live app:** https://cold-chain-alerts-78623167451.asia-south1.run.app
- **Code:** https://github.com/PRANAVTANGUTURU123/22CS02006_HCLTECH
- **Design:** [SOLUTION_DESIGN.md](SOLUTION_DESIGN.md)

## Result on the seed data

| Truck | What happens | Result |
|---|---|---|
| T-01 | Above 8 °C 08:10–08:40, peak 9.6 °C | **Alert** at 08:30, closed at 08:50 |
| T-02 | One reading of 8.9 °C | No alert (blip) |
| T-03 | Climbs to 7.0 °C, never above 8 | No alert |
| T-04 | Above 8 °C 08:25–08:45, 08:35 reading missing | **Alert** at 08:45 |
| T-05 | Same as T-01, shuffled in the file | **Alert** at 08:30, after sorting |

21 warm readings → **3 alerts**.

## How it works

1. **Loader:** validates every reading, drops duplicates and sorts each truck's readings by sensor time.
2. **State machine per truck:** OK → SUSPECT → ALERT → RECOVERING → OK. Alert after **20 min** outside **2–8 °C**; back to normal after **10 min** in range; gaps up to 15 min are bridged and reported.
3. **Notifications:** each alert is sent **once** to Discord; an update only if the temperature isn't improving; Critical at 60 min or ≥ 11 °C.
4. **AI (bonus):** Groq writes each alert in plain words, and the **Ask about the fleet** box answers questions through our **MCP server**. The rules decide; the AI only explains, and every number it writes is checked.

---

## Demo on Google Cloud

Open https://cold-chain-alerts-78623167451.asia-south1.run.app

1. **Fleet:** T-04 shows **Recovering**; the others are **OK**. The red banner names the open alert.
2. **Chart:** click **T-04** for its temperature chart: green band = safe range, red = alert period, with the 08:30–08:40 data gap.
3. **Alerts:** each card shows the reason and the AI summary.
4. **Change a truck's range:** in **Truck ranges**, click **T-03**, enter min `0` and max `5`, enter the admin key, click **Save range**. A new alert appears: **T-03 "Above 5 °C for 50 min (08:00–08:50), peak 7 °C."** Click **Use default** to undo.

The same data from PowerShell:

```powershell
$U = "https://cold-chain-alerts-78623167451.asia-south1.run.app"
Invoke-RestMethod "$U/health"
Invoke-RestMethod "$U/api/trucks" | Select-Object truck_id, state, last_temperature_c | Format-Table
Invoke-RestMethod "$U/api/alerts" | Select-Object truck_id, status, reason | Format-Table -Wrap
Invoke-RestMethod "$U/api/alerts?status=recovering" | Select-Object truck_id, reason | Format-Table -Wrap
```

Expected: `status ok`; T-04 **recovering**, T-01 and T-05 **closed**, each with its reason.

---

## Demo on your machine

### Setup (once)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
```

Create a `.env` file in the project folder:

```
ALERT_WEBHOOK_URL=<Discord webhook URL>
LLM_PROVIDER=groq
GROQ_API_KEY=<Groq key>
GROQ_MODEL=openai/gpt-oss-20b
GROQ_REASONING_EFFORT=low
ADMIN_API_KEY=<admin key>
```

All of these are optional: without them, alerts print in the terminal, the AI uses mock wording and changes need no key.

### 1. The 3 expected alerts

```powershell
.\.venv\Scripts\python -m backend.cli
```

Expected:

```
ALERTS (3)
  [WARNING] T-01  Above 8 °C for 30 min (08:10–08:40), peak 9.6 °C.
  [WARNING] T-05  Above 8 °C for 30 min (08:10–08:40), peak 9.6 °C.
  [WARNING] T-04  Above 8 °C for 20 min (08:25–08:45), peak 9.1 °C. Includes missing data: 08:30–08:40 (10 min).
NO ALERT (2)
  T-02  one reading out of range (8.9 °C at 08:10), under the 20-min limit
  T-03  never outside 2–8 °C
```

### 2. Send the 3 alerts to Discord

```powershell
.\.venv\Scripts\python -m backend.cli --ai --send
```

Expected: `Delivered 3 of 3 notifications via webhook.` and 3 messages in `#ops-alerts`, each in plain words with the exact facts underneath. Use `--send` without `--ai` for the plain rule-based messages.

### 3. Change the range or the time limit, and send to Discord

```powershell
.\.venv\Scripts\python -m backend.cli --alert-after-min 25 --ai --send
.\.venv\Scripts\python -m backend.cli --max-safe-c 9.5 --ai --send
.\.venv\Scripts\python -m backend.cli --min-safe-c 0 --max-safe-c 5 --ai --send
```

| Command | Alerts | Discord |
|---|---|---|
| `--alert-after-min 25` | 2: T-01, T-05. T-04 was out of range for exactly 20 min, so a 25-min limit misses it | 2 messages |
| `--max-safe-c 9.5` | 0: only one reading (9.6 °C) is above 9.5 | nothing |
| `--min-safe-c 0 --max-safe-c 5` | 5: every truck (4 Critical, T-03 Warning) | 6 messages (5 alerts + 1 "not improving" update for T-03) |

### 4. The dashboard

```powershell
.\.venv\Scripts\python -m uvicorn backend.main:app --port 8000
```

Open http://127.0.0.1:8000 and follow the same steps as the cloud demo. In **Ask about the fleet**, type:

- `Which trucks are in trouble right now?` → "T-04 is in recovering state…" (MCP tool: `get_fleet_status`)
- `Which trucks had alerts today and are they closed?` → "T-01 (closed), T-05 (closed), T-04 (recovering)." (MCP tool: `get_all_alerts`)

### 5. A new truck reports a problem

With the server running, in a second PowerShell terminal:

```powershell
$readings = 30,35,40,45,50 | ForEach-Object { @{ truck_id = "T-09"; timestamp = "2026-07-20T08:$($_):00"; temperature_c = 9.4; humidity_pct = 60 } }
$body = @{ readings = @($readings) } | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/readings -Headers @{ "X-API-Key" = "<admin key>" } -ContentType "application/json" -Body $body
```

T-09 is registered automatically, alerts on the dashboard and is sent to Discord. Run it again: nothing new is sent, because each alert goes out once.

Reset the demo data:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/reset -Headers @{ "X-API-Key" = "<admin key>" }
```

---

## API

| Endpoint | Purpose | Admin key |
|---|---|---|
| `GET /health` | Liveness | No |
| `GET /api/report` | Trucks, alerts, notifications with AI summaries | No |
| `GET /api/trucks`, `GET /api/trucks/{id}` | Fleet states; one truck's readings and alerts | No |
| `GET /api/alerts?status=open\|recovering\|closed` | Alerts with reasons | No |
| `GET /api/ranges` | Every truck's own range (empty = default 2–8 °C) | No |
| `POST /api/ask` | Question answered by Groq through our MCP tools | No |
| `POST /api/readings` | Upload readings (max 5,000 / 2 MB) | Yes |
| `PUT /api/trucks/{id}/range` | Set or clear a truck's range | Yes |
| `POST /api/reset` | Reload the seed data | Yes |

Interactive docs: `/docs`.

## MCP server

`backend/mcp_server.py` exposes 5 **read-only** tools: `get_fleet_status`, `get_open_alerts`, `get_all_alerts`, `get_truck_history`, `get_alert`. They are used by the dashboard's Ask box (our backend is the MCP client and Groq picks the tool), by Claude Code (`.mcp.json`) and by the MCP Inspector:

```powershell
npx @modelcontextprotocol/inspector .venv\Scripts\python -m backend.mcp_server
```

## Security

- API keys, webhook URL and database URL live in Secret Manager (cloud) or `.env` (local), never in code, Git or logs.
- Anyone can view; every change needs the admin key. The Ask box is limited to 20 questions a minute.
- Every reading is validated; uploads are size-limited; SQL only through bound parameters.
- Only numbers are sent to the LLM; invented numbers are rejected; MCP tools are read-only.

## Tech stack

Python (FastAPI, Pydantic, SQLAlchemy, PostgreSQL), React + TypeScript + Vite, Groq `openai/gpt-oss-20b` with Gemini fallback, MCP Python SDK, Discord webhook, Google Cloud Run + Cloud SQL + Secret Manager; tested with pytest, vitest and Playwright.

## Limits and next steps

- **The Ask box works locally but not yet on Cloud Run** (the MCP helper process needs the full environment there).
- Readings are processed in batches and kept in memory in the API; next: streaming ingestion and readings stored in PostgreSQL.
- Ranges are set per truck; next: a range per shipment from the dispatch system.
- Impossible sensor readings are flagged but not notified; next: a "sensor fault" alert.
- One shared admin key; next: single sign-on.
