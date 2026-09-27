import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NOW, makeHealth, makeOpportunity, makePair } from "../test/factories";
import type { ExecuteResponse } from "../types";
import { ExecuteForm } from "./ExecuteForm";
import { DEFAULT_OPPORTUNITY_FILTERS, OpportunityTable, filterOpportunities } from "./OpportunitiesView";
import { DEFAULT_PAIR_FILTERS, PairDetail, filterPairs } from "./PairsView";
import { PaperBanner } from "./PaperBanner";
import { ProfitChart } from "./ProfitChart";
import { HealthPanel, PortfolioPanel, VenuePanel } from "./SystemPanels";

describe("PaperBanner", () => {
  it("always states paper trading only and labels fixture data", () => {
    render(<PaperBanner health={makeHealth()} />);
    expect(screen.getByTestId("paper-banner")).toHaveTextContent(/paper trading only/i);
    expect(screen.getByTestId("data-mode")).toHaveTextContent("FIXTURE DATA");
    expect(screen.getByTestId("data-mode")).toHaveTextContent("simulated clock");
  });

  it("labels live data and still shows the banner while loading", () => {
    const { rerender } = render(<PaperBanner health={undefined} />);
    expect(screen.getByTestId("paper-banner")).toHaveTextContent(/paper trading only/i);
    rerender(<PaperBanner health={makeHealth({ data_mode: "live", simulated_clock: false })} />);
    expect(screen.getByTestId("data-mode")).toHaveTextContent("LIVE PUBLIC DATA");
  });
});

describe("system panels", () => {
  it("shows an error when the API is unreachable", () => {
    render(<HealthPanel health={undefined} error="Failed to fetch" />);
    expect(screen.getByRole("alert")).toHaveTextContent("API unreachable: Failed to fetch");
  });

  it("shows venue connection state and errors", () => {
    render(
      <VenuePanel
        health={makeHealth({
          venues: [
            { venue: "kalshi", data_source: "live", reachable: false, checked_at: NOW, last_success_at: null, last_error: "HTTP 403", trading_active: null, credentials_required_for: [], detail: "" },
          ],
        })}
      />,
    );
    expect(screen.getByText("unreachable")).toBeInTheDocument();
    expect(screen.getByText("HTTP 403")).toBeInTheDocument();
  });

  it("offers a kill-switch reset only when engaged", async () => {
    const onReset = vi.fn();
    const portfolio = {
      as_of: NOW, cash: [], positions: [], hedged_bundles: [], realized_pnl: "0", unrealized_pnl: "0",
      locked_in_pnl: "0", total_fees: "0", residual_exposure_notional: "0", kill_switch_engaged: true,
      consecutive_failures: 3, marks_source: "bids",
    };
    render(<PortfolioPanel portfolio={portfolio} onReset={onReset} />);
    await userEvent.click(screen.getByRole("button", { name: "Reset kill switch" }));
    expect(onReset).toHaveBeenCalledOnce();
    expect(screen.getByText("ENGAGED")).toBeInTheDocument();
  });
});

describe("opportunities", () => {
  const items = [
    makeOpportunity(),
    makeOpportunity({ id: "opp_2", status: "suppressed", expected_net_profit: "9.2", stale_after: "2026-09-25T13:55:30Z", rejection_reason: "stale_book: polymarket:no age 300s > 30s" }),
    makeOpportunity({ id: "opp_3", status: "not_profitable", expected_net_profit: "0", strategy_type: "market_rebalancing_short" }),
  ];

  it("filters by status, strategy, profit and freshness against the service clock", () => {
    expect(filterOpportunities(items, { ...DEFAULT_OPPORTUNITY_FILTERS, status: "validated" }, NOW).map((o) => o.id)).toEqual(["opp_1"]);
    expect(filterOpportunities(items, { ...DEFAULT_OPPORTUNITY_FILTERS, strategy: "market_rebalancing_short" }, NOW)).toHaveLength(1);
    expect(filterOpportunities(items, { ...DEFAULT_OPPORTUNITY_FILTERS, minProfit: "5" }, NOW).map((o) => o.id)).toEqual(["opp_2"]);
    expect(filterOpportunities(items, { ...DEFAULT_OPPORTUNITY_FILTERS, freshness: "stale" }, NOW).map((o) => o.id)).toEqual(["opp_2"]);
    expect(filterOpportunities(items, { ...DEFAULT_OPPORTUNITY_FILTERS, freshness: "fresh" }, undefined)).toEqual([]);
  });

  it("renders the table and the empty state", async () => {
    const onSelect = vi.fn();
    const { rerender } = render(<OpportunityTable items={items} onSelect={onSelect} now={NOW} />);
    const table = screen.getByTestId("opportunity-table");
    expect(within(table).getByText("stale_book: polymarket:no age 300s > 30s")).toBeInTheDocument();
    await userEvent.click(within(table).getAllByText("validated")[0]!);
    expect(onSelect).toHaveBeenCalledWith("opp_1");
    rerender(<OpportunityTable items={[]} onSelect={onSelect} now={NOW} />);
    expect(screen.getByText(/No current opportunity/)).toBeInTheDocument();
  });

  it("charts profit by quantity or explains why there is none", () => {
    const { rerender } = render(<ProfitChart points={makeOpportunity().profit_curve} chosen="150" />);
    expect(screen.getByRole("img", { name: "Cumulative net profit by quantity" })).toBeInTheDocument();
    rerender(<ProfitChart points={[]} chosen="0" />);
    expect(screen.getByText(/No profitable depth/)).toBeInTheDocument();
  });
});

