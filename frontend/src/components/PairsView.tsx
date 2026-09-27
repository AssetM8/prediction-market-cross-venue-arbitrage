import { useMemo, useState } from "react";

import { api } from "../api";
import type { MarketPair, Relation } from "../types";
import { label } from "../lib/format";
import { useResource } from "../lib/useResource";
import { Badge, Card, Empty, ErrorNote, Loading, Td, Th, relationTone, statusTone } from "./ui";

export interface PairFilters {
  relation: Relation | "all";
  decision: "all" | "approved" | "rejected";
  minConfidence: string;
  venueStatus: "all" | "active" | "inactive";
}

export const DEFAULT_PAIR_FILTERS: PairFilters = { relation: "all", decision: "all", minConfidence: "", venueStatus: "all" };

const RELATIONS = [
  "all",
  "EQUIVALENT",
  "COMPLEMENTARY",
  "A_IMPLIES_B",
  "B_IMPLIES_A",
  "MUTUALLY_EXCLUSIVE",
  "PARTIALLY_OVERLAPPING",
  "UNRELATED",
  "AMBIGUOUS",
] as const;

function bothActive(pair: MarketPair): boolean {
  return pair.deterministic_checks.some((check) => check.name === "market_status" && check.status === "pass");
}

export function filterPairs(pairs: MarketPair[], filters: PairFilters): MarketPair[] {
  const min = filters.minConfidence.trim() === "" ? undefined : Number(filters.minConfidence);
  return pairs.filter((pair) => {
    if (filters.relation !== "all" && pair.relation !== filters.relation) return false;
    if (filters.decision === "approved" && !pair.approved_for_arbitrage_calculation) return false;
    if (filters.decision === "rejected" && pair.approved_for_arbitrage_calculation) return false;
    if (min !== undefined && !Number.isNaN(min) && Number(pair.confidence) < min) return false;
    if (filters.venueStatus === "active" && !bothActive(pair)) return false;
    if (filters.venueStatus === "inactive" && bothActive(pair)) return false;
    return true;
  });
}

