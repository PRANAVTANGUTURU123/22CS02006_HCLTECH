import { useEffect, useState } from "react";
import { fetchRanges, hhmm, putRange } from "./api";
import type { Ranges } from "./types";

const PRESETS: Record<string, [number, number]> = {
  "Pharma 2–8": [2, 8],
  "Chilled 0–5": [0, 5],
  "Frozen −25 to −18": [-25, -18],
  "Ambient 15–25": [15, 25],
};

const EMPTY = { truck: "", min: "", max: "", after: "" };

export function TruckRanges({ refreshKey, onChanged }: { refreshKey: unknown; onChanged: () => void }) {
  const [data, setData] = useState<Ranges | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [form, setForm] = useState(EMPTY);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    fetchRanges().then(setData, () => setData(null));
  }, [refreshKey]);

  if (!data) return null;
  const d = data.default;

  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  async function save(clear: boolean) {
    const body = clear
      ? { min_safe_c: null, max_safe_c: null, alert_after_min: null }
      : { min_safe_c: num(form.min), max_safe_c: num(form.max), alert_after_min: num(form.after) };
    try {
      await putRange(form.truck, body, apiKey);
      setMessage({
        ok: true,
        text: clear ? `${form.truck} is back on the default range.` : `${form.truck} range saved. Alerts re-checked.`,
      });
      if (clear) setForm(EMPTY);
      setData(await fetchRanges());
      onChanged();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Failed" });
    }
  }

  return (
    <section>
      <h2>Truck ranges</h2>
      <p className="muted small">
        A truck is added here automatically the first time it reports, with its range left empty. Until
        someone fills it in, it uses the default {d.min_safe_c} to {d.max_safe_c} °C (alert after{" "}
        {d.alert_after_min} min). Click a row to edit it.
      </p>
      <table>
        <thead>
          <tr>
            <th>Truck</th>
            <th>Added</th>
            <th>Safe range</th>
            <th>Alert after</th>
          </tr>
        </thead>
        <tbody>
          {data.trucks.map((t) => (
            <tr
              key={t.truck_id}
              className={form.truck === t.truck_id ? "selected" : ""}
              onClick={() =>
                setForm({
                  truck: t.truck_id,
                  min: t.min_safe_c?.toString() ?? "",
                  max: t.max_safe_c?.toString() ?? "",
                  after: t.alert_after_min?.toString() ?? "",
                })
              }
            >
              <td>
                <strong>{t.truck_id}</strong>
              </td>
              <td className="muted">{t.added_at ? `${t.added_at.slice(0, 10)} ${hhmm(t.added_at)}` : "–"}</td>
              <td>
                {t.custom ? (
                  `${t.min_safe_c} to ${t.max_safe_c} °C`
                ) : (
                  <span className="muted">not set (default {d.min_safe_c} to {d.max_safe_c} °C)</span>
                )}
              </td>
              <td>
                {t.alert_after_min !== null ? (
                  `${t.alert_after_min} min`
                ) : (
                  <span className="muted">default {d.alert_after_min} min</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="forms">
        <fieldset>
          <legend>Set a truck's range</legend>
          <input
            aria-label="Truck id"
            value={form.truck}
            placeholder="truck id"
            onChange={(e) => setForm({ ...form, truck: e.target.value.trim() })}
          />
          <input aria-label="Min °C" type="number" step="0.1" value={form.min} placeholder="min °C" onChange={(e) => setForm({ ...form, min: e.target.value })} />
          <input aria-label="Max °C" type="number" step="0.1" value={form.max} placeholder="max °C" onChange={(e) => setForm({ ...form, max: e.target.value })} />
          <input aria-label="Alert after min" type="number" value={form.after} placeholder={`alert after (${d.alert_after_min})`} onChange={(e) => setForm({ ...form, after: e.target.value })} />
          <span className="presets">
            {Object.entries(PRESETS).map(([label, [lo, hi]]) => (
              <button key={label} type="button" onClick={() => setForm({ ...form, min: String(lo), max: String(hi) })}>
                {label}
              </button>
            ))}
          </span>
        </fieldset>
        <div className="actions">
          <input
            type="password"
            aria-label="Admin key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="admin key"
            autoComplete="off"
          />
          <button disabled={!form.truck} onClick={() => void save(false)}>
            Save range
          </button>
          <button disabled={!form.truck} onClick={() => void save(true)}>
            Use default
          </button>
        </div>
      </div>

      {message && <p className={message.ok ? "ok-text" : "error"}>{message.text}</p>}
    </section>
  );
}
