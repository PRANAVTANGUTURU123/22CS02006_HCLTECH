import { useCallback, useEffect, useRef, useState } from "react";
import { fetchReport, fetchTruck, hhmm } from "./api";
import { TruckRanges } from "./TruckRanges";
import { TempChart } from "./TempChart";
import type { Alert, Report, TruckDetail } from "./types";

const REFRESH_MS = 20_000;

const STATE_LABEL: Record<string, string> = {
  ok: "OK",
  suspect: "Suspect",
  alert: "Alert",
  recovering: "Recovering",
};

function AlertCard({ alert, summary }: { alert: Alert; summary: string | null }) {
  return (
    <li className={`alert-card ${alert.severity} ${alert.status}`}>
      <div className="alert-head">
        <strong>{alert.truck_id}</strong>
        <span className={`pill ${alert.severity}`}>{alert.severity}</span>
        <span className={`pill status-${alert.status}`}>{alert.status}</span>
        <span className="muted">fired {hhmm(alert.opened_at)}</span>
      </div>
      <p>{alert.reason}</p>
      {summary && (
        <p className="ai">
          <span className="ai-label">AI summary</span> {summary}
        </p>
      )}
      <p className="muted small">
        {alert.closed_at ? `Closed ${hhmm(alert.closed_at)}` : "Still open"}
        {alert.updates_sent > 0 && ` · ${alert.updates_sent} not-improving update(s)`}
        {alert.escalated_at && ` · Critical since ${hhmm(alert.escalated_at)}`}
      </p>
    </li>
  );
}

export default function App() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TruckDetail | null>(null);
  const [newIds, setNewIds] = useState<string[]>([]);
  const seen = useRef<Set<string> | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchReport();
      setError(null);
      setReport(data);
      const ids = data.notifications.map((n) => n.notification_id);
      if (seen.current === null) {
        seen.current = new Set(ids); // first load: nothing is "new"
      } else {
        const fresh = data.notifications.filter((n) => !seen.current!.has(n.notification_id));
        fresh.forEach((n) => seen.current!.add(n.notification_id));
        if (fresh.length) {
          setNewIds(fresh.map((n) => n.notification_id));
          if ("Notification" in window && Notification.permission === "granted") {
            fresh.forEach((n) => new Notification(`Truck ${n.truck_id}`, { body: n.ai_summary ?? n.message }));
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the report");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    fetchTruck(selected).then(setDetail, () => setDetail(null));
  }, [selected, report]);

  if (error && !report) return <main className="page"><p className="error">{error}</p></main>;
  if (!report) return <main className="page"><p className="muted">Loading…</p></main>;

  const { min_safe_c: min, max_safe_c: max, alert_after_min } = report.config;
  const open = report.alerts.filter((a) => a.status !== "closed");
  const summaryFor = (alertId: string) =>
    report.notifications.find((n) => n.alert_id === alertId && n.ai_summary)?.ai_summary ?? null;

  return (
    <main className="page">
      <header>
        <div>
          <h1>Cold-chain alerts</h1>
          <p className="muted">
            Nimbus Freight · default range {min}–{max} °C · alert after {alert_after_min} min out of
            range
          </p>
        </div>
        {"Notification" in window && Notification.permission === "default" && (
          <button onClick={() => void Notification.requestPermission()}>Enable desktop popups</button>
        )}
      </header>

      {error && <p className="error">{error} (showing last data)</p>}

      {open.length > 0 && (
        <div className={`banner ${newIds.length ? "flash" : ""}`} role="alert">
          {open.length} open alert{open.length > 1 ? "s" : ""}:{" "}
          {open.map((a) => `${a.truck_id} (${a.status}, peak ${a.peak_c} °C)`).join(" · ")}
          {newIds.length > 0 && <strong> — new since you opened this page</strong>}
        </div>
      )}

      <section>
        <h2>Fleet</h2>
        <table className="fleet">
          <thead>
            <tr>
              <th>Truck</th>
              <th>State</th>
              <th>Range</th>
              <th>Last reading</th>
              <th>Humidity</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {report.trucks.map((t) => (
              <tr
                key={t.truck_id}
                className={selected === t.truck_id ? "selected" : ""}
                onClick={() => setSelected(t.truck_id)}
              >
                <td><strong>{t.truck_id}</strong></td>
                <td>
                  <span className={`badge ${t.state}`}>{STATE_LABEL[t.state]}</span>
                  {t.silent && <span className="badge silent">Sensor silent</span>}
                </td>
                <td>
                  {t.min_safe_c} to {t.max_safe_c} °C{" "}
                  <span className="muted small">{t.profile === "custom" ? "own" : "default"}</span>
                </td>
                <td>
                  {t.last_temperature_c?.toFixed(1) ?? "–"} °C at {hhmm(t.last_reading_at)}
                </td>
                <td>{t.last_humidity_pct ?? "–"} %</td>
                <td>{t.alert_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">Click a truck to see its temperature chart.</p>
      </section>

      {detail && (
        <section>
          <h2>
            {detail.truck_id} temperature{" "}
            <span className="muted small">
              ({detail.profile === "custom" ? "own range" : "default range"}: {detail.min_safe_c} to{" "}
              {detail.max_safe_c} °C)
            </span>
          </h2>
          <TempChart truck={detail} min={detail.min_safe_c} max={detail.max_safe_c} />
          <p className="muted small">Green band = safe range. Red shading = alert period.</p>
        </section>
      )}

      <section>
        <h2>Alerts ({report.alerts.length})</h2>
        {report.alerts.length === 0 ? (
          <p className="muted">No sustained problems.</p>
        ) : (
          <ul className="alerts">
            {report.alerts.map((a) => (
              <AlertCard key={a.alert_id} alert={a} summary={summaryFor(a.alert_id)} />
            ))}
          </ul>
        )}
      </section>

      <TruckRanges refreshKey={report} onChanged={() => void load()} />

    </main>
  );
}
