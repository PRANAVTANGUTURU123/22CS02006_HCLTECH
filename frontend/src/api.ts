import type { Report, TruckDetail } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url} failed (HTTP ${response.status})`);
  return (await response.json()) as T;
}

export const fetchReport = () => getJson<Report>("/api/report");
export const fetchTruck = (id: string) =>
  getJson<TruckDetail>(`/api/trucks/${encodeURIComponent(id)}`);

export const hhmm = (iso: string | null) => (iso ? iso.slice(11, 16) : "–");
