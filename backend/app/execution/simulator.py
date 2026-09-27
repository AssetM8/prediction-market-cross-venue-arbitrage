"""Deterministic paper execution simulator for non-atomic, two-venue trades.

Cross-venue arbitrage is never atomic: one leg can fill while the other rejects, fills
partially, times out, meets a moved price or a venue outage. This engine simulates those
paths against stored order-book snapshots, with scenario injection so every path is
reproducible in tests and the demo.

Policies:

* ``sequential`` - send leg 1 (Kalshi) as IOC, then leg 2 (Polymarket) for the quantity
  leg 1 actually filled, with a limit of the planned worst price plus
  ``max_hedge_slippage``. A simulated latency separates the legs.
* ``simultaneous_ioc`` - send both IOC orders at the same instant against the same
  snapshot (the price-move scenario cannot occur between legs).

Any unhedged residual whose notional exceeds ``max_unhedged_notional`` is unwound by an IOC
sell into the leg's bids (limit = best bid - ``unwind_haircut``); smaller residuals are kept
and reported as exposure. ``kill_switch_max_failures`` consecutive failed executions engage
the kill switch, which blocks further executions until reset.

Nothing here performs I/O. Orders are :class:`PaperOrder` records with ``simulated=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.arbitrage.fees import fill_fee
from app.core.clock import Clock
from app.core.config import ExecutionPolicy
from app.core.decimal_utils import ONE, ZERO, floor_to_step
from app.core.ids import stable_id
from app.core.metrics import METRICS
from app.domain.enums import (
    ExecutionOutcome,
    ExecutionScenario,
    OpportunityStatus,
    OrderAction,
    PaperOrderStatus,
    StrategyType,
    Venue,
)
from app.domain.interfaces import PaperOrderRequest
from app.domain.models import ArbitrageLeg, ArbitrageOpportunity, BookLevel, FeeMetadata, NormalizedOrderBook
from app.domain.paper import ExecutionStep, HedgedBundle, PaperExecution, PaperFill, PaperOrder
from app.execution.ledger import PaperLedger, position_key

MIN_PRICE = Decimal("0.0001")
MAX_PRICE = Decimal("0.9999")


class ExecutionRejectedError(ValueError):
    """Pre-trade validation failed (nothing was simulated)."""


@dataclass(frozen=True)
class ExecutionConfig:
    policy: ExecutionPolicy
    max_hedge_slippage: Decimal
    max_unhedged_notional: Decimal
    unwind_haircut: Decimal
    kill_switch_max_failures: int
    partial_fill_fraction: Decimal
    adverse_price_move: Decimal
    quantity_step: Decimal
    leg_latency: timedelta = timedelta(milliseconds=250)


def _shift_asks(book: NormalizedOrderBook, move: Decimal) -> NormalizedOrderBook:
    shifted = tuple(
        BookLevel(price=min(MAX_PRICE, level.price + move), quantity=level.quantity) for level in book.asks
    )
    merged: dict[Decimal, Decimal] = {}
    for level in shifted:
        merged[level.price] = merged.get(level.price, ZERO) + level.quantity
    asks = tuple(BookLevel(price=p, quantity=q) for p, q in sorted(merged.items()))
    return book.model_copy(update={"asks": asks})


class PaperExecutionEngine:
    """Implements :class:`app.domain.interfaces.PaperExecutionVenue` over a :class:`PaperLedger`."""

    def __init__(self, config: ExecutionConfig, ledger: PaperLedger, clock: Clock) -> None:
        self._config = config
        self._ledger = ledger
        self._clock = clock

    @property
    def ledger(self) -> PaperLedger:
        return self._ledger

    # ---- single order -------------------------------------------------------------------

    def simulate_order(
        self,
        request: PaperOrderRequest,
        book: NormalizedOrderBook,
        *,
        fill_cap: Decimal | None = None,
        at: datetime | None = None,
        forced_status: PaperOrderStatus | None = None,
        reject_reason: str | None = None,
    ) -> PaperOrder:
        """Simulate one IOC order against ``book`` (buys lift asks, sells hit bids)."""
        created = at or self._clock.now()
        order_id = stable_id("po", request.execution_id, request.leg_index, request.purpose)
        if forced_status is not None:
            return self._order(request, order_id, created, (), forced_status, reject_reason)
        cap = request.quantity if fill_cap is None else min(request.quantity, fill_cap)
        levels = book.asks if request.action is OrderAction.BUY else book.bids
        remaining = cap
        fills: list[PaperFill] = []
        for level in levels:
            if remaining <= ZERO:
                break
            crosses = (
                level.price <= request.limit_price
                if request.action is OrderAction.BUY
                else level.price >= request.limit_price
            )
            if not crosses:
                break
            quantity = min(level.quantity, remaining)
            fills.append(
                PaperFill(
                    id=stable_id("pf", order_id, len(fills)),
                    order_id=order_id,
                    price=level.price,
                    quantity=quantity,
                    fee=fill_fee(request.fee, level.price, quantity),
                    filled_at=created,
                )
            )
            remaining -= quantity
        filled = cap - remaining
        if filled <= ZERO:
            status = PaperOrderStatus.REJECTED
            reason = reject_reason or "no liquidity within limit price (IOC cancelled)"
        elif filled < request.quantity:
            status, reason = PaperOrderStatus.PARTIALLY_FILLED, reject_reason or "IOC remainder cancelled"
        else:
            status, reason = PaperOrderStatus.FILLED, None
        return self._order(request, order_id, created, tuple(fills), status, reason)

    @staticmethod
    def _order(
        request: PaperOrderRequest,
        order_id: str,
        created: datetime,
        fills: tuple[PaperFill, ...],
        status: PaperOrderStatus,
        reason: str | None,
    ) -> PaperOrder:
        filled = sum((f.quantity for f in fills), ZERO)
        notional = sum((f.price * f.quantity for f in fills), ZERO)
        return PaperOrder(
            id=order_id,
            execution_id=request.execution_id,
            opportunity_id=request.opportunity_id,
            leg_index=request.leg_index,
            purpose=request.purpose,
            venue=request.venue,
            market_id=request.market_id,
            outcome_id=request.outcome_id,
            outcome_side=request.outcome_side,
            action=request.action,
            requested_quantity=request.quantity,
            limit_price=request.limit_price,
            filled_quantity=filled,
            average_price=notional / filled if filled > ZERO else None,
            fees=sum((f.fee for f in fills), ZERO),
            status=status,
            reject_reason=reason,
            created_at=created,
            fills=fills,
        )

    def cancel_simulated_order(self, order: PaperOrder) -> PaperOrder:
        """IOC orders never rest; cancelling reports the unfilled remainder as cancelled."""
        if order.status is PaperOrderStatus.FILLED:
            raise ValueError("order is fully filled; nothing to cancel")
        return order.model_copy(update={"status": PaperOrderStatus.CANCELLED})

    # ---- two-leg execution ------------------------------------------------------------------

    def simulate_hedged_execution(
        self,
        opportunity: ArbitrageOpportunity,
        books: dict[str, NormalizedOrderBook],
        fees: dict[Venue, FeeMetadata],
        *,
        quantity: Decimal,
        scenario: ExecutionScenario,
        idempotency_key: str,
        execution_id: str,
    ) -> PaperExecution:
        cfg = self._config
        start = self._clock.now()
        steps: list[ExecutionStep] = []

        def step(at: datetime, message: str, **detail: object) -> None:
            steps.append(ExecutionStep(at=at, message=message, detail={k: str(v) for k, v in detail.items()}))

        step(
            start, "execution requested", quantity=quantity, scenario=scenario.value, policy=cfg.policy.value
        )

        def rejected(
            reason: str, *, failure: bool, outcome: ExecutionOutcome = ExecutionOutcome.REJECTED
        ) -> PaperExecution:
            step(start, f"rejected: {reason}")
            self._ledger.record_outcome(failure, cfg.kill_switch_max_failures)
            METRICS.inc("paper_executions_total", outcome=outcome.value)
            return self._result(
                execution_id,
                opportunity,
                idempotency_key,
                scenario,
                quantity,
                (),
                steps,
                start,
                outcome=outcome,
                failure=failure,
                reason=reason,
                hedged=ZERO,
                residual=ZERO,
                residual_notional=ZERO,
                realized=ZERO,
                locked=ZERO,
            )

        if self._ledger.kill_switch_engaged:
            return rejected(
                f"kill switch engaged after {self._ledger.consecutive_failures} consecutive failures",
                failure=False,
                outcome=ExecutionOutcome.KILL_SWITCH_BLOCKED,
            )
        if opportunity.strategy_type is not StrategyType.CROSS_VENUE_BINARY or len(opportunity.legs) != 2:
            return rejected("only validated cross-venue binary opportunities are executable", failure=False)
        if opportunity.status is not OpportunityStatus.VALIDATED:
            return rejected(f"opportunity status is {opportunity.status.value}, not validated", failure=False)
        effective_now = start
        if scenario is ExecutionScenario.STALE_QUOTE:
            effective_now = opportunity.stale_after + timedelta(seconds=1)
            step(effective_now, "scenario: quotes aged past the staleness threshold before submission")
        if effective_now > opportunity.stale_after:
            return rejected(
                f"stale quote: now {effective_now.isoformat()} is after stale_after "
                f"{opportunity.stale_after.isoformat()}",
                failure=False,
            )
        step_quantity = floor_to_step(quantity, cfg.quantity_step)
        if step_quantity <= ZERO or step_quantity > opportunity.max_executable_quantity:
            return rejected(
                f"requested quantity {quantity} must be a positive multiple of {cfg.quantity_step} "
                f"and at most {opportunity.max_executable_quantity}",
                failure=False,
            )
        leg1, leg2 = opportunity.legs
        for leg in (leg1, leg2):
            if leg.outcome_id not in books:
                return rejected(f"missing order book for {leg.venue.value}:{leg.outcome_id}", failure=False)
            if leg.venue not in fees:
                return rejected(f"missing fee metadata for {leg.venue.value}", failure=False)
            needed = step_quantity * leg.worst_price + fill_fee(
                fees[leg.venue], leg.worst_price, step_quantity
            )
            if self._ledger.available_cash(leg.venue) < needed:
                return rejected(
                    f"insufficient paper cash on {leg.venue.value}: need {needed}, have "
                    f"{self._ledger.available_cash(leg.venue)} (venue balances cannot be netted)",
                    failure=False,
                )

        orders: list[PaperOrder] = []
        sequential = cfg.policy is ExecutionPolicy.SEQUENTIAL
        t_leg1 = start
        t_leg2 = start + cfg.leg_latency if sequential else start

        order1 = self.simulate_order(
            self._request(execution_id, opportunity, 0, "entry", leg1, step_quantity, leg1.worst_price, fees),
            books[leg1.outcome_id],
            at=t_leg1,
        )
        orders.append(order1)
        self._ledger.apply_order(order1, t_leg1)
        step(
            t_leg1,
            f"leg 1 {order1.status.value}",
            venue=leg1.venue.value,
            filled=order1.filled_quantity,
            average_price=order1.average_price,
            fees=order1.fees,
            book_hash=leg1.book_hash,
        )
        if order1.filled_quantity <= ZERO:
            return self._finish(
                execution_id,
                opportunity,
                idempotency_key,
                scenario,
                step_quantity,
                orders,
                steps,
                start,
                books,
                fees,
                leg1,
                leg2,
                order1,
                None,
            )

        leg2_quantity = order1.filled_quantity if sequential else step_quantity
        leg2_book = books[leg2.outcome_id]
        limit2 = leg2.worst_price + (cfg.max_hedge_slippage if sequential else ZERO)
        limit2 = min(MAX_PRICE, limit2)
        request2 = self._request(execution_id, opportunity, 1, "entry", leg2, leg2_quantity, limit2, fees)
        order2: PaperOrder
        if scenario is ExecutionScenario.SECOND_LEG_REJECT:
            order2 = self.simulate_order(
                request2,
                leg2_book,
                at=t_leg2,
                forced_status=PaperOrderStatus.REJECTED,
                reject_reason="venue rejected the order (simulated)",
            )
        elif scenario is ExecutionScenario.TIMEOUT:
            order2 = self.simulate_order(
                request2,
                leg2_book,
                at=t_leg2,
                forced_status=PaperOrderStatus.TIMED_OUT,
                reject_reason="no acknowledgement before timeout; treated as unfilled",
            )
        elif scenario is ExecutionScenario.VENUE_UNAVAILABLE:
            order2 = self.simulate_order(
                request2,
                leg2_book,
                at=t_leg2,
                forced_status=PaperOrderStatus.REJECTED,
                reject_reason="venue unavailable (simulated connection failure)",
            )
        elif scenario is ExecutionScenario.SECOND_LEG_PARTIAL:
            cap = floor_to_step(leg2_quantity * cfg.partial_fill_fraction, cfg.quantity_step)
            order2 = self.simulate_order(
                request2,
                leg2_book,
                at=t_leg2,
                fill_cap=cap,
                reject_reason="partial fill: displayed depth was not available",
            )
        elif scenario is ExecutionScenario.PRICE_MOVE_BEFORE_SECOND_LEG and sequential:
            moved = _shift_asks(leg2_book, cfg.adverse_price_move)
            step(
                t_leg2,
                "scenario: leg 2 asks moved against us before submission",
                move=cfg.adverse_price_move,
                limit=limit2,
            )
            order2 = self.simulate_order(request2, moved, at=t_leg2)
        else:
            if scenario is ExecutionScenario.PRICE_MOVE_BEFORE_SECOND_LEG:
                step(t_leg2, "scenario not applicable: legs sent simultaneously against one snapshot")
            order2 = self.simulate_order(request2, leg2_book, at=t_leg2)
        orders.append(order2)
        self._ledger.apply_order(order2, t_leg2)
        step(
            t_leg2,
            f"leg 2 {order2.status.value}",
            venue=leg2.venue.value,
            filled=order2.filled_quantity,
            average_price=order2.average_price,
            fees=order2.fees,
            reason=order2.reject_reason,
            book_hash=leg2.book_hash,
        )
        return self._finish(
            execution_id,
            opportunity,
            idempotency_key,
            scenario,
            step_quantity,
            orders,
            steps,
            start,
            books,
            fees,
            leg1,
            leg2,
            order1,
            order2,
        )

    def _finish(
        self,
        execution_id: str,
        opportunity: ArbitrageOpportunity,
        idempotency_key: str,
        scenario: ExecutionScenario,
        quantity: Decimal,
        orders: list[PaperOrder],
        steps: list[ExecutionStep],
        start: datetime,
        books: dict[str, NormalizedOrderBook],
        fees: dict[Venue, FeeMetadata],
        leg1: ArbitrageLeg,
        leg2: ArbitrageLeg,
        order1: PaperOrder,
        order2: PaperOrder | None,
    ) -> PaperExecution:
        cfg = self._config
        filled1 = order1.filled_quantity
        filled2 = order2.filled_quantity if order2 else ZERO
        hedged = min(filled1, filled2)
        payout_per_unit = min((s.payout_per_unit for s in opportunity.payoff_states), default=ONE)
        locked = ZERO
        if hedged > ZERO and order2 is not None:
            cost1 = self._hedged_cost(order1, hedged)
            cost2 = self._hedged_cost(order2, hedged)
            locked = hedged * payout_per_unit - cost1 - cost2
            bundle = HedgedBundle(
                execution_id=execution_id,
                pair_id=opportunity.pair_id,
                quantity=hedged,
                cost_including_fees=cost1 + cost2,
                guaranteed_payout=hedged * payout_per_unit,
                locked_in_pnl=locked,
            )
            self._ledger.add_bundle(
                bundle,
                [
                    (position_key(order1.venue, order1.market_id, order1.outcome_id), hedged),
                    (position_key(order2.venue, order2.market_id, order2.outcome_id), hedged),
                ],
            )
        # residual sits on whichever leg filled more
        realized = ZERO
        residual_order, residual_leg, residual = (order1, leg1, filled1 - filled2)
        if order2 is not None and filled2 > filled1:
            residual_order, residual_leg, residual = (order2, leg2, filled2 - filled1)
        at = (order2.created_at if order2 else order1.created_at) + cfg.leg_latency
        if residual > ZERO:
            avg = residual_order.average_price or residual_leg.vwap
            notional = residual * avg
            steps.append(
                ExecutionStep(
                    at=at,
                    message="unhedged residual detected",
                    detail={
                        "quantity": str(residual),
                        "notional": str(notional),
                        "max_unhedged_notional": str(cfg.max_unhedged_notional),
                    },
                )
            )
            if notional > cfg.max_unhedged_notional:
                book = books[residual_leg.outcome_id]
                best_bid = book.best_bid.price if book.best_bid else MIN_PRICE
                limit = max(MIN_PRICE, best_bid - cfg.unwind_haircut)
                unwind = self.simulate_order(
                    self._request(
                        execution_id,
                        opportunity,
                        residual_order.leg_index,
                        "unwind",
                        residual_leg,
                        residual,
                        limit,
                        fees,
                        action=OrderAction.SELL,
                    ),
                    book,
                    at=at,
                )
                orders.append(unwind)
                realized = self._ledger.apply_order(unwind, at)
                residual -= unwind.filled_quantity
                steps.append(
                    ExecutionStep(
                        at=at,
                        message=f"unwind {unwind.status.value}",
                        detail={
                            "sold": str(unwind.filled_quantity),
                            "average_price": str(unwind.average_price),
                            "realized_pnl": str(realized),
                            "limit": str(limit),
                        },
                    )
                )
        residual_notional = residual * (residual_order.average_price or ZERO) if residual > ZERO else ZERO
        if hedged == quantity and residual <= ZERO:
            outcome, failure, reason = ExecutionOutcome.HEDGED, False, "both legs filled; bundle hedged"
        elif hedged == ZERO and residual <= ZERO:
            outcome, failure = ExecutionOutcome.REJECTED, True
            reason = "no hedge established; any partial entry was unwound"
        elif residual <= ZERO:
            outcome, failure = ExecutionOutcome.PARTIALLY_HEDGED_UNWOUND, True
            reason = f"hedged {hedged} of {quantity}; residual unwound (realized {realized})"
        else:
            outcome, failure = ExecutionOutcome.UNHEDGED_RESIDUAL, True
            reason = (
                f"hedged {hedged} of {quantity}; residual {residual} left open (notional {residual_notional})"
            )
        self._ledger.record_outcome(failure, cfg.kill_switch_max_failures)
        if self._ledger.kill_switch_engaged:
            steps.append(
                ExecutionStep(
                    at=at,
                    message="kill switch engaged",
                    detail={"consecutive_failures": str(self._ledger.consecutive_failures)},
                )
            )
        METRICS.inc("paper_executions_total", outcome=outcome.value)
        steps.append(ExecutionStep(at=at, message=f"final: {outcome.value}", detail={"reason": reason}))
        return self._result(
            execution_id,
            opportunity,
            idempotency_key,
            scenario,
            quantity,
            tuple(orders),
            steps,
            start,
            outcome=outcome,
            failure=failure,
            reason=reason,
            hedged=hedged,
            residual=max(residual, ZERO),
            residual_notional=residual_notional,
            realized=realized,
            locked=locked,
        )

    @staticmethod
    def _hedged_cost(order: PaperOrder, hedged: Decimal) -> Decimal:
        if order.filled_quantity <= ZERO or order.average_price is None:
            return ZERO
        share = hedged / order.filled_quantity
        return hedged * order.average_price + order.fees * share

    def _request(
        self,
        execution_id: str,
        opportunity: ArbitrageOpportunity,
        index: int,
        purpose: str,
        leg: ArbitrageLeg,
        quantity: Decimal,
        limit: Decimal,
        fees: dict[Venue, FeeMetadata],
        *,
        action: OrderAction = OrderAction.BUY,
    ) -> PaperOrderRequest:
        return PaperOrderRequest(
            execution_id=execution_id,
            opportunity_id=opportunity.id,
            leg_index=index,
            purpose=purpose,
            venue=leg.venue,
            market_id=leg.market_id,
            outcome_id=leg.outcome_id,
            outcome_side=leg.outcome_side,
            action=action,
            quantity=quantity,
            limit_price=limit,
            fee=fees[leg.venue],
        )

    def _result(
        self,
        execution_id: str,
        opportunity: ArbitrageOpportunity,
        idempotency_key: str,
        scenario: ExecutionScenario,
        quantity: Decimal,
        orders: tuple[PaperOrder, ...],
        steps: list[ExecutionStep],
        start: datetime,
        *,
        outcome: ExecutionOutcome,
        failure: bool,
        reason: str,
        hedged: Decimal,
        residual: Decimal,
        residual_notional: Decimal,
        realized: Decimal,
        locked: Decimal,
    ) -> PaperExecution:
        finished = steps[-1].at if steps else start
        return PaperExecution(
            id=execution_id,
            opportunity_id=opportunity.id,
            pair_id=opportunity.pair_id,
            idempotency_key=idempotency_key,
            policy=self._config.policy.value,
            scenario=scenario,
            requested_quantity=quantity,
            hedged_quantity=hedged,
            residual_quantity=residual,
            residual_notional=residual_notional,
            outcome=outcome,
            total_cost=sum(
                (
                    sum((f.price * f.quantity for f in o.fills), ZERO)
                    for o in orders
                    if o.action is OrderAction.BUY
                ),
                ZERO,
            ),
            total_fees=sum((o.fees for o in orders), ZERO),
            realized_pnl=realized,
            expected_locked_in_pnl=locked,
            orders=orders,
            steps=tuple(steps),
            started_at=start,
            finished_at=finished,
            failure=failure,
            reason=reason,
        )
