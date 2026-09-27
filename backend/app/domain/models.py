"""Normalized domain models shared by venues, matching, arbitrage and execution.

All monetary and quantity fields are :class:`~decimal.Decimal`. When serialized to JSON
(API responses, persistence payloads) Pydantic renders Decimals as strings, so no value
passes through binary floating point.

Binary-contract conventions used throughout (verified against official docs, see
``docs/SOURCES.md``): one contract/share pays exactly 1 unit of collateral (USD on Kalshi,
pUSD on Polymarket) when its outcome occurs and 0 otherwise; prices are quoted per contract
in the open interval (0, 1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.decimal_utils import ONE, ZERO
from app.domain.enums import (
    AdjudicationSource,
    CheckStatus,
    DataSource,
    MarketStatus,
    OpportunityStatus,
    OutcomeSide,
    Relation,
    StrategyType,
    Venue,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------------------
# Markets and books
# --------------------------------------------------------------------------------------


class BookLevel(DomainModel):
    """One aggregated price level. Prices must lie strictly inside (0, 1)."""

    price: Decimal
    quantity: Decimal

    @field_validator("price")
    @classmethod
    def _price_in_unit_interval(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= ZERO or value >= ONE:
            raise ValueError(f"binary contract price must be in (0, 1), got {value}")
        return value

    @field_validator("quantity")
    @classmethod
    def _positive_quantity(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= ZERO:
            raise ValueError(f"level quantity must be positive, got {value}")
        return value


class FeeMetadata(DomainModel):
    """How taker fees are computed for a market.

    ``model`` selects the formula in :mod:`app.arbitrage.fees`. ``source`` records whether
    the parameters came from documented venue metadata or from a configured conservative
    fallback, so every opportunity can state which assumption it used.
    """

    model: str  # "kalshi_quadratic" | "polymarket_curve" | "none"
    rate: Decimal
    multiplier: Decimal = ONE
    exponent: Decimal = ONE
    rounding_quantum: Decimal
    taker_only: bool = True
    source: str  # "documented_metadata" | "fallback_assumption" | "fixture"
    notes: str = ""


class NormalizedOutcome(DomainModel):
    outcome_id: str
    label: str
    proposition_text: str
    side: OutcomeSide
    settlement_value: Decimal = ONE
    token_id: str | None = None


class NormalizedMarket(DomainModel):
    venue: Venue
    venue_market_id: str
    venue_event_id: str
    title: str
    event_title: str = ""
    description: str = ""
    rules: str = ""
    category: str = ""
    status: MarketStatus
    market_type: str = "binary"
    open_time: datetime | None = None
    close_time: datetime | None = None
    resolution_time: datetime | None = None
    resolution_source: str | None = None
    outcomes: tuple[NormalizedOutcome, ...]
    tick_size: Decimal | None = None
    min_order_size: Decimal | None = None
    fee_metadata: FeeMetadata
    raw_metadata_hash: str
    data_source: DataSource
    url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.venue.value}:{self.venue_market_id}"

    def outcome(self, side: OutcomeSide) -> NormalizedOutcome:
        for outcome in self.outcomes:
            if outcome.side is side:
                return outcome
        raise KeyError(f"{self.key} has no {side.value} outcome")


class NormalizedOrderBook(DomainModel):
    """Order book for one outcome of one market.

    ``bids`` are sorted best (highest) first and ``asks`` best (lowest) first, regardless
    of the ordering the venue used on the wire. ``derived_asks`` is True when asks were
    computed from the complementary outcome's bids (Kalshi publishes bids only).
    """

    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    sequence_or_timestamp: str
    received_at: datetime
    source_timestamp: datetime | None = None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    stale: bool = False
    checksum_or_source_hash: str
    tick_size: Decimal | None = None
    min_order_size: Decimal | None = None
    derived_asks: bool = False
    depth_limited: bool = False
    data_source: DataSource
    integrity_issues: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _sorted(self) -> NormalizedOrderBook:
        bid_prices = [level.price for level in self.bids]
        ask_prices = [level.price for level in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids must be sorted best (highest) first")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks must be sorted best (lowest) first")
        return self

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def observed_at(self) -> datetime:
        """The instant the quote is known to be valid (venue timestamp if provided)."""
        if self.source_timestamp is not None:
            return min(self.source_timestamp, self.received_at)
        return self.received_at

    def age(self, now: datetime) -> timedelta:
        return now - self.observed_at

    def is_stale(self, now: datetime, stale_after_seconds: int) -> bool:
        return self.age(now) > timedelta(seconds=stale_after_seconds)


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------


class CheckResult(DomainModel):
    name: str
    status: CheckStatus
    critical: bool
    kalshi_value: str | None = None
    polymarket_value: str | None = None
    detail: str = ""


class SemanticJudgement(DomainModel):
    """Stage-5 structured output (identical schema for rule-based and LLM adjudication)."""

    relation: Relation
    confidence: Decimal
    shared_event: bool
    same_resolution_criteria: bool
    differences: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    safe_for_cross_venue_arbitrage: bool
    reason: str

    @field_validator("confidence")
    @classmethod
    def _unit(cls, value: Decimal) -> Decimal:
        if not ZERO <= value <= ONE:
            raise ValueError("confidence must be within [0, 1]")
        return value


class LegMapping(DomainModel):
    """Which outcome sides combine into a guaranteed payout for an approved pair."""

    kalshi_side: OutcomeSide
    polymarket_side: OutcomeSide
    label: str


class MarketPair(DomainModel):
    id: str
    kalshi_market_id: str
    polymarket_market_id: str
    kalshi_title: str
    polymarket_title: str
    similarity_score: Decimal
    relation: Relation
    confidence: Decimal
    deterministic_checks: tuple[CheckResult, ...]
    semantic_explanation: SemanticJudgement
    blocking_mismatches: tuple[str, ...]
    adjudication_source: AdjudicationSource
    approved_for_arbitrage_calculation: bool
    decision_reasons: tuple[str, ...]
    leg_mappings: tuple[LegMapping, ...] = ()
    propositions: dict[str, Any] = Field(default_factory=dict)
    data_source: DataSource
    evaluated_at: datetime


# --------------------------------------------------------------------------------------
# Opportunities
# --------------------------------------------------------------------------------------


class LevelFill(DomainModel):
    """Quantity planned (or simulated) against one price level."""

    price: Decimal
    quantity: Decimal
    fee: Decimal


class ArbitrageLeg(DomainModel):
    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    action: str = "buy"
    quantity: Decimal
    best_price: Decimal
    worst_price: Decimal
    vwap: Decimal
    cost: Decimal
    fee: Decimal
    fills: tuple[LevelFill, ...]
    book_observed_at: datetime
    book_hash: str
    derived_asks: bool = False


class ProfitPoint(DomainModel):
    """Cumulative economics after consuming ``quantity`` units along both books."""

    quantity: Decimal
    cumulative_cost: Decimal
    cumulative_fees: Decimal
    cumulative_buffers: Decimal
    cumulative_net_profit: Decimal
    marginal_net_per_unit: Decimal


class PayoffState(DomainModel):
    kalshi_yes: bool
    polymarket_yes: bool
    payout_per_unit: Decimal


class ArbitrageOpportunity(DomainModel):
    id: str
    pair_id: str | None
    strategy_type: StrategyType
    direction: str
    relation: Relation | None = None
    status: OpportunityStatus
    detected_at: datetime
    book_timestamps: dict[str, datetime]
    legs: tuple[ArbitrageLeg, ...]
    max_executable_quantity: Decimal
    top_of_book_cost_per_unit: Decimal | None
    top_of_book_net_per_unit: Decimal | None
    gross_cost: Decimal
    guaranteed_payout: Decimal
    explicit_fees: Decimal
    slippage: Decimal
    safety_buffer: Decimal
    expected_net_profit: Decimal
    return_on_capital: Decimal | None
    stale_after: datetime
    assumptions: tuple[str, ...]
    rejection_reason: str | None = None
    profit_curve: tuple[ProfitPoint, ...] = ()
    payoff_states: tuple[PayoffState, ...] = ()
    data_source: DataSource
    label: str = "candidate arbitrage"

    @property
    def executable(self) -> bool:
        return self.status is OpportunityStatus.VALIDATED
