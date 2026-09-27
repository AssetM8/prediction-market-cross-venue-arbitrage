import type { ProfitPoint } from "../types";
import { money, num, qty } from "../lib/format";

const WIDTH = 520;
const HEIGHT = 200;
const PAD = { top: 12, right: 12, bottom: 28, left: 56 };

/** Cumulative net profit by executable quantity (depth-walked, after fees and buffers). */
export function ProfitChart({ points, chosen }: { points: ProfitPoint[]; chosen: string }) {
  if (points.length === 0) {
    return <p className="text-sm text-slate-500">No profitable depth: the first marginal unit already loses money.</p>;
  }
  const data = [{ x: 0, y: 0 }, ...points.map((p) => ({ x: num(p.quantity), y: num(p.cumulative_net_profit) }))];
  const xs = data.map((d) => d.x);
  const ys = data.map((d) => d.y);
  const xMax = Math.max(...xs, 1);
  const yMin = Math.min(...ys, 0);
  const yMax = Math.max(...ys, 0.01);
  const sx = (x: number) => PAD.left + (x / xMax) * (WIDTH - PAD.left - PAD.right);
  const sy = (y: number) => PAD.top + (1 - (y - yMin) / (yMax - yMin || 1)) * (HEIGHT - PAD.top - PAD.bottom);
  const path = data.map((d, i) => `${i === 0 ? "M" : "L"}${sx(d.x).toFixed(1)},${sy(d.y).toFixed(1)}`).join(" ");
  const chosenX = num(chosen);
  return (
    <figure>
      <svg
        role="img"
        aria-label="Cumulative net profit by quantity"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full max-w-xl"
      >
        <line x1={PAD.left} x2={WIDTH - PAD.right} y1={sy(0)} y2={sy(0)} stroke="#94a3b8" strokeDasharray="4 3" />
        <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke="#cbd5e1" />
        <text x={PAD.left - 6} y={sy(yMax) + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
          {money(String(yMax))}
        </text>
        <text x={PAD.left - 6} y={sy(0) + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
          0
        </text>
        {yMin < 0 && (
          <text x={PAD.left - 6} y={sy(yMin) + 4} textAnchor="end" className="fill-slate-500 text-[10px]">
            {money(String(yMin))}
          </text>
        )}
        <text x={WIDTH - PAD.right} y={HEIGHT - 8} textAnchor="end" className="fill-slate-500 text-[10px]">
          quantity {qty(String(xMax))}
        </text>
        {chosenX > 0 && (
          <line x1={sx(chosenX)} x2={sx(chosenX)} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke="#f59e0b" strokeWidth={1.5} />
        )}
        <path d={path} fill="none" stroke="#0f766e" strokeWidth={2} />
        {data.slice(1).map((d) => (
          <circle key={`${d.x}-${d.y}`} cx={sx(d.x)} cy={sy(d.y)} r={3} fill={d.y >= 0 ? "#0f766e" : "#e11d48"} />
        ))}
      </svg>
      <figcaption className="text-xs text-slate-500">
        Each point is the exact economics after consuming another depth level on both books; the amber line marks the
        chosen quantity. Points past it show where the marginal unit stops being profitable.
      </figcaption>
    </figure>
  );
}
