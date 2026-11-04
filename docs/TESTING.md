# Testing & Verification

**201 automated tests**: 194 Python (97% line coverage), 5 frontend and 2 end-to-end browser tests. On top of those, static checks, dependency audits and live checks against the real Discord and Groq. **No automated test touches the network:** `tests/conftest.py` forces the webhook off and the LLM to mock mode, even if `.env` holds real keys.

## How to run

```powershell
.\.venv\Scripts\python -m pytest                                   # unit + integration, with coverage
cd frontend; npm run typecheck; npm test; npm run build; cd ..       # types, UI tests, build
.\.venv\Scripts\python -m pytest tests_e2e --browser-channel chrome --no-cov   # end-to-end (needs the build)
.\.venv\Scripts\ruff check .                                       # lint + security rules (bandit "S")
.\.venv\Scripts\pip-audit -r requirements.txt; cd frontend; npm audit --omit=dev
```

## The test pyramid

| Level | What it means here | Files | Tests |
|---|---|---|---|
| **Unit** | One piece on its own, with fakes for anything outside it (no real network, files or browser) | `test_loader`, `test_state_machine`, `test_notify`, `test_ai` | 133 |
| **Integration** | Several real pieces together: the real seed file, the real HTTP layer, the real database, the real MCP server | `test_seed_data`, `test_cli`, `test_api`, `test_mcp_server`, frontend `App.test.tsx` | 45 + 3 |
| **End-to-end** | The whole system as a user sees it: real server, real database, built dashboard, real Chrome | `tests_e2e/test_dashboard.py` | 2 |
| **Live checks** (manual, recorded) | Against the real outside services | Discord webhook, Groq LLM, screenshot | — |

### Unit tests (133)

**`test_loader.py` (34): turning raw JSON into clean, sorted readings**
- Sorting by sensor time; naive timestamps treated as UTC; `+05:30` converted to UTC.
- Duplicates: an exact repeat, and the same moment written in two time zones. The first one is kept.
- Impossible temperatures (−41, 50.1, 999 °C) become sensor faults. The limits themselves (−40, 50) are valid.
- 12 kinds of bad input are each rejected with a reason: not an object, missing or blank truck id, an over-long id, missing or garbage timestamp, a number given as a timestamp, a temperature given as a string, `true`, `NaN`, `Infinity`.
- Bad humidity is dropped without losing the temperature reading.

**`test_state_machine.py` (45): the alerting rules, one rule per test**
- **Blips and limits:** a 1-reading blip never alerts; 15 min doesn't alert; **exactly 20 min alerts**; **exactly 8.0 °C and 2.0 °C are in range**; too cold alerts; duration comes from timestamps, not reading counts.
- **Recovery:** the 8.5/7.9 sawtooth still alerts; SUSPECT resets after 10 min in range; one in-range reading doesn't close an alert; the alert closes after 10 min and 2 readings; flapping stays the same alert; a new episode gets a new alert.
- **Gaps:** one missing reading is bridged and reported (T-04); a 15-min gap still bridges; a long gap with both sides warm is bridged and flagged; a long gap with one side in range resets SUSPECT; an open alert never closes because of missing data.
- **Silent sensor:** 20 min quiet shows on the page but doesn't notify; 30 min notifies once; a warm truck going quiet notifies at 15 min as Critical.
- **Severity and updates:** opens directly as Critical at ≥ 11 °C; escalates at 60 min exactly once; a stuck truck gets "not improving" updates at 30/40/50 min, then Critical at 60; a 0.2 °C wobble counts as not improving, while **exactly 0.3 °C counts as improving**; no update while recovering; a check after a gap isn't followed by a second one 5 min later.
- **Config:** 7 bad settings are refused; the limit is configurable (25 min drops T-04).

**`test_notify.py` (26): delivery to Discord/Slack, using a fake HTTP transport**
- The payload works for Discord (`content`) and Slack (`text`), with a red icon for Critical, the AI text with the facts underneath, and text cut to Discord's limit.
- Retries on 500/503/429/connection error/timeout. **No** retry on 400/401/404. Gives up after 3 attempts.
- Send-once: a second run skips ids already sent; a failed send is retried next run and doesn't block the others; a broken AI never blocks an alert.
- **Security:** HTTPS only; the URL never appears in errors, `repr` or logs. That last test **found a real leak**: the HTTP library was logging the full webhook URL.

**`test_ai.py` (28): AI wording and its safety check**
- The facts sent to the LLM are numbers only, as of the notification time (no later readings).
- The check rejects an invented temperature (9.8), an invented time (08:20), an invented duration (25), the wrong truck, text over 300 characters, and empty text.
- Caching (one LLM call per notification), the timeout or error fallback to the plain message, the Groq/Gemini request shapes, API keys only in headers (never in URLs or error text), and the primary-to-fallback switch inside the 5 s budget.

