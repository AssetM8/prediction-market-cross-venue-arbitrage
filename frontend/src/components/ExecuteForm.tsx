import { useState } from "react";

import { api, newIdempotencyKey } from "../api";
import type { ExecuteResponse, ExecutionScenario, Opportunity } from "../types";
import { label, money, qty } from "../lib/format";
import { Badge, ErrorNote, statusTone } from "./ui";

const SCENARIOS: { value: ExecutionScenario; text: string }[] = [
  { value: "normal", text: "Normal (both legs fill if liquidity allows)" },
  { value: "second_leg_partial", text: "Second leg partially fills" },
  { value: "second_leg_reject", text: "Second leg rejected" },
  { value: "price_move_before_second_leg", text: "Price moves before second leg" },
  { value: "timeout", text: "Second leg times out" },
  { value: "venue_unavailable", text: "Second venue unavailable" },
  { value: "stale_quote", text: "Quote goes stale before submission" },
];

/** Paper execution form. Every submission carries a fresh Idempotency-Key. */
export function ExecuteForm({
  opportunity,
  executable,
  onExecuted,
  execute = api.execute,
}: {
  opportunity: Opportunity;
  executable: boolean;
  onExecuted?: (result: ExecuteResponse) => void;
  execute?: typeof api.execute;
}) {
  const [quantity, setQuantity] = useState(opportunity.max_executable_quantity);
  const [scenario, setScenario] = useState<ExecutionScenario>("normal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<ExecuteResponse>();

  async function submit() {
    setBusy(true);
    setError(undefined);
    try {
      const response = await execute(opportunity.id, { quantity, scenario }, newIdempotencyKey("exec"));
      setResult(response);
      onExecuted?.(response);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-amber-300 bg-amber-50/60 p-3" data-testid="execute-form">
      <div className="text-sm font-semibold text-amber-950">Simulate paper execution (no real order is sent)</div>
      {!executable && (
        <p className="text-sm text-amber-900">
          Not executable: only fresh <strong>validated</strong> cross-venue opportunities can be paper-executed.
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-600">
          Quantity per leg
          <input
            aria-label="Quantity per leg"
            className="mt-1 block w-28 rounded-md border border-slate-300 bg-white px-2 py-1 text-sm tabular-nums"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            inputMode="decimal"
          />
        </label>
        <label className="text-xs text-slate-600">
          Scenario
          <select
            aria-label="Scenario"
            className="mt-1 block rounded-md border border-slate-300 bg-white px-2 py-1 text-sm"
            value={scenario}
            onChange={(event) => setScenario(event.target.value as ExecutionScenario)}
          >
            {SCENARIOS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={!executable || busy}
          onClick={submit}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? "Simulating…" : "Paper execute"}
        </button>
      </div>
      {error && <ErrorNote message={error} />}
      {result && (
        <div className="space-y-2 rounded-md bg-white p-3 text-sm ring-1 ring-slate-200" data-testid="execution-result">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(result.execution.outcome)}>{label(result.execution.outcome)}</Badge>
            {result.replayed && <Badge tone="info">idempotent replay</Badge>}
            <span className="text-slate-600">{result.execution.reason}</span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            <span>hedged {qty(result.execution.hedged_quantity)} / {qty(result.execution.requested_quantity)}</span>
            <span>residual {qty(result.execution.residual_quantity)}</span>
            <span>fees {money(result.execution.total_fees, 4)}</span>
            <span>locked-in {money(result.execution.expected_locked_in_pnl, 4)}</span>
          </div>
          <ol className="list-decimal space-y-0.5 pl-5 text-xs text-slate-600">
            {result.execution.steps.map((step, index) => (
              <li key={`${step.at}-${index}`}>{step.message}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
