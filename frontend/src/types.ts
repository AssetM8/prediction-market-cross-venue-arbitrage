// Types mirroring the FastAPI responses. Decimal values arrive as strings and are only
// converted to numbers for display and charting, never for accounting.

export type Decimal = string;
export type Venue = "kalshi" | "polymarket";
export type DataSource = "live" | "fixture";
export type OutcomeSide = "yes" | "no";
export type Relation =
  | "EQUIVALENT"
  | "COMPLEMENTARY"
  | "A_IMPLIES_B"
  | "B_IMPLIES_A"
  | "MUTUALLY_EXCLUSIVE"
  | "PARTIALLY_OVERLAPPING"
  | "UNRELATED"
  | "AMBIGUOUS";
export type CheckStatus = "pass" | "fail" | "unknown" | "not_applicable";
export type OpportunityStatus = "validated" | "candidate" | "not_profitable" | "suppressed";
export type StrategyType =
  | "cross_venue_binary"
  | "market_rebalancing_long"
  | "market_rebalancing_short"
  | "combinatorial";
export type ExecutionScenario =
  | "normal"
  | "second_leg_reject"
  | "second_leg_partial"
  | "price_move_before_second_leg"
  | "stale_quote"
  | "timeout"
  | "venue_unavailable";

export interface VenueHealth {
  venue: Venue;
  data_source: DataSource;
  reachable: boolean;
  checked_at: string;
  last_success_at: string | null;
  last_error: string | null;
  trading_active: boolean | null;
  credentials_required_for: string[];
  detail: string;
}

export interface Health {
  status: "ok" | "degraded" | "error";
  paper_trading_only: boolean;
  data_mode: DataSource;
  simulated_clock: boolean;
  now: string;
  database: string;
  last_scan: ScanSummary | null;
  venues: VenueHealth[];
}

export interface ScanSummary {
  run_id: string;
  kind: string;
  data_source: DataSource;
  status: string;
  started_at: string;
  finished_at: string | null;
  matching: Record<string, unknown>;
  pairs: number;
  approved_pairs: number;
  opportunities_by_status: Record<string, number>;
  validated_opportunities: string[];
  errors: string[];
}

export interface CheckResult {
  name: string;
  status: CheckStatus;
  critical: boolean;
  kalshi_value: string | null;
  polymarket_value: string | null;
  detail: string;
}

export interface SemanticJudgement {
  relation: Relation;
  confidence: Decimal;
  shared_event: boolean;
  same_resolution_criteria: boolean;
  differences: string[];
  evidence: string[];
  safe_for_cross_venue_arbitrage: boolean;
  reason: string;
}

export interface LegMapping {
  kalshi_side: OutcomeSide;
  polymarket_side: OutcomeSide;
  label: string;
}

export interface MarketPair {
  id: string;
  kalshi_market_id: string;
  polymarket_market_id: string;
  kalshi_title: string;
  polymarket_title: string;
  similarity_score: Decimal;
  relation: Relation;
  confidence: Decimal;
  deterministic_checks: CheckResult[];
  semantic_explanation: SemanticJudgement;
  blocking_mismatches: string[];
  adjudication_source: string;
  approved_for_arbitrage_calculation: boolean;
  decision_reasons: string[];
  leg_mappings: LegMapping[];
  propositions: Record<string, unknown>;
  data_source: DataSource;
  evaluated_at: string;
}

export interface BookLevel {
  price: Decimal;
  quantity: Decimal;
}

export interface OrderBook {
  venue: Venue;
  market_id: string;
  outcome_id: string;
  outcome_side: OutcomeSide;
  received_at: string;
  source_timestamp: string | null;
  bids: BookLevel[];
  asks: BookLevel[];
  checksum_or_source_hash: string;
  derived_asks: boolean;
  depth_limited: boolean;
  data_source: DataSource;
  integrity_issues: string[];
  tick_size: Decimal | null;
  min_order_size: Decimal | null;
}

export interface LevelFill {
  price: Decimal;
  quantity: Decimal;
  fee: Decimal;
}

export interface ArbitrageLeg {
  venue: Venue;
  market_id: string;
  outcome_id: string;
  outcome_side: OutcomeSide;
  quantity: Decimal;
  best_price: Decimal;
  worst_price: Decimal;
  vwap: Decimal;
  cost: Decimal;
  fee: Decimal;
  fills: LevelFill[];
  book_observed_at: string;
  book_hash: string;
  derived_asks: boolean;
}

export interface ProfitPoint {
  quantity: Decimal;
  cumulative_cost: Decimal;
  cumulative_fees: Decimal;
  cumulative_buffers: Decimal;
  cumulative_net_profit: Decimal;
  marginal_net_per_unit: Decimal;
}

export interface PayoffState {
  kalshi_yes: boolean;
  polymarket_yes: boolean;
  payout_per_unit: Decimal;
}

