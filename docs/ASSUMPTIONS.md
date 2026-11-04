# Assumptions

Working list of every assumption behind the alerting rules. Each one says what I assumed, why, and how it plays out on the seed data (`starter_kit/data/sensor_readings.json`, 54 readings, T-01 to T-05, 08:00 to 08:50 on 2026-07-20). The short version goes into SOLUTION_DESIGN.md.

## 1. Data

| # | Assumption | Why |
|---|---|---|
| D1 | Each reading has `truck_id`, `timestamp`, `temperature_c`, `humidity_pct`. A reading missing any of these, or with a value that isn't a number, is rejected and logged rather than guessed at. | Silently filling in a value could create or hide an alert. |
| D2 | `timestamp` is the time the **sensor** took the reading, not the time we received it. All durations come from this time. | Trucks lose signal and upload in bursts. Arrival time says nothing about how long the cargo was warm. |
| D3 | Timestamps without a time zone are treated as UTC. | The seed data has no zone. One fixed rule beats guessing per truck. |
| D4 | The file order means nothing. Readings are sorted per truck by `timestamp` before anything else happens. | T-05 is shuffled in the file. See section 4. |
| D5 | Two readings with the same `truck_id` and `timestamp` are duplicates: the first is kept and the second is dropped and counted. | A resent reading should not count twice. (There are none in the seed data, but retries make them likely.) |
| D6 | A temperature below −40 °C or above 50 °C is a sensor fault, not real cargo temperature. It is flagged and left out of the alert logic. | Reefer cargo can't get to those temperatures. A faulty sensor needs a different response (fix the sensor) than a warm truck (save the cargo). |
| D7 | Humidity is stored and shown but does not trigger alerts. A bad humidity value is dropped (set to empty) without rejecting the temperature reading. | Priya asked about temperature, and nobody gave a humidity limit. In the seed data humidity moves with temperature: correlation **0.89** across the fleet (T-01 0.98, T-02 0.88, T-03 0.94, T-04 0.91, T-05 0.98), range 48–62 %. So a humidity alert would just duplicate the temperature alert. **Caveat:** that's 54 synthetic readings over one hour. Real humidity can rise on its own (wet cargo, defrost condensation, leaks). We keep it to explain alerts, and moisture-sensitive cargo can get an optional humidity limit in its cargo profile later. |

## 2. Thresholds

| # | Assumption | Why |
|---|---|---|
| T1 | The safe range is **2–8 °C** for every truck. A reading is out of range if it is **above 8.0** or **below 2.0**. Exactly 8.0 is in range. | 2–8 °C is the standard range for refrigerated medicines, the strictest of Nimbus's cargo. Every reading in the seed data fits this profile. There's no cargo-type field, so one fleet-wide range is the honest default. |
| T2 | Below 2 °C counts as out of range too. | Freezing ruins vaccines and fresh food just as heat does. No seed reading goes below 2 °C, so this has no effect on the results here. |
| T3 | All thresholds live in one config file, not in the code. | Different cargo (frozen at −18 °C, produce at 0–5 °C) will need different numbers. Per-truck cargo profiles are future work. |

## 3. "Too long", gaps and repeat alerts

