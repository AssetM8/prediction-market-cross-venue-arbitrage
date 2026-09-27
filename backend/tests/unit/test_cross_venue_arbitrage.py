"""Cross-venue formula, depth-walking optimizer, fees/buffers, gating and complementary mapping."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.arbitrage.cross_venue import ArbitrageConfig, CrossVenueCalculator
from app.arbitrage.depth import BundleEconomics, Ladder, plan_bundle
from app.domain.enums import DataSource, OpportunityStatus, OutcomeSide, Relation, StrategyType, Venue
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import FeeMetadata, NormalizedMarket
from tests.conftest import KALSHI_FEE, NO_FEE, NOW, POLY_FEE, levels, make_market, make_pair, make_snapshot

D = Decimal


def config(**overrides: object) -> ArbitrageConfig:
    values: dict[str, object] = {
        "stale_after_seconds": 30,
        "min_top_of_book_depth": D(1),
        "min_net_profit": D(0),
        "min_return_on_capital": D(0),
        "settlement_buffer": D(0),
        "latency_buffer": D(0),
        "capital_cost_annual_rate": D(0),
        "quantity_step": D(1),
        "max_trade_quantity": D(10000),
    }
    values.update(overrides)
    return ArbitrageConfig(**values)  # type: ignore[arg-type]


def zero_econ(buffer: str = "0") -> BundleEconomics:
    return BundleEconomics(
        payout_per_unit=D(1),
        settlement_buffer_per_unit=D(buffer),
        latency_buffer_per_unit=D(0),
        capital_cost_rate=D(0),
    )


def test_depth_walk_bounded_by_both_books() -> None:
    k = Ladder("k", levels(("0.40", "100"), ("0.45", "100")), NO_FEE)
    p = Ladder("p", levels(("0.50", "50"), ("0.52", "200")), NO_FEE)
    plan = plan_bundle([k, p], zero_econ(), quantity_step=D(1), max_quantity=D(10000))
    assert plan.quantity == D(200)  # Kalshi depth (200) < Polymarket depth (250)
    assert plan.economics is not None
    assert plan.economics.net_profit == D(12)  # 50*0.10 + 50*0.08 + 100*0.03
    assert plan.stop_reason.startswith("book depth exhausted")
    k_leg, p_leg = plan.economics.legs
    assert k_leg.vwap == D("0.425") and p_leg.vwap == D("0.515")


def test_depth_walk_stops_at_first_unprofitable_marginal_unit() -> None:
    k = Ladder("k", levels(("0.40", "100"), ("0.45", "100")), NO_FEE)
    p = Ladder("p", levels(("0.50", "50"), ("0.52", "200")), NO_FEE)
    plan = plan_bundle([k, p], zero_econ(buffer="0.04"), quantity_step=D(1), max_quantity=D(10000))
    assert plan.quantity == D(100)
    assert plan.economics is not None
    assert plan.economics.buffers == D(4)
    assert plan.economics.net_profit == D(5)  # 100 - 91 - 4
    assert "marginal net per unit" in plan.stop_reason
    marginals = [point.marginal_net_per_unit for point in plan.curve]
    assert marginals[:2] == [D("0.06"), D("0.04")] and marginals[2] < 0  # curve extends past the stop


def test_quantity_step_and_max_quantity() -> None:
    k = Ladder("k", levels(("0.40", "10.5")), NO_FEE)
    p = Ladder("p", levels(("0.50", "100")), NO_FEE)
    assert plan_bundle([k, p], zero_econ(), quantity_step=D(1), max_quantity=D(100)).quantity == D(10)
    assert plan_bundle([k, p], zero_econ(), quantity_step=D(1), max_quantity=D(3)).quantity == D(3)


def _markets(
    fee_k: FeeMetadata = KALSHI_FEE, fee_p: FeeMetadata = POLY_FEE
) -> tuple[NormalizedMarket, NormalizedMarket]:
    return (
        make_market("K", Venue.KALSHI, fee=fee_k, resolution_time=NOW + timedelta(days=73)),
        make_market("P", Venue.POLYMARKET, fee=fee_p, resolution_time=NOW + timedelta(days=73)),
    )


def _books(
    k_yes: str = "0.40", p_no: str = "0.50", *, p_age: int = 0
) -> tuple[BinaryBookSnapshot, BinaryBookSnapshot]:
    k = make_snapshot(
        "K",
        Venue.KALSHI,
        yes_asks=levels((k_yes, "100")),
        no_asks=levels(("0.70", "100")),
        yes_bids=levels(("0.30", "100")),
    )
    p = make_snapshot(
        "P",
        Venue.POLYMARKET,
        yes_asks=levels(("0.62", "100")),
        no_asks=levels((p_no, "100")),
        no_bids=levels(("0.45", "100")),
        observed_at=NOW - timedelta(seconds=p_age),
    )
    return k, p


def test_equivalent_pair_formula_without_costs() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, p_books = _books()
    opps = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )
    direction_a = opps[0]
    assert direction_a.direction.startswith("A:")
    assert direction_a.status is OpportunityStatus.VALIDATED
    assert direction_a.max_executable_quantity == D(100)
    assert direction_a.gross_cost == D("90.00")
    assert direction_a.guaranteed_payout == D(100)
    assert direction_a.expected_net_profit == D(10)
    assert [s.payout_per_unit for s in direction_a.payoff_states] == [D(1), D(1)]
    direction_b = opps[1]  # NO@Kalshi 0.70 + YES@Polymarket 0.62 = 1.32
    assert direction_b.status is OpportunityStatus.NOT_PROFITABLE
    assert direction_b.max_executable_quantity == 0


def test_fees_and_buffers_reduce_net_profit() -> None:
    k_market, p_market = _markets()
    k_books, p_books = _books()
    cfg = config(settlement_buffer=D("0.005"), latency_buffer=D("0.0025"))
    opp = CrossVenueCalculator(cfg).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert opp.explicit_fees == D("1.68") + D("1.25")  # 0.07*100*0.4*0.6 + 0.05*100*0.5*0.5
    assert opp.safety_buffer == D("0.75")
    assert opp.expected_net_profit == D("10") - D("2.93") - D("0.75")
    assert any("kalshi fee" in note for note in opp.assumptions)
    assert opp.return_on_capital is not None and opp.return_on_capital > 0


def test_capital_cost_is_charged_for_lock_up() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, p_books = _books()
    opp = CrossVenueCalculator(config(capital_cost_annual_rate=D("0.05"))).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    # 90 deployed * 5% * 73/365 = 0.9
    assert opp.safety_buffer == D("0.900000")
    assert opp.expected_net_profit == D("9.1")


def test_complementary_pair_uses_same_side_legs() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k = make_snapshot("K", Venue.KALSHI, yes_asks=levels(("0.45", "100")), no_asks=levels(("0.58", "100")))
    p = make_snapshot(
        "P", Venue.POLYMARKET, yes_asks=levels(("0.48", "100")), no_asks=levels(("0.53", "100"))
    )
    opps = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.COMPLEMENTARY), k_market, p_market, k, p, now=NOW, data_source=DataSource.FIXTURE
    )
    c = opps[0]
    assert [leg.outcome_side for leg in c.legs] == [OutcomeSide.YES, OutcomeSide.YES]
    assert c.expected_net_profit == D(7)
    assert opps[1].status is OpportunityStatus.NOT_PROFITABLE  # NO+NO = 1.11


def test_stale_book_suppresses() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, p_books = _books(p_age=45)
    opp = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert opp.status is OpportunityStatus.SUPPRESSED
    assert opp.rejection_reason is not None and "stale_book" in opp.rejection_reason
    assert opp.stale_after == NOW - timedelta(seconds=45) + timedelta(seconds=30)


def test_malformed_crossed_and_thin_books_suppress() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    _, p_books = _books()
    bad = make_snapshot(
        "K",
        Venue.KALSHI,
        yes_asks=levels(("0.40", "100")),
        no_asks=levels(("0.70", "100")),
        issues=("crossed_book: test",),
    )
    opp = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        bad,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert opp.status is OpportunityStatus.SUPPRESSED and "crossed_book" in (opp.rejection_reason or "")
    thin_k, _ = _books()
    opp = CrossVenueCalculator(config(min_top_of_book_depth=D(500))).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        thin_k,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert "insufficient_depth" in (opp.rejection_reason or "")


def test_inactive_venue_or_market_suppresses() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, p_books = _books()
    opp = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
        venue_trading={Venue.KALSHI: False},
    )[0]
    assert "venue_trading_inactive" in (opp.rejection_reason or "")


def test_thresholds_keep_it_a_candidate() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, p_books = _books()
    opp = CrossVenueCalculator(config(min_net_profit=D(50))).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p_books,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert opp.status is OpportunityStatus.CANDIDATE
    assert opp.label.startswith("candidate arbitrage")
    assert "net profit" in (opp.rejection_reason or "")


def test_min_order_size_suppresses() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k_books, _ = _books()
    p = make_snapshot(
        "P", Venue.POLYMARKET, yes_asks=levels(("0.62", "100")), no_asks=levels(("0.50", "100"))
    )
    p = p.model_copy(update={"no": p.no.model_copy(update={"min_order_size": D(500)})})
    opp = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.EQUIVALENT),
        k_market,
        p_market,
        k_books,
        p,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )[0]
    assert opp.status is OpportunityStatus.SUPPRESSED and "below_min_order_size" in (
        opp.rejection_reason or ""
    )


def test_implication_pairs_are_analytics_only() -> None:
    k_market, p_market = _markets(NO_FEE, NO_FEE)
    k = make_snapshot("K", Venue.KALSHI, yes_asks=levels(("0.50", "100")), no_asks=levels(("0.52", "100")))
    p = make_snapshot(
        "P", Venue.POLYMARKET, yes_asks=levels(("0.40", "100")), no_asks=levels(("0.62", "100"))
    )
    opps = CrossVenueCalculator(config()).evaluate_pair(
        make_pair(Relation.A_IMPLIES_B, approved=False),
        k_market,
        p_market,
        k,
        p,
        now=NOW,
        data_source=DataSource.FIXTURE,
    )
    assert len(opps) == 1
    assert opps[0].strategy_type is StrategyType.COMBINATORIAL
    assert opps[0].status is OpportunityStatus.CANDIDATE  # profitable, but never VALIDATED
    assert [leg.outcome_side for leg in opps[0].legs] == [OutcomeSide.NO, OutcomeSide.YES]
