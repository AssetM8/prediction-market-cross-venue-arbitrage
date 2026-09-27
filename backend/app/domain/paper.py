"""Paper-trading models. Nothing here can reach a venue."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.domain.enums import (
    ExecutionOutcome,
    ExecutionScenario,
    OrderAction,
    OutcomeSide,
    PaperOrderStatus,
    Venue,
)
from app.domain.models import DomainModel


class PaperFill(DomainModel):
    id: str
    order_id: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    filled_at: datetime


class PaperOrder(DomainModel):
    """A simulated IOC order. ``simulated`` is always True; there is no live variant."""

    id: str
    execution_id: str
    opportunity_id: str
    leg_index: int
    purpose: str  # "entry" | "unwind"
    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    action: OrderAction
    requested_quantity: Decimal
    limit_price: Decimal
    filled_quantity: Decimal
    average_price: Decimal | None
    fees: Decimal
    status: PaperOrderStatus
    reject_reason: str | None = None
    created_at: datetime
    fills: tuple[PaperFill, ...] = ()
    simulated: bool = True


class PaperPosition(DomainModel):
    key: str
    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    quantity: Decimal
    average_cost: Decimal
    cost_basis: Decimal
    fees_paid: Decimal
    realized_pnl: Decimal
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    updated_at: datetime


class VenueCash(DomainModel):
    venue: Venue
    starting_cash: Decimal
    cash: Decimal


class HedgedBundle(DomainModel):
    """Quantity held as a verified two-leg bundle with a guaranteed settlement payout."""

    execution_id: str
    pair_id: str | None
    quantity: Decimal
    cost_including_fees: Decimal
    guaranteed_payout: Decimal
    locked_in_pnl: Decimal


class PaperPortfolio(DomainModel):
    as_of: datetime
    cash: tuple[VenueCash, ...]
    positions: tuple[PaperPosition, ...]
    hedged_bundles: tuple[HedgedBundle, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    locked_in_pnl: Decimal
    total_fees: Decimal
    residual_exposure_notional: Decimal
    kill_switch_engaged: bool
    consecutive_failures: int
    marks_source: str = "latest stored order books (best bid, conservative liquidation value)"


class ExecutionStep(DomainModel):
    at: datetime
    message: str
    detail: dict[str, str] = Field(default_factory=dict)


class PaperExecution(DomainModel):
    id: str
    opportunity_id: str
    pair_id: str | None
    idempotency_key: str
    policy: str
    scenario: ExecutionScenario
    requested_quantity: Decimal
    hedged_quantity: Decimal
    residual_quantity: Decimal
    residual_notional: Decimal
    outcome: ExecutionOutcome
    total_cost: Decimal
    total_fees: Decimal
    realized_pnl: Decimal
    expected_locked_in_pnl: Decimal
    orders: tuple[PaperOrder, ...]
    steps: tuple[ExecutionStep, ...]
    started_at: datetime
    finished_at: datetime
    failure: bool
    reason: str
