// Mirrors backend/report.py. If the API shape changes, `npm run typecheck` fails here.

export type TruckState = "ok" | "suspect" | "alert" | "recovering";
export type Severity = "warning" | "critical";
export type AlertStatus = "open" | "recovering" | "closed";

export interface ShortEpisode {
  started_at: string;
  last_out_at: string;
  duration_min: number;
  peak_c: number;
  reading_count: number;
  ongoing: boolean;
}

export interface Truck {
  truck_id: string;
  state: TruckState;
  silent: boolean;
  last_reading_at: string | null;
  last_temperature_c: number | null;
  last_humidity_pct: number | null;
  open_alert_id: string | null;
  alert_count: number;
  short_episodes: ShortEpisode[];
}

export interface Alert {
  alert_id: string;
  truck_id: string;
  severity: Severity;
  status: AlertStatus;
  direction: "above" | "below";
  limit_c: number;
  started_at: string;
  opened_at: string;
  last_out_at: string;
  closed_at: string | null;
  escalated_at: string | null;
  duration_min: number;
  peak_c: number;
  reading_count: number;
  updates_sent: number;
  missing_data: { from: string; to: string; minutes: number }[];
  reason: string;
}

export interface Notification {
  notification_id: string;
  kind: "alert_opened" | "alert_not_improving" | "alert_escalated" | "sensor_silent";
  truck_id: string;
  at: string;
  severity: Severity;
  message: string;
  alert_id: string | null;
  ai_summary: string | null;
}

export interface Report {
  evaluated_at: string | null;
  config: { min_safe_c: number; max_safe_c: number; alert_after_min: number };
  data_quality: {
    input_readings: number;
    accepted: number;
    rejected: { index: number; reason: string }[];
    duplicates: number;
    sensor_faults: unknown[];
  };
  trucks: Truck[];
  alerts: Alert[];
  notifications: Notification[];
}

export interface TruckDetail extends Truck {
  readings: { timestamp: string; temperature_c: number; humidity_pct: number | null }[];
  alerts: Alert[];
}
