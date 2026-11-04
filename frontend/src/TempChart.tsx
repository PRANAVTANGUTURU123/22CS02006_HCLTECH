import type { TruckDetail } from "./types";
import { hhmm } from "./api";

const W = 640;
const H = 220;
const PAD = { left: 40, right: 12, top: 12, bottom: 28 };

/** Temperature over time with the safe band and each alert period shaded. Plain SVG. */
export function TempChart({ truck, min, max }: { truck: TruckDetail; min: number; max: number }) {
  const pts = truck.readings;
  if (pts.length < 2) return <p className="muted">Not enough readings to chart.</p>;

  const t0 = Date.parse(pts[0].timestamp);
  const t1 = Date.parse(pts[pts.length - 1].timestamp);
  const temps = pts.map((p) => p.temperature_c);
  const lo = Math.floor(Math.min(...temps, min) - 1);
  const hi = Math.ceil(Math.max(...temps, max) + 1);
  const x = (iso: string) =>
    PAD.left + ((Date.parse(iso) - t0) / Math.max(t1 - t0, 1)) * (W - PAD.left - PAD.right);
  const y = (c: number) => PAD.top + ((hi - c) / (hi - lo)) * (H - PAD.top - PAD.bottom);
  const line = pts.map((p) => `${x(p.timestamp)},${y(p.temperature_c)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Temperature chart for ${truck.truck_id}`}>
      <rect className="band" x={PAD.left} width={W - PAD.left - PAD.right} y={y(max)} height={y(min) - y(max)} />
      {truck.alerts.map((a) => (
        <rect
          key={a.alert_id}
          className="alert-span"
          x={x(a.started_at)}
          width={Math.max(x(a.last_out_at) - x(a.started_at), 2)}
          y={PAD.top}
          height={H - PAD.top - PAD.bottom}
        />
      ))}
      {[min, max].map((c) => (
        <g key={c}>
          <line className="limit" x1={PAD.left} x2={W - PAD.right} y1={y(c)} y2={y(c)} />
          <text className="axis" x={PAD.left - 6} y={y(c) + 4} textAnchor="end">
            {c}°
          </text>
        </g>
      ))}
      <polyline className="temp" points={line} />
      {pts.map((p) => (
        <circle
          key={p.timestamp}
          className={p.temperature_c > max || p.temperature_c < min ? "dot out" : "dot"}
          cx={x(p.timestamp)}
          cy={y(p.temperature_c)}
          r={3.5}
        >
          <title>{`${hhmm(p.timestamp)}  ${p.temperature_c} °C`}</title>
        </circle>
      ))}
      <text className="axis" x={PAD.left} y={H - 8}>
        {hhmm(pts[0].timestamp)}
      </text>
      <text className="axis" x={W - PAD.right} y={H - 8} textAnchor="end">
        {hhmm(pts[pts.length - 1].timestamp)}
      </text>
    </svg>
  );
}
