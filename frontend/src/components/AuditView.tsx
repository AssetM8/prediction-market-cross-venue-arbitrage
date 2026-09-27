import { useState } from "react";

import { api } from "../api";
import { time } from "../lib/format";
import { useResource } from "../lib/useResource";
import { Badge, Card, Empty, ErrorNote, Loading } from "./ui";

const CATEGORIES = ["", "paper_execution", "pair_decision", "opportunity", "scan", "kill_switch"];

export function AuditView({ version }: { version: number }) {
  const [category, setCategory] = useState("");
  const events = useResource(() => api.audit(category || undefined), [version, category]);
  const [open, setOpen] = useState<number>();
  return (
    <Card
      title="Audit log"
      actions={
        <select
          aria-label="Audit category"
          className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          {CATEGORIES.map((value) => (
            <option key={value} value={value}>
              {value || "all categories"}
            </option>
          ))}
        </select>
      }
    >
      {events.error && <ErrorNote message={events.error} />}
      {!events.data ? (
        <Loading />
      ) : events.data.length === 0 ? (
        <Empty>No audit events.</Empty>
      ) : (
        <ul className="divide-y divide-slate-100" data-testid="audit-list">
          {events.data.map((event) => (
            <li key={event.id} className="py-2">
              <button type="button" className="w-full text-left" onClick={() => setOpen(open === event.id ? undefined : event.id)}>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone="info">{event.category}</Badge>
                  <span className="text-xs text-slate-500">{time(event.ts)}</span>
                  {event.data_source === "fixture" && <Badge tone="warn">fixture</Badge>}
                  <span>{event.message}</span>
                </div>
              </button>
              {open === event.id && (
                <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-900 p-2 text-[11px] text-slate-100">
                  {JSON.stringify(event.payload, null, 2)}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
