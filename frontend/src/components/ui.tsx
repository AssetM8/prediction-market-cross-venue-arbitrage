import type { ReactNode } from "react";

type Tone = "neutral" | "good" | "bad" | "warn" | "info" | "muted";

const TONES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-800 ring-slate-300",
  good: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  bad: "bg-rose-50 text-rose-800 ring-rose-300",
  warn: "bg-amber-50 text-amber-900 ring-amber-300",
  info: "bg-sky-50 text-sky-800 ring-sky-300",
  muted: "bg-slate-50 text-slate-500 ring-slate-200",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function Card({ title, actions, children, testId }: { title: ReactNode; actions?: ReactNode; children: ReactNode; testId?: string }) {
  return (
    <section data-testid={testId} className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
        <h2 className="text-sm font-semibold tracking-wide text-slate-700 uppercase">{title}</h2>
        {actions}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  const color = tone === "bad" ? "text-rose-700" : tone === "good" ? "text-emerald-700" : "text-slate-900";
  return (
    <div className="min-w-0">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`truncate text-lg font-semibold tabular-nums ${color}`} title={hint}>
        {value}
      </div>
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
      {message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-200 p-4 text-sm text-slate-500">{children}</div>;
}

export function Loading() {
  return <div className="animate-pulse text-sm text-slate-400">Loading…</div>;
}

export function Th({ children, right }: { children?: ReactNode; right?: boolean }) {
  return (
    <th className={`px-2 py-1.5 text-xs font-medium tracking-wide text-slate-500 uppercase ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

export function Td({ children, right, mono, className = "" }: { children?: ReactNode; right?: boolean; mono?: boolean; className?: string }) {
  return (
    <td className={`px-2 py-1.5 align-top text-sm ${right ? "text-right tabular-nums" : ""} ${mono ? "font-mono text-xs" : ""} ${className}`}>
      {children}
    </td>
  );
}

export function statusTone(status: string): Tone {
  switch (status) {
    case "validated":
    case "hedged":
    case "pass":
    case "filled":
    case "ok":
      return "good";
    case "candidate":
    case "partially_filled":
    case "partially_hedged_unwound":
    case "unknown":
    case "degraded":
      return "warn";
    case "suppressed":
    case "rejected":
    case "fail":
    case "unhedged_residual":
    case "kill_switch_blocked":
    case "timed_out":
    case "error":
      return "bad";
    case "not_applicable":
    case "not_profitable":
      return "muted";
    default:
      return "neutral";
  }
}

export function relationTone(relation: string, approved?: boolean): Tone {
  if (approved) return "good";
  if (relation === "EQUIVALENT" || relation === "COMPLEMENTARY") return "info";
  if (relation === "AMBIGUOUS") return "warn";
  if (relation === "UNRELATED") return "muted";
  return "neutral";
}