| # | Assumption | Why |
|---|---|---|
| A1 | An **episode** starts with the first out-of-range reading and lasts until the truck has been **back in range for 10 minutes** (A7). | One problem, one episode. A single dip under 8 °C doesn't end it. |
| A2 | **Episode duration = latest out-of-range timestamp − first out-of-range timestamp.** Not number of readings × 5 min. | The starter code counts readings, so a missing reading makes an episode look shorter (T-04 shows up as 15 min instead of 20). |
| A3 | Alert once an episode reaches **≥ 20 minutes**. | On the seed data any limit from 10 to 20 min is correct: 5 min alerts on T-04's early blip, and 25 min misses T-04. 20 is the most patient correct value. It ignores door openings and defrost cycles (5–15 min), still has 3 real readings if one is missing, and leaves ops time to act before pharma cargo spoils. |
| A4 | A gap of **up to 15 minutes** between two out-of-range readings does not break the episode. We assume the truck stayed warm during the gap. We never make up a temperature. | Missing one or two readings is normal. For pharma cargo, "assume it stayed warm" is the safe choice. |
| A5 | If a truck sends nothing for **more than 15 minutes**, it gets a separate **"sensor silent"** status. If it's out of range on **both** sides of the gap, the episode covers the gap and the alert says "includes N min without data". If one side is in range, the episode stops at the last out-of-range reading. | Silence is a problem of its own (dead sensor, no signal). Warm before and after most likely means warm throughout, so for medicine we assume the worse case and say so. |
| A6 | **One alert per episode.** While it is open, new readings update the duration and peak temperature. Only the "not improving" update (A11) or Critical (A8) sends anything new. | This is Priya's "not the same issue re-alerting every few minutes". |
| A7 | The truck is back to normal only after **10 minutes in range since the last out-of-range reading, confirmed by at least 2 in-range readings**. This applies in SUSPECT (resets the timer) and in RECOVERING (closes the alert). Out of range again before that = the **same** episode. | Without it, a truck reading 8.5 / 7.9 / 8.5 / 7.9 would never alert, and an open alert would flap open/closed. |
| A8 | Severity is **Warning** when the alert opens and becomes **Critical** if the temperature goes 3 °C or more out of range (≥ 11 °C) or the truck has been **out of range for 60 minutes** or more. Critical replaces any further "not improving" updates (A11). A very hot episode still waits the 20 minutes (one hot reading could be a glitch), then opens directly as Critical with a single notification. A silent-sensor notification is Critical if the truck was out of range when it went quiet, otherwise Warning. | An alert still open after an hour means the first one didn't get the problem fixed, so it goes further up. That hot usually means a failed reefer unit, so we don't wait. It's 3× the first limit, so clearly a separate step. |
| A9 | Notifications go to a **webhook URL** (`ALERT_WEBHOOK_URL`, Discord or Slack), only on open and on Critical. Each alert has a fixed id (truck + start time) and sent ids are saved, so nothing is sent twice. Timeout 5 s, 3 retries; on failure we log it and the alert stays on the web page. With no URL set (and in tests), alerts go to the console. | One HTTP POST is the simplest real notification. The URL is a secret, kept only in `.env` / Secret Manager. |
| A11 | **"Not improving" update, decided by the data with no acknowledge button.** Every 10 min after the alert opens, while it is still out of range and not yet Critical, compare the current temperature with the reading at the previous check (10 min earlier). If it hasn't moved back toward the safe range by at least **0.3 °C**, send one update: "T-04 still above 8 °C after 30 min and not improving (9.1 → 9.4 °C)". Improving, back in range (RECOVERING) or Critical: send nothing. Each update has a fixed id (alert id + check number). If a check falls in a data gap, it happens at the next out-of-range reading, and the following check is a full 10 min after that one. Seed data: T-01/T-05 drop from 9.3 to 8.2 °C between 08:30 and 08:40, and T-04 is back in range at 08:50, so there are no updates. | Without it, nothing is sent for 40 min between the alert (20 min) and Critical (60 min), even if nobody is fixing the truck. A timed repeat would bring back the noise, so the data decides: falling temperature means someone is on it, or it's recovering by itself. 0.3 °C is above normal sensor jitter (0.1–0.2 °C). At most 3 updates before Critical, and only for a truck that isn't getting better. We chose this over an acknowledge button: it needs no database or UI, works on the seed data, and stays inside the testable engine. |
| A10 | **Silent sensor.** No reading for 15 min: page shows "sensor silent", no notification. No reading for **30 min**: one webhook ("T-03 has sent no data for 30 min, last reading 6.8 °C at 08:35"). If the truck was **SUSPECT or ALERT** when it went quiet, notify at **15 min**. One notification per silence, with a fixed id (truck + last reading time). It closes when data returns. "Now" is the clock when live, and the latest timestamp in the file for a batch run (08:50 in the seed data, so no truck is silent there; a test covers it). | Short signal drops (tunnels, rural roads) are normal, so 15 min would be noisy. 30 min is still early enough for ops to call the driver. A warm truck that has gone dark is the riskiest case, so it gets the earlier warning. |

## 4. Out-of-order data (what to do when data isn't sorted)

**The problem.** T-05 has the same values as T-01, but the file lists them in this order: 08:00, 08:05, 08:20, 08:45, 08:35, 08:10, 08:50, 08:30, 08:15, 08:40, 08:25. The starter code walks the list as it comes. So it sees warm, cool, warm, warm, cool… and breaks one 30-minute problem into three short pieces (0, 5 and 15 min). **None reaches 20 minutes, so the truck that is actually as warm as T-01 gets no alert.**

