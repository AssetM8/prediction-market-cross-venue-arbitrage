import type { Health, Portfolio } from "../types";
import { money, time } from "../lib/format";
import { Badge, Card, ErrorNote, Stat, statusTone } from "./ui";

export function HealthPanel({ health, error }: { health: Health | undefined; error: string | undefined }) {
  const scan = health?.last_scan;
  const statuses = scan?.opportunities_by_status ?? {};
  return (
    <Card title="System health" testId="health-panel">
      {error && <ErrorNote message={`API unreachable: ${error}`} />}
      {health && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={statusTone(health.status)}>status: {health.status}</Badge>
            <Badge tone={health.database === "ok" ? "good" : "bad"}>database: {health.database}</Badge>
            <Badge tone="info">mode: {health.data_mode}</Badge>
            {health.simulated_clock && <Badge tone="warn">simulated clock</Badge>}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Approved pairs" value={scan?.approved_pairs ?? 0} />
            <Stat label="Pairs evaluated" value={scan?.pairs ?? 0} />
            <Stat label="Validated opps" value={statuses.validated ?? 0} tone={(statuses.validated ?? 0) > 0 ? "good" : undefined} />
            <Stat label="Suppressed" value={statuses.suppressed ?? 0} />
          </div>
          <div className="text-xs text-slate-500">
            Last scan: {scan ? `${scan.kind} · ${time(scan.finished_at ?? scan.started_at)} · ${scan.data_source}` : "none yet"}
          </div>
          {scan?.errors?.length ? (
            <ul className="list-disc space-y-1 pl-5 text-xs text-rose-700">
              {scan.errors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Card>
  );
}

export function VenuePanel({ health }: { health: Health | undefined }) {
  return (
    <Card title="Venue connections" testId="venue-panel">
      {!health?.venues.length ? (
        <p className="text-sm text-slate-500">No venue checks recorded yet.</p>
      ) : (
        <ul className="space-y-3">
          {health.venues.map((venue) => (
            <li key={venue.venue} className="text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium capitalize">{venue.venue}</span>
                <Badge tone={venue.reachable ? "good" : "bad"}>{venue.reachable ? "reachable" : "unreachable"}</Badge>
              </div>
              <div className="mt-0.5 text-xs text-slate-500">
                {venue.data_source} · last success {time(venue.last_success_at)}
                {venue.trading_active === false ? " · trading halted" : ""}
              </div>
              {venue.last_error && <div className="mt-0.5 text-xs break-words text-rose-700">{venue.last_error}</div>}
              {venue.credentials_required_for.length > 0 && (
                <div className="mt-0.5 text-xs text-amber-700">
                  credentials required for: {venue.credentials_required_for.join(", ")} (degraded gracefully)
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function PortfolioPanel({ portfolio, onReset }: { portfolio: Portfolio | undefined; onReset: () => void }) {
  return (
    <Card
      title="Paper portfolio"
      testId="portfolio-panel"
      actions={
        portfolio?.kill_switch_engaged ? (
          <button
            type="button"
            onClick={onReset}
            className="rounded-md bg-rose-600 px-2 py-1 text-xs font-medium text-white hover:bg-rose-700"
          >
            Reset kill switch
          </button>
        ) : null
      }
    >
      {!portfolio ? (
        <p className="text-sm text-slate-500">No paper activity yet.</p>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Locked-in P&L" value={money(portfolio.locked_in_pnl, 4)} tone="good" hint="guaranteed settlement payout minus cost, for hedged bundles" />
            <Stat label="Realized P&L" value={money(portfolio.realized_pnl, 4)} tone={Number(portfolio.realized_pnl) < 0 ? "bad" : undefined} />
            <Stat label="Unrealized (bid marks)" value={money(portfolio.unrealized_pnl, 4)} hint={portfolio.marks_source} />
            <Stat label="Residual exposure" value={money(portfolio.residual_exposure_notional, 2)} tone={Number(portfolio.residual_exposure_notional) > 0 ? "bad" : undefined} />
            <Stat label="Fees paid" value={money(portfolio.total_fees, 4)} />
            <Stat
              label="Kill switch"
              value={portfolio.kill_switch_engaged ? "ENGAGED" : "armed"}
              tone={portfolio.kill_switch_engaged ? "bad" : "good"}
              hint={`${portfolio.consecutive_failures} consecutive failures`}
            />
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-slate-600">
            {portfolio.cash.map((cash) => (
              <span key={cash.venue} className="rounded bg-slate-100 px-2 py-1">
                <span className="capitalize">{cash.venue}</span> cash {money(cash.cash)} / {money(cash.starting_cash)}
              </span>
            ))}
            <span className="text-slate-400">venue balances cannot be netted</span>
          </div>
        </div>
      )}
    </Card>
  );
}
