"""Property-based tests for the arbitrage and execution invariants."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.arbitrage.depth import BundleEconomics, Ladder, plan_bundle
from app.core.clock import FrozenClock
from app.core.config import ExecutionPolicy
from app.domain.enums import ExecutionOutcome, ExecutionScenario, Venue
from app.domain.models import BookLevel, FeeMetadata
from app.execution.ledger import PaperLedger
from app.execution.simulator import ExecutionConfig, PaperExecutionEngine
from tests.unit.test_paper_execution import FEES, setup

D = Decimal

prices = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.99"), places=2)
sizes = st.decimals(min_value=Decimal(1), max_value=Decimal(500), places=0)
ladder = st.lists(st.tuples(prices, sizes), min_size=1, max_size=6)


def to_levels(raw: list[tuple[Decimal, Decimal]]) -> tuple[BookLevel, ...]:
    merged: dict[Decimal, Decimal] = {}
    for price, size in raw:
        merged[price] = merged.get(price, D(0)) + size
    return tuple(BookLevel(price=p, quantity=q) for p, q in sorted(merged.items()))


def fee(rate: Decimal) -> FeeMetadata:
    return FeeMetadata(model="polymarket_curve", rate=rate, rounding_quantum=D("0.00001"), source="test")


ECON = BundleEconomics(
    payout_per_unit=D(1),
    settlement_buffer_per_unit=D("0.001"),
    latency_buffer_per_unit=D("0.001"),
    capital_cost_rate=D("0.001"),
)


@given(
    ladder,
    ladder,
    st.decimals(min_value=0, max_value=Decimal("0.10"), places=3),
    st.decimals(min_value=0, max_value=Decimal("0.10"), places=3),
)
@settings(max_examples=150, deadline=None)
def test_net_profit_never_increases_with_fees(a: list, b: list, low: Decimal, bump: Decimal) -> None:  # type: ignore[type-arg]
    high = low + bump
    cheap = plan_bundle(
        [Ladder("a", to_levels(a), fee(low)), Ladder("b", to_levels(b), fee(low))],
        ECON,
        quantity_step=D(1),
        max_quantity=D(5000),
    )
    dear = plan_bundle(
        [Ladder("a", to_levels(a), fee(high)), Ladder("b", to_levels(b), fee(high))],
        ECON,
        quantity_step=D(1),
        max_quantity=D(5000),
    )
    cheap_net = cheap.economics.net_profit if cheap.economics else D(0)
    dear_net = dear.economics.net_profit if dear.economics else D(0)
    assert dear_net <= cheap_net


@given(ladder, ladder, st.decimals(min_value=1, max_value=2000, places=0))
@settings(max_examples=150, deadline=None)
def test_executable_quantity_bounded_by_every_book(a: list, b: list, cap: Decimal) -> None:  # type: ignore[type-arg]
    left, right = Ladder("a", to_levels(a), fee(D(0))), Ladder("b", to_levels(b), fee(D(0)))
    plan = plan_bundle([left, right], ECON, quantity_step=D(1), max_quantity=cap)
    assert plan.quantity <= left.depth
    assert plan.quantity <= right.depth
    assert plan.quantity <= cap
    if plan.economics is not None:
        assert plan.economics.net_profit > 0
        for leg in plan.economics.legs:
            assert sum(f.quantity for f in leg.fills) == plan.quantity


@given(
    st.integers(min_value=31, max_value=86400),
    st.sampled_from(list(ExecutionScenario)),
    st.sampled_from(list(ExecutionPolicy)),
)
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_stale_opportunities_cannot_execute(
    age: int, scenario: ExecutionScenario, policy: ExecutionPolicy
) -> None:
    opportunity, books = setup()
    clock = FrozenClock(opportunity.stale_after + timedelta(seconds=age - 30))
    engine = PaperExecutionEngine(
        ExecutionConfig(
            policy=policy,
            max_hedge_slippage=D("0.02"),
            max_unhedged_notional=D(25),
            unwind_haircut=D("0.01"),
            kill_switch_max_failures=3,
            partial_fill_fraction=D("0.4"),
            adverse_price_move=D("0.03"),
            quantity_step=D(1),
        ),
        PaperLedger.fresh({Venue.KALSHI: D(1000), Venue.POLYMARKET: D(1000)}),
        clock,
    )
    result = engine.simulate_hedged_execution(
        opportunity, books, FEES, quantity=D(10), scenario=scenario, idempotency_key="k", execution_id="e"
    )
    assert result.outcome is ExecutionOutcome.REJECTED
    assert result.orders == ()
    assert engine.ledger.positions == {}


@given(
    st.decimals(allow_nan=False, allow_infinity=False, places=4),
    st.decimals(allow_nan=False, allow_infinity=False, places=2),
)
@settings(max_examples=200, deadline=None)
def test_invalid_prices_and_depth_are_rejected(price: Decimal, quantity: Decimal) -> None:
    valid = D(0) < price < D(1) and quantity > 0
    if valid:
        assert BookLevel(price=price, quantity=quantity).price == price
    else:
        with pytest.raises(ValidationError):
            BookLevel(price=price, quantity=quantity)