**What we do (v1, whole file at once):**
1. Group readings by truck.
2. Sort each truck's readings by sensor `timestamp`.
3. Drop duplicates (D5) and flag bad values (D6).
4. Only then run the episode logic.

After sorting, T-05 looks exactly like T-01: out of range from 08:10 to 08:40, 30 minutes, one alert.

**What we'd do live, when readings arrive one at a time and some come late:**
- Keep each truck's recent readings in timestamp order. A late reading goes into its correct place, not at the end.
- When a reading arrives, recompute that truck's episode from its readings in time order. That way the result is the same whatever order readings arrive in.
- Accept late readings up to **60 minutes** old. Anything older is logged as "too late" and doesn't change any alert, so the alert history stays stable.
- If a late reading makes an episode long enough, the alert fires then, and the alert says the delay was caused by late data.
- If a late reading shows an alert that already went out should not have fired, we don't delete it. We add a note saying it was revised, so ops can see what changed and why.

## 5. Worked example: what happens at 25 minutes

"25 minutes" can mean two different questions. Both are answered here.

**(a) A truck has been out of range for 25 minutes (limit still 20).**

Example: out of range from 08:10 on, one reading every 5 minutes.

| Time | Out of range for | What happens |
|---|---|---|
| 08:10 | 0 min | Episode starts. No alert yet. |
| 08:20 | 10 min | Still waiting. |
| **08:30** | **20 min** | **Alert opens (Warning).** Ops gets one notification. |
| 08:35 | 25 min | **No new alert.** The open alert now shows 25 min and the current peak. |
| 08:40+ | 30+ min | Same: the alert updates quietly. It notifies again only if it becomes Critical (≥ 11 °C, or open 60 min). |
| back in range for 10 min | — | Alert closes. A later warm reading starts a new episode. |

So 25 minutes doesn't mean another alert. It's one open alert that has been warm for 25 minutes.

**(b) The panel changes the limit to 25 minutes.**

| Truck | Out of range for (by timestamp) | Limit 20 min | Limit 25 min |
|---|---|---|---|
| T-01 | 08:10 → 08:40 = 30 min | Alert at 08:30 | Alert at 08:35 |
| T-02 | single reading = 0 min | No | No |
| T-03 | never out of range | No | No |
| T-04 | 08:25 → 08:45 = 20 min (08:35 missing) | **Alert at 08:45** | **No alert** |
| T-05 | same as T-01 once sorted = 30 min | Alert at 08:30 | Alert at 08:35 |

**T-04 is the edge case.** It meets the 20-minute limit exactly. With a 25-minute limit it drops out, because it is back in range (7.2 °C) at 08:50. This trade-off is what the panel will ask about:
- A longer limit means fewer false alarms, but slower alerts and T-04 is missed.
- A shorter limit catches T-04 sooner, but more blips risk becoming alerts.
- For pharma cargo, missing a real problem costs more than one extra alert, so I'd keep 20 minutes. It's one config value, so Nimbus can change it per cargo type without touching code.

## 6. What the seed data gives with these assumptions

| Truck | Result | Reason |
|---|---|---|
| T-01 | **Alert** (Warning), 08:10–08:40, 30 min, peak 9.6 °C, closes at 08:50 | Sustained warming |
| T-02 | No alert | One-reading blip (8.9 °C at 08:10) |
| T-03 | No alert | Peaks at 7.0 °C, never out of range |
| T-04 | **Alert** (Warning) at 08:45, 08:25–08:45, 20 min, peak 9.1 °C; still RECOVERING when the data ends | Sustained, across a missing 08:35 reading. The earlier 08:05–08:10 blip stays SUSPECT and resets at 08:20 (10 min in range). |
| T-05 | **Alert** (Warning), same as T-01 | Only visible after sorting |

The starter code finds only T-01.

## 7. Open questions for Nimbus (we'd ask Priya)

- How long can a truck normally go without signal on its routes? (This sets the 30-min silent limit.)

- What cargo is on each truck, and what are its real limits?
- How fast does ops need to know? Is 20 minutes OK for pharma?
- How should ops be notified (dashboard, SMS, email), and who acknowledges an alert?
- How late can sensor data realistically arrive after a signal drop?
