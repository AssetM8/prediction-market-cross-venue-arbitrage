"""Enumerations shared across the domain."""

from __future__ import annotations

from enum import StrEnum


class Venue(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


class DataSource(StrEnum):
    """Provenance of a record. Fixture data is never presented as live."""

    LIVE = "live"
    FIXTURE = "fixture"


class MarketStatus(StrEnum):
    ACTIVE = "active"
    NOT_OPEN = "not_open"
    PAUSED = "paused"
    CLOSED = "closed"
    RESOLVING = "resolving"
    SETTLED = "settled"
    UNKNOWN = "unknown"


class OutcomeSide(StrEnum):
    YES = "yes"
    NO = "no"

    @property
    def opposite(self) -> OutcomeSide:
        return OutcomeSide.NO if self is OutcomeSide.YES else OutcomeSide.YES


class Relation(StrEnum):
    """Logical relation between proposition A (Kalshi YES) and B (Polymarket YES)."""

    EQUIVALENT = "EQUIVALENT"
    COMPLEMENTARY = "COMPLEMENTARY"
    A_IMPLIES_B = "A_IMPLIES_B"
    B_IMPLIES_A = "B_IMPLIES_A"
    MUTUALLY_EXCLUSIVE = "MUTUALLY_EXCLUSIVE"
    PARTIALLY_OVERLAPPING = "PARTIALLY_OVERLAPPING"
    UNRELATED = "UNRELATED"
    AMBIGUOUS = "AMBIGUOUS"


#: Relations eligible for the MVP cross-venue execution calculation.
EXECUTABLE_RELATIONS = frozenset({Relation.EQUIVALENT, Relation.COMPLEMENTARY})


class CheckStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - enum label, not a password
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class AdjudicationSource(StrEnum):
    DETERMINISTIC = "deterministic"
    DETERMINISTIC_WITH_LLM_VETO = "deterministic_with_llm_veto"


class StrategyType(StrEnum):
    CROSS_VENUE_BINARY = "cross_venue_binary"
    MARKET_REBALANCING_LONG = "market_rebalancing_long"
    MARKET_REBALANCING_SHORT = "market_rebalancing_short"
    COMBINATORIAL = "combinatorial"


class OpportunityStatus(StrEnum):
    """Lifecycle label of an opportunity.

    ``VALIDATED`` means contract equivalence *and* executable depth passed every gate; it
    is the only status that can be paper-executed. ``CANDIDATE`` means a positive edge
    exists but a threshold (minimum profit, return, depth) was not met, so it remains
    "candidate arbitrage". ``NOT_PROFITABLE`` and ``SUPPRESSED`` carry a rejection reason.
    """

    VALIDATED = "validated"
    CANDIDATE = "candidate"
    NOT_PROFITABLE = "not_profitable"
    SUPPRESSED = "suppressed"


class OrderAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PaperOrderStatus(StrEnum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ExecutionScenario(StrEnum):
    """Deterministic fault injection for the paper simulator."""

    NORMAL = "normal"
    SECOND_LEG_REJECT = "second_leg_reject"
    SECOND_LEG_PARTIAL = "second_leg_partial"
    PRICE_MOVE_BEFORE_SECOND_LEG = "price_move_before_second_leg"
    STALE_QUOTE = "stale_quote"
    TIMEOUT = "timeout"
    VENUE_UNAVAILABLE = "venue_unavailable"


class ExecutionOutcome(StrEnum):
    HEDGED = "hedged"
    PARTIALLY_HEDGED_UNWOUND = "partially_hedged_unwound"
    UNHEDGED_RESIDUAL = "unhedged_residual"
    REJECTED = "rejected"
    KILL_SWITCH_BLOCKED = "kill_switch_blocked"
