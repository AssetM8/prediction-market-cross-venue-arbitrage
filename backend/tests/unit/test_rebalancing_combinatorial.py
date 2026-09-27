from __future__ import annotations

from decimal import Decimal

import pytest

from app.arbitrage.combinatorial import (
    dependent_subsets,
    evaluate,
    is_dependent,
    min_payout,
    validate_vectors,
)
from app.arbitrage.cross_venue import ArbitrageConfig
from app.arbitrage.rebalancing import build_groups, evaluate_group
from app.domain.enums import DataSource, OpportunityStatus, StrategyType, Venue
from tests.conftest import NO_FEE, NOW, levels, make_market, make_snapshot

D = Decimal
CONFIG = ArbitrageConfig(
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


def _group(yes_asks: list[str], no_asks: list[str], *, complete: bool = True):  # type: ignore[no-untyped-def]
    ids = [f"m{i}" for i in range(len(yes_asks))]
    listed = ids if complete else [*ids, "missing"]
    markets = [
        make_market(
            mid,
            Venue.POLYMARKET,
            fee=NO_FEE,
            event_title="Race",
            extra={"neg_risk": True, "neg_risk_market_id": "0xneg", "event_market_ids": listed},
        )
        for mid in ids
    ]
    snaps = {
        mid: make_snapshot(mid, Venue.POLYMARKET, yes_asks=levels((y, "100")), no_asks=levels((n, "100")))
        for mid, y, n in zip(ids, yes_asks, no_asks, strict=True)
    }
    groups = build_groups(markets)
    assert len(groups) == 1
    return groups[0], snaps


def test_long_rebalancing_when_yes_prices_sum_below_one() -> None:
    group, snaps = _group(["0.30", "0.30", "0.30"], ["0.72", "0.72", "0.72"])
    assert group.exhaustive and group.mutually_exclusive
    long, short = evaluate_group(group, snaps, now=NOW, config=CONFIG, data_source=DataSource.FIXTURE)
    assert long.strategy_type is StrategyType.MARKET_REBALANCING_LONG
    assert long.status is OpportunityStatus.CANDIDATE  # analytics, never VALIDATED
    assert long.max_executable_quantity == D(100)
    assert long.expected_net_profit == D(10)  # 100 * (1 - 0.90)
    assert "paper-derived" in long.label
    assert short.strategy_type is StrategyType.MARKET_REBALANCING_SHORT
    assert short.status is OpportunityStatus.NOT_PROFITABLE  # 2.16 > payout 2


def test_short_rebalancing_pays_n_minus_one() -> None:
    group, snaps = _group(["0.40", "0.40", "0.40"], ["0.60", "0.60", "0.60"])
    _, short = evaluate_group(group, snaps, now=NOW, config=CONFIG, data_source=DataSource.FIXTURE)
    assert short.guaranteed_payout == D(200)  # 100 bundles x (3 - 1)
    assert short.expected_net_profit == D(20)  # 200 - 180


def test_long_requires_verified_exhaustiveness() -> None:
    group, snaps = _group(["0.30", "0.30", "0.30"], ["0.72", "0.72", "0.72"], complete=False)
    assert not group.exhaustive
    long, _ = evaluate_group(group, snaps, now=NOW, config=CONFIG, data_source=DataSource.FIXTURE)
    assert long.status is OpportunityStatus.CANDIDATE
    assert "exhaustiveness not verified" in (long.rejection_reason or "")


# ---- combinatorial arbitrage (paper Definitions 2 and 4) --------------------------------------

WINNER_TO_PARTY = [((1, 0, 0), (1, 0, 0)), ((0, 1, 0), (0, 1, 0)), ((0, 0, 1), (0, 0, 1))]


def test_dependency_and_dependent_subsets() -> None:
    assert is_dependent(3, 3, WINNER_TO_PARTY)
    subsets = dependent_subsets(3, 3, WINNER_TO_PARTY)
    pairs = {(tuple(sorted(s.s)), tuple(sorted(s.s_prime))) for s in subsets}
    assert ((0,), (0,)) in pairs and ((0, 1), (0, 1)) in pairs
    assert all(len(s.s) < 3 or len(s.s_prime) < 3 for s in subsets)


def test_independent_markets_have_no_dependent_subsets() -> None:
    full = [(v1, v2) for v1 in ((1, 0), (0, 1)) for v2 in ((1, 0), (0, 1))]
    assert not is_dependent(2, 2, full)
    assert dependent_subsets(2, 2, full) == []


def test_definition_4_construction_has_verified_payout() -> None:
    subsets = next(s for s in dependent_subsets(3, 3, WINNER_TO_PARTY) if s.s == {0} and s.s_prime == {0})
    result = evaluate(
        subsets, [D("0.50"), D("0.40"), D("0.10")], [D("0.45"), D("0.45"), D("0.10")], WINNER_TO_PARTY
    )
    assert result is not None
    assert result.buy_m1 == frozenset({1, 2}) and result.buy_m2 == frozenset({0})
    assert result.cost == D("0.95")
    assert result.min_payout == 1
    assert result.profit == D("0.05")
    assert min_payout(WINNER_TO_PARTY, frozenset(), frozenset()) == 0


def test_invalid_vectors_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        validate_vectors(2, 2, [((1, 1), (1, 0))])
