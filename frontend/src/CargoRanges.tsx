import { useEffect, useState } from "react";
import { fetchProfiles, putJson } from "./api";
import type { Profiles, Truck } from "./types";

/**
 * Ops set each truck's safe range here: no code changes. A truck with no assignment
 * (e.g. a new truck) uses the default profile. Changes need the admin key.
 */
export function CargoRanges({ trucks, onChanged }: { trucks: Truck[]; onChanged: () => void }) {
  const [data, setData] = useState<Profiles | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [truck, setTruck] = useState("");
  const [profile, setProfile] = useState("");
  const [form, setForm] = useState({ name: "", min: "", max: "", after: "20" });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    fetchProfiles().then(setData, () => setData(null));
  }, []);

  if (!data) return null;

  async function run(action: () => Promise<Profiles>, done: string) {
    try {
      setData(await action());
      setMessage({ ok: true, text: done });
      onChanged();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Failed" });
    }
  }

  const assign = () =>
    run(
      () => putJson(`/api/trucks/${encodeURIComponent(truck)}/profile`, { profile }, apiKey),
      `${truck} now uses "${profile}". Alerts re-evaluated.`,
    );

  const saveProfile = () =>
    run(
      () =>
        putJson(
          `/api/profiles/${encodeURIComponent(form.name)}`,
          {
            min_safe_c: Number(form.min),
            max_safe_c: Number(form.max),
            alert_after_min: Number(form.after),
          },
          apiKey,
        ),
      `Profile "${form.name}" saved. Alerts re-evaluated.`,
    );

  return (
    <section>
      <h2>Cargo ranges</h2>
      <p className="muted small">
        Each truck is checked against its cargo's range. Trucks without a setting (including new
        trucks) use the default, <strong>{data.default}</strong>, the strictest range.
      </p>
      <table>
        <thead>
          <tr>
            <th>Profile</th>
            <th>Safe range</th>
            <th>Alert after</th>
            <th>Trucks</th>
          </tr>
        </thead>
        <tbody>
          {data.profiles.map((p) => {
            const using = trucks
              .filter((t) => (data.assignments[t.truck_id] ?? data.default) === p.name)
              .map((t) => t.truck_id);
            return (
              <tr key={p.name} onClick={() => setForm({ name: p.name, min: String(p.min_safe_c), max: String(p.max_safe_c), after: String(p.alert_after_min) })}>
                <td>
                  <strong>{p.name}</strong>
                  {p.name === data.default && <span className="muted small"> (default)</span>}
                </td>
                <td>
                  {p.min_safe_c} to {p.max_safe_c} °C
                </td>
                <td>{p.alert_after_min} min</td>
                <td>{using.join(", ") || "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="forms">
        <label>
          Admin key{" "}
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="needed to change ranges"
            autoComplete="off"
          />
        </label>

        <fieldset>
          <legend>Assign a truck</legend>
          <input
            aria-label="Truck id"
            list="truck-ids"
            value={truck}
            onChange={(e) => setTruck(e.target.value.trim())}
            placeholder="T-03 or a new truck id"
          />
          <datalist id="truck-ids">
            {trucks.map((t) => (
              <option key={t.truck_id} value={t.truck_id} />
            ))}
          </datalist>
          <select aria-label="Profile" value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="">profile…</option>
            {data.profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name} ({p.min_safe_c} to {p.max_safe_c} °C)
              </option>
            ))}
          </select>
          <button disabled={!truck || !profile} onClick={() => void assign()}>
            Assign
          </button>
        </fieldset>

        <fieldset>
          <legend>Add or edit a profile (click a row to edit it)</legend>
          <input aria-label="Profile name" value={form.name} placeholder="name, e.g. vaccines" onChange={(e) => setForm({ ...form, name: e.target.value.trim().toLowerCase() })} />
          <input aria-label="Min °C" type="number" step="0.1" value={form.min} placeholder="min °C" onChange={(e) => setForm({ ...form, min: e.target.value })} />
          <input aria-label="Max °C" type="number" step="0.1" value={form.max} placeholder="max °C" onChange={(e) => setForm({ ...form, max: e.target.value })} />
          <input aria-label="Alert after min" type="number" value={form.after} placeholder="alert after (min)" onChange={(e) => setForm({ ...form, after: e.target.value })} />
          <button disabled={!form.name || form.min === "" || form.max === ""} onClick={() => void saveProfile()}>
            Save profile
          </button>
        </fieldset>
      </div>

      {message && <p className={message.ok ? "ok-text" : "error"}>{message.text}</p>}
    </section>
  );
}
