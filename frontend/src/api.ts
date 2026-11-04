import type { Ranges, Report, TruckDetail, TruckRange } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url} failed (HTTP ${response.status})`);
  return (await response.json()) as T;
}

export const fetchReport = () => getJson<Report>("/api/report");
export const fetchTruck = (id: string) =>
  getJson<TruckDetail>(`/api/trucks/${encodeURIComponent(id)}`);

export const hhmm = (iso: string | null) => (iso ? iso.slice(11, 16) : "–");
export const fetchRanges = () => getJson<Ranges>("/api/ranges");

/** Set (or clear, with nulls) one truck's own range. The admin key is only held in memory. */
export async function putRange(
  truckId: string,
  body: { min_safe_c: number | null; max_safe_c: number | null; alert_after_min: number | null },
  apiKey: string,
): Promise<TruckRange> {
  const response = await fetch(`/api/trucks/${encodeURIComponent(truckId)}/range`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
    throw new Error(response.status === 401 ? "Wrong or missing admin key" : detail);
  }
  return data as TruckRange;
}