export interface Opportunity {
  id: string;
  pair_id: string | null;
  strategy_type: StrategyType;
  direction: string;
  relation: Relation | null;
  status: OpportunityStatus;
  detected_at: string;
  book_timestamps: Record<string, string>;
  legs: ArbitrageLeg[];
  max_executable_quantity: Decimal;
  top_of_book_cost_per_unit: Decimal | null;
  top_of_book_net_per_unit: Decimal | null;
  gross_cost: Decimal;
  guaranteed_payout: Decimal;
  explicit_fees: Decimal;
  slippage: Decimal;
  safety_buffer: Decimal;
  expected_net_profit: Decimal;
  return_on_capital: Decimal | null;
  stale_after: string;
  assumptions: string[];
  rejection_reason: string | null;
  profit_curve: ProfitPoint[];
  payoff_states: PayoffState[];
  data_source: DataSource;
  label: string;
}

export interface OpportunityDetail {
  opportunity: Opportunity;
  run_id: string;
  fresh: boolean;
  executable: boolean;
  pair: MarketPair | null;
  books: OrderBook[];
}

export interface PaperFill {
  id: string;
  order_id: string;
  price: Decimal;
  quantity: Decimal;
  fee: Decimal;
  filled_at: string;
}

export interface PaperOrder {
  id: string;
  execution_id: string;
  opportunity_id: string;
  leg_index: number;
  purpose: string;
  venue: Venue;
  market_id: string;
  outcome_id: string;
  outcome_side: OutcomeSide;
  action: "buy" | "sell";
  requested_quantity: Decimal;
  limit_price: Decimal;
  filled_quantity: Decimal;
  average_price: Decimal | null;
  fees: Decimal;
  status: string;
  reject_reason: string | null;
  created_at: string;
  fills: PaperFill[];
  simulated: boolean;
}

export interface ExecutionStep {
  at: string;
  message: string;
  detail: Record<string, string>;
}

export interface PaperExecution {
  id: string;
  opportunity_id: string;
  pair_id: string | null;
  idempotency_key: string;
  policy: string;
  scenario: ExecutionScenario;
  requested_quantity: Decimal;
  hedged_quantity: Decimal;
  residual_quantity: Decimal;
  residual_notional: Decimal;
  outcome: string;
  total_cost: Decimal;
  total_fees: Decimal;
  realized_pnl: Decimal;
  expected_locked_in_pnl: Decimal;
  orders: PaperOrder[];
  steps: ExecutionStep[];
  started_at: string;
  finished_at: string;
  failure: boolean;
  reason: string;
}

export interface ExecuteResponse {
  paper_trading_only: boolean;
  replayed: boolean;
  execution: PaperExecution;
}

export interface PaperPosition {
  key: string;
  venue: Venue;
  market_id: string;
  outcome_id: string;
  outcome_side: OutcomeSide;
  quantity: Decimal;
  average_cost: Decimal;
  cost_basis: Decimal;
  fees_paid: Decimal;
  realized_pnl: Decimal;
  mark_price: Decimal | null;
  unrealized_pnl: Decimal | null;
  updated_at: string;
}

export interface VenueCash {
  venue: Venue;
  starting_cash: Decimal;
  cash: Decimal;
}

export interface HedgedBundle {
  execution_id: string;
  pair_id: string | null;
  quantity: Decimal;
  cost_including_fees: Decimal;
  guaranteed_payout: Decimal;
  locked_in_pnl: Decimal;
}

export interface Portfolio {
  as_of: string;
  cash: VenueCash[];
  positions: PaperPosition[];
  hedged_bundles: HedgedBundle[];
  realized_pnl: Decimal;
  unrealized_pnl: Decimal;
  locked_in_pnl: Decimal;
  total_fees: Decimal;
  residual_exposure_notional: Decimal;
  kill_switch_engaged: boolean;
  consecutive_failures: number;
  marks_source: string;
}

export interface AuditEvent {
  id: number;
  ts: string;
  category: string;
  message: string;
  data_source: DataSource;
  correlation_id: string | null;
  pair_id: string | null;
  opportunity_id: string | null;
  execution_id: string | null;
  paper_order_id: string | null;
  payload: Record<string, unknown>;
}

export interface RelationshipEdge {
  id: string;
  pair_id: string;
  source: string;
  target: string;
  source_title: string;
  target_title: string;
  relation: Relation;
  confidence: Decimal;
  approved: boolean;
  risk_free_in_mvp: boolean;
  verified_constructions: { kalshi: OutcomeSide; polymarket: OutcomeSide; min_payout: Decimal }[];
}

export interface RelationshipGraph {
  nodes: { id: string; venue: Venue; title: string }[];
  edges: RelationshipEdge[];
}

export interface PublicConfig {
  paper_trading_only: boolean;
  data_mode: DataSource;
  simulated_clock: boolean;
  book_stale_after_seconds: number;
  market_pair_min_confidence: Decimal;
  min_net_profit: Decimal;
  min_return_on_capital: Decimal;
  execution_policy: string;
  max_unhedged_notional: Decimal;
  [key: string]: unknown;
}