### Integration tests (45 Python + 3 frontend)

- **`test_seed_data.py` (11):** the real seed file through the loader and engine. **One test per truck story** (T-01 to T-05). Shuffling the whole file 5 ways gives an identical result. A 25-minute limit drops T-04. The starter code finds only T-01, which documents why it was replaced.
- **`test_cli.py` (8):** the command-line tool as a user runs it: the text report, `--json`, overrides, `--now` (silent sensors), listing of data problems, and clean errors (exit code 2) for a missing file, a bad time or a bad config.
- **`test_api.py` (16):** real HTTP requests through FastAPI to the engine and a **real SQLite database**.
  - All the endpoints.
  - **Upload → alert → exactly one push.** Re-uploading sends nothing.
  - **Sent ids survive a server restart.**
  - Per-reading rejection; uploads that move time forward raise silent-sensor notices.
  - 422 for malformed bodies; **413 for over 5,000 readings or over 2 MB.**
- **`test_mcp_server.py` (10):** the MCP tools through the real MCP server object.
  - "Which trucks are in trouble" gives T-04 recovering.
  - Exactly 5 read-only tools are exposed.
  - The history window is capped (0, −5 and 1441 minutes are refused); an unknown truck is refused, including an SQL-injection-style string.
- **`frontend/src/App.test.tsx` (3):** the dashboard component with the API mocked. It checks the banner, state badges, reasons, the **labelled AI summary**, the chart on click, and the error message when the API is down.

### Cargo profiles (`test_profiles.py`, 16: unit + integration)
- Presets are added on first start; **a never-seen truck gets the default `pharma` 2–8 °C**; assign and unassign; custom profiles; settings survive a restart.
- Refused: a bad name, min above max, a range outside the sensor limits, a zero alert time, an unknown profile, an over-long truck id.
- The same −10 °C reading is "too warm" for frozen and "too cold" for pharma.
- Through the API: assigning T-03 to `chilled` immediately creates an "Above 5 °C" alert; widening `pharma` to 9.7 °C clears all alerts; writes without the key get 401; bad input gets 422.
- UI tests: the panel lists profiles with the default marked, sends the admin key in the header, and shows "Wrong or missing admin key" on a 401.

### End-to-end tests (2)

`tests_e2e/test_dashboard.py` starts the **real server** (real database, built dashboard) and drives **Chrome**:
1. The page shows the open-alert banner, 3 alerts with AI summaries, and T-04's chart with the alert period shaded.
2. **The full journey:** upload warm readings for a new truck through the API → the engine opens an alert → **exactly one** push goes to the (fake) Discord sender, AI-worded → the dashboard shows T-09 in **Alert** → re-uploading sends nothing.

### Live checks (manual, against the real services)

| Check | Result |
|---|---|
| `python -m backend.cli --send` → real Discord webhook | 3/3 delivered to `#ops-alerts` ([screenshot](images/discord-alerts.png)) |
| `python -m backend.cli --ai` → real Groq | 3 summaries, all passed the invented-number check |
| Server + PostgreSQL 16 + Chrome | Dashboard renders with real Groq summaries ([screenshot](images/dashboard.png)) |

## Static checks and security

| Check | Result |
|---|---|
| `ruff check .` (style, bugs, and the bandit `S` security rules) | All checks passed |
| `npm run typecheck` (TypeScript strict) | No errors; the page and API types agree |
| `pip-audit -r requirements.txt` | No known vulnerabilities |
| `npm audit --omit=dev` | 0 vulnerabilities |
| Secret scan of every commit (API keys, webhook URL, DB password) | None committed; `.env` is git-ignored |

## Bugs the tests caught (and fixed)

1. **Floating-point:** a drop from 9.6 to 9.3 °C computed as 0.2999 °C, so a real improvement looked like "not improving" → the value is now rounded before comparing.
2. **Gap timing:** after a data gap, a late "is it improving?" check was followed by another only 5 min later → the next check is now a full 10 min after the actual one.
3. **Secret leak:** httpx logged the full webhook URL, which contains the token → its logger is held at WARNING.
4. **Test isolation:** importing the API module built the app with the developer's real `.env` (live Groq calls) → app creation moved to `backend/main.py`.
5. **Database password in `repr`:** the settings object printed the full `DATABASE_URL`, password included → it is now a secret value too.

## Not covered yet

- Load and concurrency testing (one process; the `Fleet` lock is tested only by design, not under stress).
- Real PostgreSQL in CI: the API tests use SQLite through the same SQLAlchemy code. PostgreSQL was checked live, but not in an automated test.
- Mobile layout and accessibility audits of the dashboard.
