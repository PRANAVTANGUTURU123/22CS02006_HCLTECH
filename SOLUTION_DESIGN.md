# Solution Design: Cold-Chain Alerting Engine (Nimbus Freight)

## 1. The problem in my words

Every reefer truck sends a temperature reading every ~5 minutes. Today ops gets an alert for every warm reading, so they get swamped, ignore the alerts, and miss real failures. Nimbus wants **one alert per real, sustained problem on a specific truck**. That means ignoring brief blips, not re-alerting while the same problem continues, and getting it right even when readings go missing or arrive out of order.

## 2. What the seed data shows

54 readings, 5 trucks, 08:00–08:50 on 2026-07-20. Each truck tests one thing:

| Truck | What happens | Starter code | My engine |
|---|---|---|---|
| T-01 | Above 8 °C from 08:10 to 08:40, peak 9.6 °C | Alert | **Alert** at 08:30, closed at 08:50 |
| T-02 | One reading of 8.9 °C at 08:10 | No alert | No alert (blip) |
| T-03 | Climbs to 7.0 °C, never out of range | No alert | No alert |
| T-04 | 5-min blip at 08:05, then above 8 °C from 08:25 to 08:45; **08:35 reading missing** | **Missed** (counts 4 readings × 5 = 15 min) | **Alert** at 08:45 (20 min by timestamps); still recovering at 08:50 |
| T-05 | Same values as T-01, **shuffled order in the file** | **Missed** (splits it into 0/5/15-min pieces) | **Alert** at 08:30 after sorting |

Result: 3 alerts, 3 notifications in total, and a reason for every truck, including why T-02 and T-03 did *not* alert.

## 3. Assumptions

Full list with reasons: `docs/ASSUMPTIONS.md`. All numbers live in one config file (`backend/engine/config.py`).

