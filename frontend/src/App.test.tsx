import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import App from "./App";
import type { Report, TruckDetail } from "./types";

const alert = {
  alert_id: "T-04:a",
  truck_id: "T-04",
  severity: "warning" as const,
  status: "recovering" as const,
  direction: "above" as const,
  limit_c: 8,
  started_at: "2026-07-20T08:25:00+00:00",
  opened_at: "2026-07-20T08:45:00+00:00",
  last_out_at: "2026-07-20T08:45:00+00:00",
  closed_at: null,
  escalated_at: null,
  duration_min: 20,
  peak_c: 9.1,
  reading_count: 4,
  updates_sent: 0,
  missing_data: [{ from: "2026-07-20T08:30:00+00:00", to: "2026-07-20T08:40:00+00:00", minutes: 10 }],
  reason: "Above 8 °C for 20 min (08:25–08:45), peak 9.1 °C. Includes missing data: 08:30–08:40 (10 min).",
};

const report: Report = {
  evaluated_at: "2026-07-20T08:50:00+00:00",
  config: { min_safe_c: 2, max_safe_c: 8, alert_after_min: 20 },
  data_quality: { input_readings: 2, accepted: 2, rejected: [], duplicates: 0, sensor_faults: [] },
  trucks: [
    { truck_id: "T-02", state: "ok", silent: false, last_reading_at: "2026-07-20T08:50:00+00:00", last_temperature_c: 6.1, last_humidity_pct: 50, open_alert_id: null, profile: "pharma", min_safe_c: 2, max_safe_c: 8, alert_after_min: 20, alert_count: 0, short_episodes: [] },
    { truck_id: "T-04", state: "recovering", silent: false, last_reading_at: "2026-07-20T08:50:00+00:00", last_temperature_c: 7.2, last_humidity_pct: 53, open_alert_id: "T-04:a", profile: "pharma", min_safe_c: 2, max_safe_c: 8, alert_after_min: 20, alert_count: 1, short_episodes: [] },
  ],
  alerts: [alert],
  notifications: [
    { notification_id: "T-04:a:opened", kind: "alert_opened", truck_id: "T-04", at: "2026-07-20T08:45:00+00:00", severity: "warning", message: "WARNING: Truck T-04 above 8 °C for 20 min", alert_id: "T-04:a", ai_summary: "Truck T-04 is too warm; likely a reefer problem." },
  ],
};

const detail: TruckDetail = {
  ...report.trucks[1],
  readings: [
    { timestamp: "2026-07-20T08:25:00+00:00", temperature_c: 8.7, humidity_pct: 56 },
    { timestamp: "2026-07-20T08:50:00+00:00", temperature_c: 7.2, humidity_pct: 53 },
  ],
  alerts: [alert],
};

const profiles = {
  default: "pharma",
  profiles: [
    { name: "chilled", min_safe_c: 0, max_safe_c: 5, alert_after_min: 20 },
    { name: "pharma", min_safe_c: 2, max_safe_c: 8, alert_after_min: 20 },
  ],
  assignments: {},
};

function mockFetch(onPut?: (url: string, init: RequestInit) => Response) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT" && onPut) return onPut(url, init);
    const body = url === "/api/profiles" ? profiles : url.startsWith("/api/trucks/") ? detail : report;
    return new Response(JSON.stringify(body), { status: 200 });
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("dashboard", () => {
  it("shows fleet states, the open-alert banner, reasons and the labelled AI summary", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "1 open alert: T-04 (recovering, peak 9.1 °C)",
    );
    const row = screen.getByText("T-04", { selector: "td strong" }).closest("tr")!;
    expect(within(row).getByText("Recovering")).toBeTruthy();
    expect(screen.getByText(/Includes missing data: 08:30–08:40/)).toBeTruthy();
    expect(screen.getByText("AI summary")).toBeTruthy();
    expect(screen.getByText(/likely a reefer problem/)).toBeTruthy();
  });

  it("draws the chart when a truck is clicked", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<App />);
    fireEvent.click(await screen.findByText("T-04", { selector: "td strong" }));
    expect(await screen.findByRole("img", { name: "Temperature chart for T-04" })).toBeTruthy();
  });

  it("lists cargo ranges and assigns a truck with the admin key", async () => {
    const puts: { url: string; key: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      mockFetch((url, init) => {
        const headers = init.headers as Record<string, string>;
        puts.push({ url, key: headers["X-API-Key"], body: String(init.body) });
        return new Response(JSON.stringify({ ...profiles, assignments: { "T-02": "chilled" } }), { status: 200 });
      }),
    );
    render(<App />);
    expect(await screen.findByText("Cargo ranges")).toBeTruthy();
    expect(screen.getByText("(default)")).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("needed to change ranges"), { target: { value: "k" } });
    fireEvent.change(screen.getByLabelText("Truck id"), { target: { value: "T-02" } });
    fireEvent.change(screen.getByLabelText("Profile"), { target: { value: "chilled" } });
    fireEvent.click(screen.getByText("Assign"));
    expect(await screen.findByText('T-02 now uses "chilled". Alerts re-evaluated.')).toBeTruthy();
    expect(puts).toEqual([{ url: "/api/trucks/T-02/profile", key: "k", body: '{"profile":"chilled"}' }]);
  });

  it("shows a clear message for a wrong admin key", async () => {
    vi.stubGlobal("fetch", mockFetch(() => new Response(JSON.stringify({ detail: "x" }), { status: 401 })));
    render(<App />);
    fireEvent.change(await screen.findByLabelText("Truck id"), { target: { value: "T-02" } });
    fireEvent.change(screen.getByLabelText("Profile"), { target: { value: "chilled" } });
    fireEvent.click(screen.getByText("Assign"));
    expect(await screen.findByText("Wrong or missing admin key")).toBeTruthy();
  });

  it("shows an error when the API is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 500 })));
    render(<App />);
    expect(await screen.findByText("/api/report failed (HTTP 500)")).toBeTruthy();
  });
});