/** Contract-rule difference viewer: every deterministic check with both venues' values. */
export function PairDetail({ pair }: { pair: MarketPair }) {
  const props = pair.propositions as Record<string, unknown>;
  const kalshiRules = String(props.kalshi_rules ?? "");
  const polyRules = String(props.polymarket_rules ?? "");
  const groups = (props.check_groups ?? {}) as Record<string, string>;
  return (
    <div className="space-y-4" data-testid="pair-detail">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={relationTone(pair.relation, pair.approved_for_arbitrage_calculation)}>{pair.relation}</Badge>
        <Badge tone={pair.approved_for_arbitrage_calculation ? "good" : "bad"}>
          {pair.approved_for_arbitrage_calculation ? "approved for arbitrage calculation" : "rejected"}
        </Badge>
        <Badge>confidence {pair.confidence}</Badge>
        <Badge>similarity {pair.similarity_score}</Badge>
        <Badge tone="muted">{label(pair.adjudication_source)}</Badge>
      </div>
      <div className="text-sm text-slate-700">{pair.semantic_explanation.reason}</div>
      {pair.blocking_mismatches.length > 0 && (
        <div>
          <h3 className="mb-1 text-xs font-semibold text-rose-700 uppercase">Blocking mismatches</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-rose-800" data-testid="blocking-mismatches">
            {pair.blocking_mismatches.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {pair.leg_mappings.length > 0 && (
        <div className="text-sm">
          <h3 className="mb-1 text-xs font-semibold text-slate-600 uppercase">Verified leg mappings</h3>
          <ul className="list-disc pl-5">
            {pair.leg_mappings.map((mapping) => (
              <li key={mapping.label}>{mapping.label}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px]" data-testid="rule-diff">
          <thead>
            <tr>
              <Th>check</Th>
              <Th>result</Th>
              <Th>Kalshi</Th>
              <Th>Polymarket</Th>
              <Th>detail</Th>
            </tr>
          </thead>
          <tbody>
            {pair.deterministic_checks.map((check) => (
              <tr key={check.name} className={`border-t border-slate-100 ${check.status === "fail" ? "bg-rose-50/60" : check.status === "unknown" ? "bg-amber-50/60" : ""}`}>
                <Td>
                  <div>{label(check.name)}</div>
                  <div className="text-[10px] text-slate-400">
                    {groups[check.name] ?? ""} {check.critical ? "· critical" : ""}
                  </div>
                </Td>
                <Td>
                  <Badge tone={statusTone(check.status)}>{label(check.status)}</Badge>
                </Td>
                <Td mono>{check.kalshi_value ?? "—"}</Td>
                <Td mono>{check.polymarket_value ?? "—"}</Td>
                <Td className="text-xs text-slate-600">{check.detail}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <RulesBox title={`Kalshi rules — ${pair.kalshi_market_id}`} text={kalshiRules} source={String(props.kalshi_resolution_source ?? "")} />
        <RulesBox title={`Polymarket rules — ${pair.polymarket_market_id}`} text={polyRules} source={String(props.polymarket_resolution_source ?? "")} />
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer font-semibold text-slate-600 uppercase">Structured judgement (Stage 5 JSON)</summary>
        <pre className="mt-1 overflow-x-auto rounded bg-slate-900 p-2 text-[11px] text-slate-100">
          {JSON.stringify(pair.semantic_explanation, null, 2)}
        </pre>
      </details>
      <p className="text-xs text-slate-500">
        Equivalence cannot be inferred from titles alone. Even approved pairs carry basis risk if venue settlement rules diverge
        in ways the text checks cannot see.
      </p>
    </div>
  );
}

function RulesBox({ title, text, source }: { title: string; text: string; source: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-2">
      <div className="mb-1 text-xs font-semibold text-slate-600">{title}</div>
      <p className="text-xs whitespace-pre-wrap text-slate-700">{text || "—"}</p>
      <div className="mt-1 text-[11px] text-slate-500">Resolution source: {source || "—"}</div>
    </div>
  );
}

export function PairsView({ version }: { version: number }) {
  const pairs = useResource(() => api.pairs(), [version]);
  const [filters, setFilters] = useState(DEFAULT_PAIR_FILTERS);
  const [selected, setSelected] = useState<string>();
  const items = useMemo(() => filterPairs(pairs.data ?? [], filters), [pairs.data, filters]);
  const current = items.find((pair) => pair.id === selected) ?? (pairs.data ?? []).find((pair) => pair.id === selected);
  const set = <K extends keyof PairFilters>(key: K, value: PairFilters[K]) => setFilters((c) => ({ ...c, [key]: value }));
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <Card title="Candidate market pairs">
        <div className="mb-3 flex flex-wrap gap-3">
          <label className="text-xs text-slate-600">
            Relation
            <select
              aria-label="Relation"
              className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
              value={filters.relation}
              onChange={(event) => set("relation", event.target.value as PairFilters["relation"])}
            >
              {RELATIONS.map((relation) => (
                <option key={relation} value={relation}>
                  {relation === "all" ? "all" : relation}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            Decision
            <select
              aria-label="Decision"
              className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
              value={filters.decision}
              onChange={(event) => set("decision", event.target.value as PairFilters["decision"])}
            >
              <option value="all">all</option>
              <option value="approved">approved</option>
              <option value="rejected">rejected</option>
            </select>
          </label>
          <label className="text-xs text-slate-600">
            Venue status
            <select
              aria-label="Venue status"
              className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
              value={filters.venueStatus}
              onChange={(event) => set("venueStatus", event.target.value as PairFilters["venueStatus"])}
            >
              <option value="all">all</option>
              <option value="active">both active</option>
              <option value="inactive">not both active</option>
            </select>
          </label>
          <label className="text-xs text-slate-600">
            Min confidence
            <input
              aria-label="Min confidence"
              className="mt-1 block w-20 rounded-md border border-slate-300 px-2 py-1 text-sm"
              placeholder="0.90"
              value={filters.minConfidence}
              onChange={(event) => set("minConfidence", event.target.value)}
            />
          </label>
        </div>
        {pairs.error && <ErrorNote message={pairs.error} />}
        {pairs.loading && !pairs.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty>No pairs match these filters.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]" data-testid="pair-table">
              <thead>
                <tr>
                  <Th>relation</Th>
                  <Th>Kalshi ↔ Polymarket</Th>
                  <Th right>conf.</Th>
                  <Th>reason</Th>
                </tr>
              </thead>
              <tbody>
                {items.map((pair) => (
                  <tr
                    key={pair.id}
                    onClick={() => setSelected(pair.id)}
                    className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 ${selected === pair.id ? "bg-sky-50" : ""}`}
                  >
                    <Td>
                      <Badge tone={relationTone(pair.relation, pair.approved_for_arbitrage_calculation)}>
                        {pair.approved_for_arbitrage_calculation ? "✓ " : ""}
                        {pair.relation}
                      </Badge>
                    </Td>
                    <Td>
                      <div className="text-sm">{pair.kalshi_title}</div>
                      <div className="text-sm text-slate-600">{pair.polymarket_title}</div>
                    </Td>
                    <Td right>{pair.confidence}</Td>
                    <Td className="max-w-xs text-xs text-slate-600">
                      {pair.approved_for_arbitrage_calculation ? "approved" : pair.blocking_mismatches[0] ?? pair.decision_reasons[0]}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Contract-rule difference viewer">
        {current ? <PairDetail pair={current} /> : <Empty>Select a pair to compare both venues' contract rules check by check.</Empty>}
      </Card>
    </div>
  );
}
