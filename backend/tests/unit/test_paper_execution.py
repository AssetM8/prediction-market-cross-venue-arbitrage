"""Paper execution simulator: fills, leg risk, unwind, policies, kill switch, cash."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.arbitrage.cross_venue import ArbitrageConfig, CrossVenueCalculator
from app.core.clock import FrozenClock
from app.core.config import ExecutionPolicy
from app.domain.enums import (
    DataSource,
    ExecutionOutcome,
    ExecutionScenario,
    OrderAction,
    OutcomeSide,
    PaperOrderStatus,
    Relation,
    Venue,
)
from app.domain.interfaces import PaperOrderRequest
from app.domain.models import ArbitrageOpportunity, NormalizedOrderBook
from app.domain.paper import PaperExecution
from app.execution.ledger import PaperLedger
from app.execution.simulator import ExecutionConfig, PaperExecutionEngine
from tests.conftest import KALSHI_FEE, NO_FEE, NOW, POLY_FEE, levels, make_market, make_pair, make_snapshot

D = Decimal
ARB = ArbitrageConfig(
    stale_after_seconds=30,
    min_top_of_book_depth=D(1),
    min_net_profit=D(0),
    min_return_on_capital=D(0),
    settlement_buffer=D(0),
    latency_buffer=D(0),
    capital_cost_annual_rate=D(0),
    quantity_step=D(1),
    max_trade_quantity=D(10000),
)


def exec_config(**overrides: object) -> ExecutionConfig:
    values: dict[str, object] = {
        "policy": ExecutionPolicy.SEQUENTIAL,
        "max_hedge_slippage": D("0.02"),
        "max_unhedged_notional": D(25),
        "unwind_haircut": D("0.01"),
        "kill_switch_max_failures": 3,
        "partial_fill_fraction": D("0.4"),
        "adverse_price_move": D("0.05"),
        "quantity_step": D(1),
    }
    values.update(overrides)
    return ExecutionConfig(**values)  # type: ignore[arg-type]


def setup(fees: bool = False) -> tuple[ArbitrageOpportunity, dict[str, NormalizedOrderBook]]:
    k_market = make_market("K", Venue.KALSHI, fee=KALSHI_FEE if fees else NO_FEE)
    p_market = make_market("P", Venue.POLYMARKET, fee=POLY_FEE if fees else NO_FEE)
    k = make_snapshot(
        "K",
        Venue.KALSHI,
        yes_asks=levels(("0.40", "100")),
        no_asks=levels(("0.70", "100")),
        yes_bids=levels(("0.36", "60"), ("0.33", "100")),
    )
    p = make_snapshot(
        "P",
        Venue.POLYMARKET,
        yes_asks=levels(("0.62", "100")),
        no_asks=levels(("0.50", "100"), ("0.51", "100")),
        no_bids=levels(("0.46", "100")),
    )
    opp = CrossVenueCalculator(ARB).evaluate_pair(
        make_pair(Relation.EQUIVALENT), k_market, p_market, k, p, now=NOW, data_source=DataSource.FIXTURE
    )[0]
    books = {b.outcome_id: b for snap in (k, p) for b in (snap.yes, snap.no)}
    return opp, books


FEES = {Venue.KALSHI: NO_FEE, Venue.POLYMARKET: NO_FEE}


def engine(
    config: ExecutionConfig | None = None, ledger: PaperLedger | None = None, clock: FrozenClock | None = None
) -> PaperExecutionEngine:
    return PaperExecutionEngine(
        config or exec_config(),
        ledger or PaperLedger.fresh({Venue.KALSHI: D(1000), Venue.POLYMARKET: D(1000)}),
        clock or FrozenClock(NOW),
    )


def run(
    eng: PaperExecutionEngine,
    scenario: ExecutionScenario = ExecutionScenario.NORMAL,
    quantity: str = "100",
    key: str = "k1",
) -> PaperExecution:
    opp, books = setup()
    return eng.simulate_hedged_execution(
        opp,
        books,
        FEES,
        quantity=D(quantity),
        scenario=scenario,
        idempotency_key=key,
        execution_id=f"exec-{key}",
    )


def test_both_legs_fill_and_bundle_is_hedged() -> None:
    eng = engine()
    result = run(eng)
    assert result.outcome is ExecutionOutcome.HEDGED
    assert result.hedged_quantity == D(100)
    assert result.residual_quantity == 0
    assert result.expected_locked_in_pnl == D(10)  # 100 - 40 - 50
    assert [o.status for o in result.orders] == [PaperOrderStatus.FILLED, PaperOrderStatus.FILLED]
    assert all(o.simulated for o in result.orders)
    portfolio = eng.ledger.portfolio(NOW, {})
    assert portfolio.locked_in_pnl == D(10)
    cash = {c.venue: c.cash for c in portfolio.cash}
    assert cash[Venue.KALSHI] == D(960) and cash[Venue.POLYMARKET] == D(950)  # funding is per venue
    assert not result.failure


def test_second_leg_reject_unwinds_large_residual() -> None:
    eng = engine()
    result = run(eng, ExecutionScenario.SECOND_LEG_REJECT)
    assert result.orders[1].status is PaperOrderStatus.REJECTED
    unwind = result.orders[2]
    assert unwind.purpose == "unwind" and unwind.action is OrderAction.SELL
    # residual 100 @ 0.40 = 40 > 25: sell into bids >= 0.36 - 0.01 -> 60 @ 0.36 only
    assert unwind.filled_quantity == D(60)
    assert result.residual_quantity == D(40)
    assert result.outcome is ExecutionOutcome.UNHEDGED_RESIDUAL
    assert result.realized_pnl == D(60) * (D("0.36") - D("0.40"))
    assert result.failure


def test_second_leg_partial_fill_leaves_small_residual_open() -> None:
    eng = engine(exec_config(max_unhedged_notional=D(1000)))
    result = run(eng, ExecutionScenario.SECOND_LEG_PARTIAL)
    assert result.orders[1].status is PaperOrderStatus.PARTIALLY_FILLED
    assert result.hedged_quantity == D(40)
    assert result.residual_quantity == D(60)
    assert result.residual_notional == D(24)
    assert result.outcome is ExecutionOutcome.UNHEDGED_RESIDUAL
    assert eng.ledger.residual_notional() == D(24)


def test_partial_fill_residual_fully_unwound() -> None:
    eng = engine(exec_config(max_unhedged_notional=D(10)))
    opp, books = setup()
    result = eng.simulate_hedged_execution(
        opp,
        books,
        FEES,
        quantity=D(50),
        scenario=ExecutionScenario.SECOND_LEG_PARTIAL,
        idempotency_key="p",
        execution_id="exec-p",
    )
    assert result.hedged_quantity == D(20)
    assert result.residual_quantity == 0
    assert result.outcome is ExecutionOutcome.PARTIALLY_HEDGED_UNWOUND


def test_price_move_before_second_leg_respects_max_hedge_slippage() -> None:
    eng = engine(
        exec_config(adverse_price_move=D("0.05"), max_hedge_slippage=D("0.02"), max_unhedged_notional=D(1000))
    )
    result = run(eng, ExecutionScenario.PRICE_MOVE_BEFORE_SECOND_LEG)
    # asks moved 0.50 -> 0.55 but limit is 0.50 + 0.02
    assert result.orders[1].filled_quantity == 0
    assert any("moved against us" in step.message for step in result.steps)
    loose = engine(exec_config(adverse_price_move=D("0.01"), max_hedge_slippage=D("0.02")))
    moved = run(loose, ExecutionScenario.PRICE_MOVE_BEFORE_SECOND_LEG)
    assert moved.outcome is ExecutionOutcome.HEDGED
    assert moved.orders[1].average_price == D("0.51")
    assert moved.expected_locked_in_pnl == D(9)


def test_simultaneous_policy_ignores_between_leg_moves() -> None:
    eng = engine(exec_config(policy=ExecutionPolicy.SIMULTANEOUS_IOC))
    result = run(eng, ExecutionScenario.PRICE_MOVE_BEFORE_SECOND_LEG)
    assert result.outcome is ExecutionOutcome.HEDGED
    assert result.orders[0].created_at == result.orders[1].created_at


def test_stale_quote_timeout_and_venue_unavailable() -> None:
    stale = run(engine(), ExecutionScenario.STALE_QUOTE)
    assert stale.outcome is ExecutionOutcome.REJECTED and "stale quote" in stale.reason
    assert stale.orders == ()
    timeout = run(engine(exec_config(max_unhedged_notional=D(1000))), ExecutionScenario.TIMEOUT)
    assert timeout.orders[1].status is PaperOrderStatus.TIMED_OUT
    assert timeout.residual_quantity == D(100)
    outage = run(engine(exec_config(max_unhedged_notional=D(1000))), ExecutionScenario.VENUE_UNAVAILABLE)
    assert outage.orders[1].reject_reason is not None and "unavailable" in outage.orders[1].reject_reason


def test_real_clock_staleness_rejects_before_any_order() -> None:
    clock = FrozenClock(NOW + timedelta(seconds=31))
    result = run(engine(clock=clock))
    assert result.outcome is ExecutionOutcome.REJECTED and not result.orders


def test_kill_switch_after_repeated_failures() -> None:
    eng = engine(exec_config(kill_switch_max_failures=2, max_unhedged_notional=D(1000)))
    run(eng, ExecutionScenario.SECOND_LEG_REJECT, quantity="10", key="a")
    assert not eng.ledger.kill_switch_engaged
    second = run(eng, ExecutionScenario.SECOND_LEG_REJECT, quantity="10", key="b")
    assert eng.ledger.portfolio(NOW, {}).kill_switch_engaged
    assert any("kill switch engaged" in step.message for step in second.steps)
    blocked = run(eng, ExecutionScenario.NORMAL, quantity="10", key="c")
    assert blocked.outcome is ExecutionOutcome.KILL_SWITCH_BLOCKED and not blocked.orders
    eng.ledger.reset_kill_switch()
    assert run(eng, ExecutionScenario.NORMAL, quantity="10", key="d").outcome is ExecutionOutcome.HEDGED


def test_success_resets_failure_counter() -> None:
    eng = engine(exec_config(kill_switch_max_failures=2, max_unhedged_notional=D(1000)))
    run(eng, ExecutionScenario.SECOND_LEG_REJECT, quantity="10", key="a")
    run(eng, ExecutionScenario.NORMAL, quantity="10", key="b")
    assert eng.ledger.consecutive_failures == 0


def test_insufficient_cash_on_one_venue_rejects() -> None:
    ledger = PaperLedger.fresh({Venue.KALSHI: D(1000), Venue.POLYMARKET: D(10)})
    result = run(engine(ledger=ledger))
    assert (
        result.outcome is ExecutionOutcome.REJECTED
        and "insufficient paper cash on polymarket" in result.reason
    )


def test_quantity_validation() -> None:
    assert "must be a positive multiple" in run(engine(), quantity="101").reason
    assert "must be a positive multiple" in run(engine(), quantity="0.5").reason


def test_simulate_order_limits_and_cancel() -> None:
    eng = engine()
    _, books = setup()
    request = PaperOrderRequest(
        execution_id="e",
        opportunity_id="o",
        leg_index=1,
        purpose="entry",
        venue=Venue.POLYMARKET,
        market_id="P",
        outcome_id="P:no",
        outcome_side=OutcomeSide.NO,
        action=OrderAction.BUY,
        quantity=D(150),
        limit_price=D("0.50"),
        fee=POLY_FEE,
    )
    order = eng.simulate_order(request, books["P:no"])
    assert order.filled_quantity == D(100)  # 0.51 level exceeds the limit
    assert order.status is PaperOrderStatus.PARTIALLY_FILLED
    assert order.fees == D("1.25")
    assert eng.cancel_simulated_order(order).status is PaperOrderStatus.CANCELLED
    full = eng.simulate_order(request.model_copy(update={"quantity": D(10)}), books["P:no"])
    with pytest.raises(ValueError, match="fully filled"):
        eng.cancel_simulated_order(full)
