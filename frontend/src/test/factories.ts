import type { Health, MarketPair, Opportunity } from "../types";

export const NOW = "2026-09-25T14:00:00Z";

export function makeOpportunity(overrides: Partial<Opportunity> = {}): Opportunity {
  return {
    id: "opp_1",
    pair_id: "pair_1",
    strategy_type: "cross_venue_binary",
    direction: "A: buy YES on Kalshi + buy NO on Polymarket",
    relation: "EQUIVALENT",
    status: "validated",
    detected_at: NOW,
    book_timestamps: { "kalshi:yes": NOW, "polymarket:no": NOW },
    legs: [],
    max_executable_quantity: "150",
    top_of_book_cost_per_unit: "0.949",
    top_of_book_net_per_unit: "0.032",
    gross_cost: "138.6",
    guaranteed_payout: "150",
    explicit_fees: "4.3902",
    slippage: "0.6",
    safety_buffer: "2.7361",
    expected_net_profit: "4.273711",
    return_on_capital: "0.029888",
    stale_after: "2026-09-25T14:00:30Z",
    assumptions: ["binary contracts pay exactly 1 unit"],
    rejection_reason: null,
    profit_curve: [
      { quantity: "120", cumulative_cost: "110", cumulative_fees: "3", cumulative_buffers: "2", cumulative_net_profit: "3.9", marginal_net_per_unit: "0.032" },
      { quantity: "150", cumulative_cost: "138", cumulative_fees: "4", cumulative_buffers: "2.7", cumulative_net_profit: "4.27", marginal_net_per_unit: "0.012" },
    ],
    payoff_states: [
      { kalshi_yes: true, polymarket_yes: true, payout_per_unit: "1" },
      { kalshi_yes: false, polymarket_yes: false, payout_per_unit: "1" },
    ],
    data_source: "fixture",
    label: "validated paper opportunity",
    ...overrides,
  };
}

export function makePair(overrides: Partial<MarketPair> = {}): MarketPair {
  return {
    id: "pair_1",
    kalshi_market_id: "KXSENATEOH-26-JAVE",
    polymarket_market_id: "610008",
    kalshi_title: "2026 Ohio Senate election winner? — Jordan Avery",
    polymarket_title: "Will Jordan Avery win the 2026 Ohio gubernatorial election?",
    similarity_score: "0.58",
    relation: "UNRELATED",
    confidence: "0.90",
    deterministic_checks: [
      { name: "office", status: "fail", critical: true, kalshi_value: "senate", polymarket_value: "governor", detail: "differs" },
      { name: "subject", status: "pass", critical: true, kalshi_value: "jordan avery", polymarket_value: "jordan avery", detail: "identical" },
    ],
    semantic_explanation: {
      relation: "UNRELATED",
      confidence: "0.90",
      shared_event: false,
      same_resolution_criteria: true,
      differences: ["office: senate vs governor"],
      evidence: [],
      safe_for_cross_venue_arbitrage: false,
      reason: "event identity differs: office",
    },
    blocking_mismatches: ["office: differs"],
    adjudication_source: "deterministic",
    approved_for_arbitrage_calculation: false,
    decision_reasons: ["event identity differs: office", "not approved"],
    leg_mappings: [],
    propositions: { kalshi_rules: "If Jordan Avery wins the Senate race...", polymarket_rules: "Resolves Yes if Jordan Avery wins the governor race..." },
    data_source: "fixture",
    evaluated_at: NOW,
    ...overrides,
  };
}

export function makeHealth(overrides: Partial<Health> = {}): Health {
  return {
    status: "ok",
    paper_trading_only: true,
    data_mode: "fixture",
    simulated_clock: true,
    now: NOW,
    database: "ok",
    last_scan: null,
    venues: [],
    ...overrides,
  };
}
