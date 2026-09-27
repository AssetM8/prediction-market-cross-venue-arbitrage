import { api } from "../api";
import type { Opportunity } from "../types";
import { label, money, qty } from "../lib/format";
import { useResource } from "../lib/useResource";
import { Badge, Card, Empty, ErrorNote, Loading, Td, Th, relationTone, statusTone } from "./ui";

/** Logical relationship graph (combinatorial view) plus the paper-derived analytics. */
export function RelationshipsView({ version }: { version: number }) {
  const graph = useResource(() => api.relationships(), [version]);
  const opportunities = useResource(() => api.opportunities(), [version]);
  const analytics = (opportunities.data ?? []).filter((item: Opportunity) => item.strategy_type !== "cross_venue_binary");
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Relationship graph (cross-venue)">
        {graph.error && <ErrorNote message={graph.error} />}
        {!graph.data ? (
          <Loading />
        ) : graph.data.edges.length === 0 ? (
          <Empty>No logical relations determined.</Empty>
        ) : (
          <ul className="space-y-3" data-testid="relationship-edges">
            {graph.data.edges.map((edge) => (
              <li key={edge.id} className="rounded-lg border border-slate-200 p-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={relationTone(edge.relation, edge.approved)}>{edge.relation}</Badge>
                  <Badge tone={edge.risk_free_in_mvp ? "good" : "muted"}>
                    {edge.risk_free_in_mvp ? "executable in MVP" : "analytics only"}
                  </Badge>
                  <span className="text-xs text-slate-500">confidence {edge.confidence}</span>
                </div>
                <div className="mt-1">
                  <span className="text-slate-500">A (Kalshi):</span> {edge.source_title}
                </div>
                <div>
                  <span className="text-slate-500">B (Polymarket):</span> {edge.target_title}
                </div>
                {edge.verified_constructions.length > 0 && (
                  <div className="mt-1 text-xs text-slate-600">
                    Verified constructions:{" "}
                    {edge.verified_constructions
                      .map((c) => `${c.kalshi.toUpperCase()}@Kalshi + ${c.polymarket.toUpperCase()}@Polymarket (min payout ${c.min_payout})`)
                      .join("; ")}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Paper-derived analytics (not cross-venue execution candidates)">
        <p className="mb-3 text-xs text-slate-500">
          Market rebalancing (paper Definition 3) inside one venue and combinatorial constructions for implication pairs
          (Definition 4, adapted). These are never paper-executed in the MVP.
        </p>
        {opportunities.error && <ErrorNote message={opportunities.error} />}
        {!opportunities.data ? (
          <Loading />
        ) : analytics.length === 0 ? (
          <Empty>No analytics records.</Empty>
        ) : (
          <table className="w-full" data-testid="analytics-table">
            <thead>
              <tr>
                <Th>kind</Th>
                <Th>construction</Th>
                <Th right>qty</Th>
                <Th right>net</Th>
              </tr>
            </thead>
            <tbody>
              {analytics.map((item) => (
                <tr key={item.id} className="border-t border-slate-100">
                  <Td>
                    <Badge tone={statusTone(item.status)}>{label(item.status)}</Badge>
                    <div className="mt-0.5 text-xs text-slate-500">{label(item.strategy_type)}</div>
                  </Td>
                  <Td>
                    <div className="text-sm">{item.direction}</div>
                    {item.rejection_reason && <div className="text-xs text-slate-500">{item.rejection_reason}</div>}
                  </Td>
                  <Td right>{qty(item.max_executable_quantity)}</Td>
                  <Td right>{money(item.expected_net_profit, 4)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
