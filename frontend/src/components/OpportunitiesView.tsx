import { useMemo, useState } from "react";

import { api } from "../api";
import type { ExecuteResponse, Opportunity, OpportunityStatus, StrategyType } from "../types";
import { label, money, pct, price, qty, time } from "../lib/format";
import { useResource } from "../lib/useResource";
import { DepthTable } from "./DepthTable";
import { ExecuteForm } from "./ExecuteForm";
import { ProfitChart } from "./ProfitChart";
import { Badge, Card, Empty, ErrorNote, Loading, Td, Th, statusTone } from "./ui";

export interface OpportunityFilters {
  status: OpportunityStatus | "all";
  strategy: StrategyType | "all";
  minProfit: string;
  freshness: "all" | "fresh" | "stale";
}

export const DEFAULT_OPPORTUNITY_FILTERS: OpportunityFilters = {
  status: "all",
  strategy: "all",
  minProfit: "",
  freshness: "all",
};

/** Freshness is judged against the service clock (simulated in fixture mode), never the browser's. */
export function isFresh(item: Opportunity, now: string | undefined): boolean | undefined {
  if (!now) return undefined;
  return Date.parse(now) <= Date.parse(item.stale_after);
}

export function filterOpportunities(items: Opportunity[], filters: OpportunityFilters, now: string | undefined): Opportunity[] {
  const min = filters.minProfit.trim() === "" ? undefined : Number(filters.minProfit);
  return items.filter((item) => {
    if (filters.status !== "all" && item.status !== filters.status) return false;
    if (filters.strategy !== "all" && item.strategy_type !== filters.strategy) return false;
    if (min !== undefined && !Number.isNaN(min) && Number(item.expected_net_profit) < min) return false;
    const fresh = isFresh(item, now);
    if (filters.freshness === "fresh" && fresh !== true) return false;
    if (filters.freshness === "stale" && fresh !== false) return false;
    return true;
  });
}