describe("ExecuteForm", () => {
  it("is disabled for non-executable opportunities", () => {
    render(<ExecuteForm opportunity={makeOpportunity({ status: "suppressed" })} executable={false} execute={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Paper execute" })).toBeDisabled();
    expect(screen.getByText(/Not executable/)).toBeInTheDocument();
  });

  it("sends an idempotency key and shows the simulated result", async () => {
    const response: ExecuteResponse = {
      paper_trading_only: true,
      replayed: false,
      execution: {
        id: "exec_1", opportunity_id: "opp_1", pair_id: "pair_1", idempotency_key: "k", policy: "sequential",
        scenario: "second_leg_partial", requested_quantity: "10", hedged_quantity: "4", residual_quantity: "0",
        residual_notional: "0", outcome: "partially_hedged_unwound", total_cost: "9", total_fees: "0.1",
        realized_pnl: "-0.2", expected_locked_in_pnl: "0.3", orders: [],
        steps: [{ at: NOW, message: "leg 1 filled", detail: {} }, { at: NOW, message: "unwind filled", detail: {} }],
        started_at: NOW, finished_at: NOW, failure: true, reason: "hedged 4 of 10; residual unwound",
      },
    };
    const execute = vi.fn().mockResolvedValue(response);
    const onExecuted = vi.fn();
    render(<ExecuteForm opportunity={makeOpportunity()} executable execute={execute} onExecuted={onExecuted} />);
    await userEvent.clear(screen.getByLabelText("Quantity per leg"));
    await userEvent.type(screen.getByLabelText("Quantity per leg"), "10");
    await userEvent.selectOptions(screen.getByLabelText("Scenario"), "second_leg_partial");
    await userEvent.click(screen.getByRole("button", { name: "Paper execute" }));
    expect(execute).toHaveBeenCalledWith("opp_1", { quantity: "10", scenario: "second_leg_partial" }, expect.stringMatching(/^exec-[A-Za-z0-9_.:-]{8,}$/));
    const result = await screen.findByTestId("execution-result");
    expect(result).toHaveTextContent("partially hedged unwound");
    expect(result).toHaveTextContent("unwind filled");
    expect(onExecuted).toHaveBeenCalledWith(response);
  });

  it("surfaces API errors", async () => {
    const execute = vi.fn().mockRejectedValue(new Error("opportunity status is suppressed"));
    render(<ExecuteForm opportunity={makeOpportunity()} executable execute={execute} />);
    await userEvent.click(screen.getByRole("button", { name: "Paper execute" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("opportunity status is suppressed");
  });
});

describe("pairs", () => {
  it("filters by relation, decision, confidence and venue status", () => {
    const approved = makePair({ id: "p2", relation: "EQUIVALENT", approved_for_arbitrage_calculation: true, confidence: "1.00",
      deterministic_checks: [{ name: "market_status", status: "pass", critical: true, kalshi_value: "active", polymarket_value: "active", detail: "" }] });
    const pairs = [makePair(), approved];
    expect(filterPairs(pairs, { ...DEFAULT_PAIR_FILTERS, decision: "approved" })).toEqual([approved]);
    expect(filterPairs(pairs, { ...DEFAULT_PAIR_FILTERS, relation: "UNRELATED" })).toHaveLength(1);
    expect(filterPairs(pairs, { ...DEFAULT_PAIR_FILTERS, minConfidence: "0.95" })).toEqual([approved]);
    expect(filterPairs(pairs, { ...DEFAULT_PAIR_FILTERS, venueStatus: "active" })).toEqual([approved]);
  });

  it("shows the contract-rule difference viewer with blocking mismatches", () => {
    render(<PairDetail pair={makePair()} />);
    expect(screen.getByTestId("blocking-mismatches")).toHaveTextContent("office: differs");
    const diff = screen.getByTestId("rule-diff");
    expect(within(diff).getByText("senate")).toBeInTheDocument();
    expect(within(diff).getByText("governor")).toBeInTheDocument();
    expect(screen.getByText(/cannot be inferred from titles alone/)).toBeInTheDocument();
  });
});
