import type { Profiles, Report, TruckDetail } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${url} failed (HTTP ${response.status})`);
  return (await response.json()) as T;
}

export const fetchReport = () => getJson<Report>("/api/report");
export const fetchTruck = (id: string) =>
  getJson<TruckDetail>(`/api/trucks/${encodeURIComponent(id)}`);

export const hhmm = (iso: string | null) => (iso ? iso.slice(11, 16) : "–");
export const fetchProfiles = () => getJson<Profiles>("/api/profiles");

/** PUT with the admin key. The key is only held in memory by the page. */
export async function putJson(url: string, body: unknown, apiKey: string): Promise<Profiles> {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": apiKey },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
    throw new Error(response.status === 401 ? "Wrong or missing admin key" : detail);
  }
  return data as Profiles;
}