function Select<T extends string>({ label: text, value, options, onChange }: {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}) {
  return (
    <label className="text-xs text-slate-600">
      {text}
      <select
        aria-label={text}
        className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value as T)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {label(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function OpportunityTable({ items, selected, onSelect, now }: {
  items: Opportunity[];
  selected?: string;
  onSelect: (id: string) => void;
  now?: string;
}) {
  if (items.length === 0) {
    return <Empty>No opportunities match these filters. “No current opportunity” is a valid result.</Empty>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px]" data-testid="opportunity-table">
        <thead>
          <tr>
            <Th>status</Th>
            <Th>strategy · direction</Th>
            <Th right>qty</Th>
            <Th right>net profit</Th>
            <Th right>return</Th>
            <Th>age</Th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const fresh = isFresh(item, now);
            return (
              <tr
                key={item.id}
                onClick={() => onSelect(item.id)}
                className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === item.id ? "bg-sky-50" : ""}`}
              >
                <Td>
                  <Badge tone={statusTone(item.status)}>{label(item.status)}</Badge>
                </Td>
                <Td>
                  <div className="text-xs text-slate-500">{label(item.strategy_type)} · {item.data_source}</div>
                  <div className="text-sm">{item.direction}</div>
                  {item.rejection_reason && <div className="text-xs text-slate-500">{item.rejection_reason}</div>}
                </Td>
                <Td right>{qty(item.max_executable_quantity)}</Td>
                <Td right className={Number(item.expected_net_profit) > 0 ? "text-emerald-700" : "text-slate-500"}>
                  {money(item.expected_net_profit, 4)}
                </Td>
                <Td right>{pct(item.return_on_capital)}</Td>
                <Td>
                  <Badge tone={fresh ? "good" : "muted"}>{fresh === undefined ? "—" : fresh ? "fresh" : "stale"}</Badge>
                </Td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function OpportunityDetailView({ id, onExecuted }: { id: string; onExecuted?: (result: ExecuteResponse) => void }) {
  const detail = useResource(() => api.opportunity(id), [id]);
  if (detail.error) return <ErrorNote message={detail.error} />;
  if (!detail.data) return <Loading />;
  const { opportunity: opp, books, executable, fresh, pair } = detail.data;
  const bookFor = (outcomeId: string) => books.find((book) => book.outcome_id === outcomeId);
  return (
    <div className="space-y-4" data-testid="opportunity-detail">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(opp.status)}>{label(opp.status)}</Badge>
        <Badge tone="info">{opp.label}</Badge>
        <Badge tone={fresh ? "good" : "muted"}>{fresh ? "fresh" : `stale since ${time(opp.stale_after)}`}</Badge>
        {opp.data_source === "fixture" && <Badge tone="warn">fixture data</Badge>}
      </div>
      <div className="text-sm font-medium">{opp.direction}</div>
      {pair && (
        <div className="text-xs text-slate-600">
          Pair {pair.relation} (confidence {pair.confidence}): <em>{pair.kalshi_title}</em> ↔ <em>{pair.polymarket_title}</em>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4" data-testid="cost-breakdown">
        <Metric name="Max executable qty" value={qty(opp.max_executable_quantity)} />
        <Metric name="Guaranteed payout" value={money(opp.guaranteed_payout, 4)} />
        <Metric name="Gross cost (depth-walked)" value={money(opp.gross_cost, 4)} />
        <Metric name="of which slippage" value={money(opp.slippage, 4)} />
        <Metric name="Explicit fees" value={money(opp.explicit_fees, 4)} />
        <Metric name="Safety buffers" value={money(opp.safety_buffer, 4)} />
        <Metric name="Expected net profit" value={money(opp.expected_net_profit, 4)} strong />
        <Metric name="Return on capital" value={pct(opp.return_on_capital)} />
        <Metric name="Top-of-book cost / unit" value={price(opp.top_of_book_cost_per_unit)} />
        <Metric name="Top-of-book net / unit" value={price(opp.top_of_book_net_per_unit)} />
        <Metric name="Detected" value={time(opp.detected_at)} />
        <Metric name="Stale after" value={time(opp.stale_after)} />
      </div>
      {opp.rejection_reason && <ErrorNote message={`Why not executable: ${opp.rejection_reason}`} />}
      <div>
        <h3 className="mb-1 text-xs font-semibold text-slate-600 uppercase">Profit by quantity</h3>
        <ProfitChart points={opp.profit_curve} chosen={opp.max_executable_quantity} />
      </div>
      <div>
        <h3 className="mb-1 text-xs font-semibold text-slate-600 uppercase">Order-book depth used on each venue</h3>
        <div className="grid gap-3 md:grid-cols-2">
          {opp.legs.map((leg) => {
            const book = bookFor(leg.outcome_id);
            return (
              <div key={leg.outcome_id} className="space-y-1">
                <div className="text-xs text-slate-600">
                  Buy {leg.outcome_side.toUpperCase()} on <span className="capitalize">{leg.venue}</span>: qty {qty(leg.quantity)} ·
                  VWAP {price(leg.vwap)} · worst {price(leg.worst_price)} · fee {money(leg.fee, 4)}
                </div>
                {book ? <DepthTable book={book} leg={leg} /> : <Empty>book unavailable</Empty>}
              </div>
            );
          })}
        </div>
      </div>
      {opp.payoff_states.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs font-semibold text-slate-600 uppercase">Verified payoff per unit (joint states allowed by the relation)</h3>
          <table className="text-xs">
            <thead>
              <tr>
                <Th>Kalshi YES</Th>
                <Th>Polymarket YES</Th>
                <Th right>payout</Th>
              </tr>
            </thead>
            <tbody>
              {opp.payoff_states.map((state) => (
                <tr key={`${state.kalshi_yes}-${state.polymarket_yes}`}>
                  <Td>{state.kalshi_yes ? "true" : "false"}</Td>
                  <Td>{state.polymarket_yes ? "true" : "false"}</Td>
                  <Td right>{state.payout_per_unit}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details className="text-xs text-slate-600">
        <summary className="cursor-pointer font-semibold uppercase">Assumptions ({opp.assumptions.length})</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {opp.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ul>
      </details>
      <ExecuteForm
        opportunity={opp}
        executable={executable}
        onExecuted={(result) => {
          detail.reload();
          onExecuted?.(result);
        }}
      />
    </div>
  );
}

function Metric({ name, value, strong }: { name: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-md bg-slate-50 px-2 py-1.5">
      <div className="text-[11px] text-slate-500">{name}</div>
      <div className={`tabular-nums ${strong ? "font-semibold text-emerald-700" : ""}`}>{value}</div>
    </div>
  );
}

export function OpportunitiesView({ now, onExecuted, version }: { now?: string; onExecuted: () => void; version: number }) {
  const list = useResource(() => api.opportunities(), [version]);
  const [filters, setFilters] = useState(DEFAULT_OPPORTUNITY_FILTERS);
  const [selected, setSelected] = useState<string>();
  const items = useMemo(() => filterOpportunities(list.data ?? [], filters, now), [list.data, filters, now]);
  const set = <K extends keyof OpportunityFilters>(key: K, value: OpportunityFilters[K]) =>
    setFilters((current) => ({ ...current, [key]: value }));
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <Card title="Opportunities (net of fees, slippage and buffers)">
        <div className="mb-3 flex flex-wrap gap-3">
          <Select label="Status" value={filters.status} options={["all", "validated", "candidate", "not_profitable", "suppressed"] as const} onChange={(v) => set("status", v)} />
          <Select
            label="Strategy"
            value={filters.strategy}
            options={["all", "cross_venue_binary", "combinatorial", "market_rebalancing_long", "market_rebalancing_short"] as const}
            onChange={(v) => set("strategy", v)}
          />
          <Select label="Freshness" value={filters.freshness} options={["all", "fresh", "stale"] as const} onChange={(v) => set("freshness", v)} />
          <label className="text-xs text-slate-600">
            Min expected profit
            <input
              aria-label="Min expected profit"
              className="mt-1 block w-24 rounded-md border border-slate-300 px-2 py-1 text-sm"
              value={filters.minProfit}
              placeholder="0"
              onChange={(event) => set("minProfit", event.target.value)}
            />
          </label>
        </div>
        {list.error && <ErrorNote message={list.error} />}
        {list.loading && !list.data ? <Loading /> : <OpportunityTable items={items} selected={selected} onSelect={setSelected} now={now} />}
      </Card>
      <Card title="Opportunity detail">
        {selected ? (
          <OpportunityDetailView
            key={selected}
            id={selected}
            onExecuted={() => {
              list.reload();
              onExecuted();
            }}
          />
        ) : (
          <Empty>Select an opportunity to see book depth, profit by quantity, assumptions and the paper-execution form.</Empty>
        )}
      </Card>
    </div>
  );
}
