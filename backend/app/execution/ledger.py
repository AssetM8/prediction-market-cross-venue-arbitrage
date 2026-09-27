"""Paper ledger: per-venue cash, positions, hedged bundles, P&L and the kill switch.

Cash is tracked per venue because capital on Kalshi and Polymarket cannot be netted
(funding fragmentation): an opportunity is only executable if *each* venue's paper account
can pay for its own leg.

P&L views:

* realized - from simulated unwinds (sell proceeds minus cost basis and fees);
* unrealized - positions marked at the current best *bid* (conservative liquidation value);
* locked-in - for verified hedged bundles, guaranteed settlement payout minus cost including
  fees. It is only as good as the pair's equivalence; basis risk is not modelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.decimal_utils import ZERO
from app.domain.enums import OrderAction, OutcomeSide, Venue
from app.domain.paper import (
    HedgedBundle,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
    VenueCash,
)


def position_key(venue: Venue, market_id: str, outcome_id: str) -> str:
    return f"{venue.value}:{market_id}:{outcome_id}"


@dataclass
class PositionState:
    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    fees_paid: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    hedged_quantity: Decimal = ZERO
    updated_at: datetime | None = None

    @property
    def average_cost(self) -> Decimal:
        return self.cost_basis / self.quantity if self.quantity > ZERO else ZERO


@dataclass
class PaperLedger:
    starting_cash: dict[Venue, Decimal]
    cash: dict[Venue, Decimal]
    positions: dict[str, PositionState] = field(default_factory=dict)
    bundles: list[HedgedBundle] = field(default_factory=list)
    consecutive_failures: int = 0
    kill_switch_engaged: bool = False

    @classmethod
    def fresh(cls, starting: dict[Venue, Decimal]) -> PaperLedger:
        return cls(starting_cash=dict(starting), cash=dict(starting))

    def available_cash(self, venue: Venue) -> Decimal:
        return self.cash.get(venue, ZERO)

    def apply_order(self, order: PaperOrder, now: datetime) -> Decimal:
        """Book an order's fills; returns realized P&L produced by the order (sells only)."""
        if order.filled_quantity <= ZERO:
            return ZERO
        key = position_key(order.venue, order.market_id, order.outcome_id)
        state = self.positions.setdefault(
            key,
            PositionState(
                venue=order.venue,
                market_id=order.market_id,
                outcome_id=order.outcome_id,
                outcome_side=order.outcome_side,
            ),
        )
        notional = sum((fill.price * fill.quantity for fill in order.fills), ZERO)
        state.updated_at = now
        state.fees_paid += order.fees
        if order.action is OrderAction.BUY:
            state.quantity += order.filled_quantity
            state.cost_basis += notional
            self.cash[order.venue] = self.available_cash(order.venue) - notional - order.fees
            return ZERO
        if order.filled_quantity > state.quantity:
            raise ValueError("paper sell exceeds position; short selling is not simulated")
        released_basis = state.average_cost * order.filled_quantity
        state.quantity -= order.filled_quantity
        state.cost_basis -= released_basis
        realized = notional - released_basis - order.fees
        state.realized_pnl += realized
        self.cash[order.venue] = self.available_cash(order.venue) + notional - order.fees
        return realized

    def add_bundle(self, bundle: HedgedBundle, legs: list[tuple[str, Decimal]]) -> None:
        self.bundles.append(bundle)
        for key, quantity in legs:
            if key in self.positions:
                self.positions[key].hedged_quantity += quantity

    def record_outcome(self, failure: bool, max_failures: int) -> None:
        if failure:
            self.consecutive_failures += 1
            if self.consecutive_failures >= max_failures:
                self.kill_switch_engaged = True
        else:
            self.consecutive_failures = 0

    def reset_kill_switch(self) -> None:
        self.kill_switch_engaged = False
        self.consecutive_failures = 0

    def residual_notional(self) -> Decimal:
        total = ZERO
        for state in self.positions.values():
            residual = state.quantity - state.hedged_quantity
            if residual > ZERO:
                total += residual * state.average_cost
        return total

    def portfolio(self, now: datetime, marks: dict[str, Decimal | None]) -> PaperPortfolio:
        positions = []
        unrealized = ZERO
        for key, state in sorted(self.positions.items()):
            mark = marks.get(key)
            pnl = None
            if mark is not None and state.quantity > ZERO:
                pnl = state.quantity * mark - state.cost_basis
                unrealized += pnl
            positions.append(
                PaperPosition(
                    key=key,
                    venue=state.venue,
                    market_id=state.market_id,
                    outcome_id=state.outcome_id,
                    outcome_side=state.outcome_side,
                    quantity=state.quantity,
                    average_cost=state.average_cost,
                    cost_basis=state.cost_basis,
                    fees_paid=state.fees_paid,
                    realized_pnl=state.realized_pnl,
                    mark_price=mark,
                    unrealized_pnl=pnl,
                    updated_at=state.updated_at or now,
                )
            )
        return PaperPortfolio(
            as_of=now,
            cash=tuple(
                VenueCash(venue=venue, starting_cash=self.starting_cash.get(venue, ZERO), cash=amount)
                for venue, amount in sorted(self.cash.items(), key=lambda item: item[0].value)
            ),
            positions=tuple(positions),
            hedged_bundles=tuple(self.bundles),
            realized_pnl=sum((s.realized_pnl for s in self.positions.values()), ZERO),
            unrealized_pnl=unrealized,
            locked_in_pnl=sum((b.locked_in_pnl for b in self.bundles), ZERO),
            total_fees=sum((s.fees_paid for s in self.positions.values()), ZERO),
            residual_exposure_notional=self.residual_notional(),
            kill_switch_engaged=self.kill_switch_engaged,
            consecutive_failures=self.consecutive_failures,
        )