- **Safe range 2–8 °C** for the whole fleet (the standard range for refrigerated medicines, Nimbus's strictest cargo; every reading fits it). Above 8.0 or below 2.0 is out of range; exactly 8.0 is fine. There's no cargo-type field, so **every truck defaults to the strictest profile**. Ranges are not hard-coded: ops assign each truck a **cargo profile** from the dashboard (presets `pharma` 2–8, `chilled` 0–5, `frozen` −25 to −18, `ambient` 15–25 °C, or their own). A truck with no assignment, including a brand-new one, automatically uses the default, so it is never unprotected. Profiles and assignments are stored in the database, changes need the admin key, and the fleet is re-evaluated immediately.
- **Time comes from the sensor timestamp**, not from when we received the reading. Timestamps without a zone are treated as UTC.
- **Alert after 20 minutes out of range** (the same for every truck in v1). On the seed data any limit from 10 to 20 min gives the right answer: 5 min alerts on T-04's early blip, and 25 min misses T-04. I picked 20, the most patient value that is still correct. It ignores door openings and defrost cycles (5–15 min), still has 3 real readings if one is missing, and leaves ops time to act before pharma cargo spoils.
- **Escalate to Critical after 60 minutes out of range, or 3 °C or more out of range (≥ 11 °C).** A truck still out of range after an hour means the first alert didn't get the problem fixed, so it goes further up. A truck that hot is probably a failed reefer unit, so it doesn't wait for the hour: it still needs the 20 minutes (one hot reading could be a glitch), then opens straight as Critical.
- **Between opening and Critical, the data decides whether to send an update; there are no timed repeats.** Every 10 min while the alert is open and the truck is still out of range, the current temperature is compared with the one 10 min earlier. If it hasn't improved by at least 0.3 °C (more than sensor jitter), one update goes out: "T-04 still above 8 °C after 30 min and not improving (9.1 → 9.4 °C)". If it is improving, back in range, or already Critical, nothing is sent. That's at most one update per 10 min, only for a truck nobody has fixed, and it closes the 40-minute silent stretch between the alert (20 min) and Critical (60 min). On the seed data every alert was already improving, so there are no updates (still 3 notifications).
- **A truck is back to normal only after 10 minutes in range**, confirmed by at least 2 readings. One dip just under 8 °C doesn't reset anything. This applies before the alert (otherwise a truck reading 8.5 / 7.9 / 8.5 / 7.9 would never alert) and after it (otherwise the alert flaps open/closed).
- **Missing readings:** we never make up a temperature. A gap of up to 15 min keeps the episode going (T-04). A longer gap is bridged only if the truck is out of range on both sides, and the alert says "includes missing data". Otherwise a not-yet-alerted episode restarts. **An open alert never closes because of missing data**, only on confirmed in-range readings.
- **A silent sensor is not treated as a hot truck.** That would bring back the noise, send ops to fix the wrong thing, and put made-up data in a safety record. It gets its own status and notification. After 15 min with no data the page shows "sensor silent". After **30 min** one notification goes out. If the truck was already out of range when it went quiet, the notification goes out at **15 min** as Critical, because a warm truck we can't see is the riskiest case. In the seed file "now" is the latest timestamp (08:50) and every truck reports up to then, so none is silent. Tests cover it.
- **Duplicates** (same truck and timestamp) are dropped, keeping the first. **Impossible values** (< −40 °C or > 50 °C) are flagged as sensor faults and not used. Malformed readings are rejected with a reason.
- **Humidity is stored and shown but doesn't trigger alerts.** In this data it moves with temperature: the correlation is 0.89 across the fleet and 0.88–0.98 per truck, with a range of 48–62 %. A humidity alert would duplicate the temperature alert and add noise, and the brief gives no humidity limit. That's from 54 synthetic readings over one hour, not proof for real trucks: wet cargo, condensation or a leak can raise humidity on its own. So it stays available to explain alerts ("humidity also rose 55→62 %, suggesting warm air getting in"), and moisture-sensitive cargo could get an optional humidity limit in its cargo profile.

## 4. Why sorting is needed

The rules depend on order: "out of range for 20 minutes *in a row*" only makes sense if readings are in time order. Trucks lose signal and upload in bursts, and retries arrive late, so the order they arrive in is not the order they were taken in. T-05 shows the failure. Read in file order (08:00, 08:05, 08:20, 08:45, 08:35, 08:10, …), it seems to go warm, cool, warm, so one 30-minute problem looks like three short ones and **no alert fires**. Sorted by sensor time, T-05 is the same as T-01 and alerts. A test shuffles the whole file 5 ways and gets the same result each time.

- **v1 (whole file):** group by truck, sort by timestamp, drop duplicates, then run the rules.
- **Live:** a late reading is put in its correct place and that truck is recalculated, so the result doesn't depend on arrival order. Readings more than 60 min late are logged but don't change alerts. An alert already sent is never deleted; it is marked "revised" with the reason.

## 5. The design

**The whole system.** The rules decide, and everything else only presents or explains what they decided:

```
 readings (file / upload)
        │
        ▼
 1. Loader          check fields, flag impossible temps, drop duplicates, SORT by sensor time
        │
        ▼
 2. State machine   one per truck: OK → SUSPECT → ALERT → RECOVERING → OK   (pure Python, no I/O)
        │
        ▼
 alerts + notifications (each with a fixed id, so sent only once)
        │
   ┌────┴─────┬──────────────┬───────────────┬──────────────┐
   ▼          ▼              ▼               ▼              ▼
  CLI      Webhook       AI summary       Web page      MCP server
 report    (push)       (wording only)    (screen)    (ask questions)
```

**The state machine (per truck):**

```
           out of range                     20 min out of range
   OK ─────────────────► SUSPECT ─────────────────────────► ALERT ──► notify (Warning)
   ▲                        │                                │  ▲     60 min or ≥ 11 °C ──► notify (Critical)
   │  10 min back in range  │                       in range │  │ out of range again
   └────────────────────────┘                                ▼  │ (same alert, timer continues)
   ▲                                                      RECOVERING
   └────────────── 10 min back in range: alert closed ────────┘
```

| State | Meaning | Notifies? |
|---|---|---|
| OK | In range | No |
| SUSPECT | Out of range, timer running | No (T-02 and T-04's early blip end here) |
| ALERT | 20 min reached; duration and peak keep updating | **Once** when it opens; an update every 10 min **only if not improving**; **once** if Critical |
| RECOVERING | Back in range, waiting 10 min before closing | No |

"Sensor silent" is a separate flag shown next to the state, so a dead sensor never hides or fakes a temperature alert. It has its own single notification and clears when data comes back. Short episodes that never reached 20 minutes are kept too, so the report can say *why* a truck didn't alert ("one reading out of range (8.9 °C at 08:10), under the 20-min limit").

## 6. How ops hears about it

There are three channels, each with one job:

| Channel | Direction | Job |
|---|---|---|
| **Web page** | Ops looks at it | The main screen: every truck's state, open and closed alerts with reasons, temperature chart per truck. Refreshes automatically. |
| **Webhook** (Discord / Slack channel, e.g. `#ops-alerts`) | We push to ops | Reaches ops when nobody is looking at the page. Sent only when an alert opens, isn't improving after another 10 min, turns Critical, or a sensor goes silent. |
| **MCP server** | Ops asks an AI | "Which trucks are in trouble right now?" Read-only (§7). It doesn't send anything. |

**Webhook details.** The notifier POSTs JSON to `ALERT_WEBHOOK_URL` with both `text` (Slack) and `content` (Discord), plus the alert's fields. Example: *"WARNING: Truck T-04 above 8 °C for 20 min (08:25–08:45), peak 9.1 °C. Includes missing data: 08:30–08:40 (10 min)."*

- **No repeats:** each notification has a fixed id (e.g. `T-04:2026-07-20T08:25:00+00:00:opened`), and sent ids are saved, so nothing is sent twice, even after a restart.
- **Failures:** 5-second timeout and 3 retries with growing waits. If it still fails, we log it and carry on. The alert stays visible on the web page.
- **Security:** the URL is a secret, because its token is part of the path (`.env` and Secret Manager only, never committed or logged). HTTPS only. Errors never echo it, and the HTTP library's own request logging is turned down, because a test caught it writing the full URL to the log. The message holds truck id, times and temperatures, with no customer or cargo details.
- If no URL is set (and in all tests), notifications go to the console and log.
- **Why not SMS:** SMS costs money, needs a registered sender in India, and would add a second noisy channel. The notifier has one interface with swappable senders, so SMS for Critical alerts only can be added later without touching the engine.

## 7. AI features (bonus): rules decide, AI explains

- **Alert summary:** an LLM (Groq) turns an alert's numbers into one plain sentence with a *likely* cause, e.g. "steady rise with humidity up, so likely a reefer problem rather than a door opening". Only numbers go in (truck id, times, temperatures, humidity), never customer or cargo data. The answer is checked: it must name the right truck, stay short, and contain no temperature or time that wasn't in the alert. If the check fails, or the call takes more than 5 s or errors, the plain rule-based message is sent instead, so an alert is never delayed or lost because of the AI. One call per alert, cached. Shown labelled as "AI summary", next to the exact numbers. Tests run in mock mode.
- **MCP server:** read-only tools (`get_fleet_status`, `get_open_alerts`, `get_truck_history`, `get_alert`) over the same engine, so an ops manager can ask an AI assistant "which trucks are in trouble right now?" and get answers built from real data. No tool can close or silence an alert; that stays a human action. Inputs are checked (known truck ids, capped time windows). It runs locally over stdio; a hosted version would need an API key.

## 8. Technology

| Part | Choice | Why |
|---|---|---|
| Engine and CLI | Python 3.12, plain functions plus a config file | Simple, easy to test, runs without a server |
| API | FastAPI + Pydantic | Validates uploaded readings; `/health`, `/api/trucks`, `/api/alerts`, upload endpoint |
| Storage | SQLAlchemy + PostgreSQL 16 (Cloud SQL in production) | Keeps alert history and sent-notification ids across restarts; portable SQL with bound parameters |
| Webhook | httpx + tenacity | Timeouts and retries out of the box |
| Web page | React + TypeScript (Vite) | The build fails if the page and the API disagree about the data shape, rather than showing a blank screen in the demo |
| AI | Groq LLM (Gemini as fallback) + official `mcp` Python SDK | See §7; mock mode in tests |
| Tests | pytest (engine, CLI, API, webhook with a fake sender), vitest (UI) | One test per truck story plus every edge case |
| Deploy | Google Cloud Run (asia-south1) + Cloud SQL; Azure App Service as backup | Already set up; secrets come from Secret Manager |

## 9. Trade-offs I considered

- **Alert on every reading** (today): simple, but it causes the noise problem.
- **Rolling average:** smooths out blips but hides short, sharp spikes, and still flaps around the limit.
- **ML anomaly detection:** 54 readings isn't enough to train on, and ops can't see *why* it alerted.
- **Chosen, a duration-based state machine:** easy to explain ("20 minutes above 8 °C"), predictable, testable, and each number can be tuned per cargo.
- **Costs of this choice:** an alert can't fire sooner than 20 minutes, and a truck hovering near 8 °C will alert (on purpose, for pharma). A 20-minute episode sits exactly on the limit (T-04), so we test that edge explicitly; with a 25-minute limit T-04 would be missed.
- **AI kept out of the decision:** the LLM could spot patterns, but an alert must be explainable and repeatable. So the LLM only words messages, and every number it writes is checked.

## 10. With more time, data or budget

- **Cargo profiles per shipment:** today profiles are set per truck by ops. Ideally the range would come with each shipment from Nimbus's dispatch system, plus an optional humidity limit.
- **Sensor-fault notification:** today impossible readings are flagged and ignored, but nobody is notified, and a truck sending *only* faulty readings drops out of the report. Fix: a "sensor fault" status with one notification after 15 min of bad readings.
- Streaming ingestion (Pub/Sub) instead of file upload; late-data handling as in §4; on-call escalation (who gets an update if nobody fixes the truck).
- "Trending warm" early warning (T-03's slow climb), and door-open or defrost signals from the truck to explain blips.
- SMS for Critical alerts only; a hosted MCP server with authentication.
