import type { Health } from "../types";
import { time } from "../lib/format";

/** Always-visible paper-trading warning plus the live/fixture data indicator. */
export function PaperBanner({ health }: { health: Health | undefined }) {
  const fixture = health?.data_mode === "fixture";
  return (
    <div className="sticky top-0 z-20">
      <div
        role="status"
        data-testid="paper-banner"
        className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 bg-amber-400 px-4 py-2 text-center text-sm font-semibold text-amber-950"
      >
        <span className="rounded bg-amber-950 px-2 py-0.5 text-xs tracking-widest text-amber-300 uppercase">
          Paper trading only
        </span>
        <span>Read-only market data · simulated execution · no real orders can be placed · not financial advice</span>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 bg-slate-900 px-4 py-2 text-sm text-slate-200">
        <div className="font-semibold text-white">Kalshi × Polymarket cross-venue arbitrage monitor</div>
        <div className="flex items-center gap-2" data-testid="data-mode">
          {health ? (
            fixture ? (
              <span className="rounded-md bg-violet-500/20 px-2 py-0.5 text-violet-200 ring-1 ring-violet-400/40">
                FIXTURE DATA · synthetic · simulated clock {time(health.now)}
              </span>
            ) : (
              <span className="rounded-md bg-emerald-500/20 px-2 py-0.5 text-emerald-200 ring-1 ring-emerald-400/40">
                LIVE PUBLIC DATA · read-only
              </span>
            )
          ) : (
            <span className="text-slate-400">connecting…</span>
          )}
        </div>
      </div>
    </div>
  );
}
