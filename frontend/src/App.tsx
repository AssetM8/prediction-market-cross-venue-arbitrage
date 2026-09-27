import { useState } from "react";

import { api } from "./api";
import { AuditView } from "./components/AuditView";
import { OpportunitiesView } from "./components/OpportunitiesView";
import { PairsView } from "./components/PairsView";
import { PaperBanner } from "./components/PaperBanner";
import { PaperView } from "./components/PaperView";
import { RelationshipsView } from "./components/RelationshipsView";
import { HealthPanel, PortfolioPanel, VenuePanel } from "./components/SystemPanels";
import { ErrorNote } from "./components/ui";
import { useResource } from "./lib/useResource";

const TABS = [
  { id: "opportunities", text: "Opportunities" },
  { id: "pairs", text: "Market pairs" },
  { id: "relationships", text: "Relationships & analytics" },
  { id: "paper", text: "Paper trading" },
  { id: "audit", text: "Audit log" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("opportunities");
  const [version, setVersion] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string>();
  const health = useResource(() => api.health(), [version], 15000);
  const portfolio = useResource(() => api.portfolio(), [version]);
  const bump = () => setVersion((value) => value + 1);

  async function refresh() {
    setRefreshing(true);
    setRefreshError(undefined);
    try {
      await api.refresh();
      bump();
    } catch (reason) {
      setRefreshError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRefreshing(false);
    }
  }

  async function resetKillSwitch() {
    await api.resetKillSwitch();
    bump();
  }

  return (
    <div className="min-h-screen bg-slate-100 text-slate-900">
      <PaperBanner health={health.data} />
      <main className="mx-auto max-w-[1600px] space-y-4 p-4">
        <div className="grid gap-4 lg:grid-cols-3">
          <HealthPanel health={health.data} error={health.error} />
          <VenuePanel health={health.data} />
          <PortfolioPanel portfolio={portfolio.data} onReset={resetKillSwitch} />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <nav className="flex flex-wrap gap-1" aria-label="Sections">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                  tab === item.id ? "bg-slate-900 text-white" : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50"
                }`}
              >
                {item.text}
              </button>
            ))}
          </nav>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50 disabled:opacity-50"
          >
            {refreshing ? "Scanning…" : `Re-scan (${health.data?.data_mode ?? "…"} data, read-only)`}
          </button>
        </div>
        {refreshError && <ErrorNote message={`Refresh failed: ${refreshError}`} />}
        {tab === "opportunities" && <OpportunitiesView now={health.data?.now} version={version} onExecuted={bump} />}
        {tab === "pairs" && <PairsView version={version} />}
        {tab === "relationships" && <RelationshipsView version={version} />}
        {tab === "paper" && <PaperView version={version} portfolio={portfolio.data} />}
        {tab === "audit" && <AuditView version={version} />}
        <footer className="pb-6 text-center text-xs text-slate-500">
          Educational research demo derived from arXiv:2508.03474. Not financial advice. Contract equivalence cannot be
          inferred from titles alone; settlement differences between venues create basis risk.
        </footer>
      </main>
    </div>
  );
}
