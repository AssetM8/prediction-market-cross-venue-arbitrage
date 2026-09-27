"""Depth- and fee-aware cross-venue arbitrage for approved binary pairs.

For quantity ``q`` of a two-leg bundle::

    total_cost(q)  = executable_cost_leg_1(q) + executable_cost_leg_2(q)
                     + explicit_fees(q) + buffers(q)
    net_profit(q)  = guaranteed_payout(q) - total_cost(q)

``guaranteed_payout`` is the minimum payout over the joint states the pair's relation
allows (:mod:`app.arbitrage.payoff`), i.e. 1 per unit for the approved mappings:

* EQUIVALENT: A = YES@Kalshi + NO@Polymarket, B = NO@Kalshi + YES@Polymarket
* COMPLEMENTARY: C = YES@Kalshi + YES@Polymarket, D = NO@Kalshi + NO@Polymarket

Implication pairs can be priced as *combinatorial analytics* (paper Definition 4 adapted
across venues) but are capped at ``CANDIDATE``: their relation is inferred from wording,
not established as equivalence, so they are never executable in this MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.arbitrage.depth import BundleEconomics, BundlePlan, Ladder, plan_bundle
from app.arbitrage.fees import describe as describe_fee
from app.arbitrage.gating import GateConfig, gate_leg
from app.arbitrage.payoff import guaranteed_payout_per_unit, payoff_states, verified_constructions
from app.core.decimal_utils import ONE, ZERO, round_ratio
from app.core.ids import stable_id
from app.core.metrics import METRICS
from app.domain.enums import (
    EXECUTABLE_RELATIONS,
    DataSource,
    OpportunityStatus,
    StrategyType,
    Venue,
)
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import (
    ArbitrageLeg,
    ArbitrageOpportunity,
    LegMapping,
    MarketPair,
    NormalizedMarket,
    NormalizedOrderBook,
)

DAYS_PER_YEAR = Decimal(365)
SECONDS_PER_DAY = Decimal(86400)
DEFAULT_HORIZON_DAYS = Decimal(365)


@dataclass(frozen=True)
class ArbitrageConfig:
    stale_after_seconds: int
    min_top_of_book_depth: Decimal
    min_net_profit: Decimal
    min_return_on_capital: Decimal
    settlement_buffer: Decimal
    latency_buffer: Decimal
    capital_cost_annual_rate: Decimal
    quantity_step: Decimal
    max_trade_quantity: Decimal


def capital_cost_rate(
    annual_rate: Decimal, now: datetime, *resolution_times: datetime | None
) -> tuple[Decimal, str]:
    """Fraction of deployed capital charged for lock-up until the later resolution time."""
    known = [t for t in resolution_times if t is not None]
    if not known:
        return annual_rate * DEFAULT_HORIZON_DAYS / DAYS_PER_YEAR, (
            f"resolution time unknown; capital cost assumes {DEFAULT_HORIZON_DAYS} days"
        )
    horizon: timedelta = max(known) - now
    days = max(Decimal(0), Decimal(int(horizon.total_seconds())) / SECONDS_PER_DAY)
    return annual_rate * days / DAYS_PER_YEAR, (
        f"capital cost {annual_rate} p.a. for {days.quantize(Decimal('0.01'))} days to the later resolution"
    )


class CrossVenueCalculator:
    def __init__(self, config: ArbitrageConfig) -> None:
        self._config = config

    def evaluate_pair(
        self,
        pair: MarketPair,
        kalshi: NormalizedMarket,
        polymarket: NormalizedMarket,
        kalshi_books: BinaryBookSnapshot,
        polymarket_books: BinaryBookSnapshot,
        *,
        now: datetime,
        data_source: DataSource,
        venue_trading: dict[Venue, bool] | None = None,
    ) -> list[ArbitrageOpportunity]:
        """Price every verified two-leg construction for ``pair``."""
        if pair.approved_for_arbitrage_calculation and pair.relation in EXECUTABLE_RELATIONS:
            strategy = StrategyType.CROSS_VENUE_BINARY
            mappings = list(pair.leg_mappings)
        else:
            strategy = StrategyType.COMBINATORIAL
            mappings = [
                LegMapping(
                    kalshi_side=k_side,
                    polymarket_side=p_side,
                    label=f"{k_side.value.upper()}@Kalshi + {p_side.value.upper()}@Polymarket "
                    f"(verified min payout {payout} under {pair.relation.value})",
                )
                for k_side, p_side, payout in verified_constructions(pair.relation)
            ]
        trading = venue_trading or {}
        return [
            self._evaluate_mapping(
                pair,
                kalshi,
                polymarket,
                kalshi_books,
                polymarket_books,
                mapping,
                strategy=strategy,
                now=now,
                data_source=data_source,
                trading=trading,
            )
            for mapping in mappings
        ]

    def _evaluate_mapping(
        self,
        pair: MarketPair,
        kalshi: NormalizedMarket,
        polymarket: NormalizedMarket,
        kalshi_books: BinaryBookSnapshot,
        polymarket_books: BinaryBookSnapshot,
        mapping: LegMapping,
        *,
        strategy: StrategyType,
        now: datetime,
        data_source: DataSource,
        trading: dict[Venue, bool],
    ) -> ArbitrageOpportunity:
        cfg = self._config
        k_book = kalshi_books.side(mapping.kalshi_side)
        p_book = polymarket_books.side(mapping.polymarket_side)
        payout_per_unit = guaranteed_payout_per_unit(
            pair.relation, mapping.kalshi_side, mapping.polymarket_side
        )
        gate = GateConfig(
            stale_after_seconds=cfg.stale_after_seconds, min_top_of_book_depth=cfg.min_top_of_book_depth
        )
        suppress = gate_leg(
            k_book, kalshi_books, kalshi, now=now, config=gate, venue_trading=trading.get(Venue.KALSHI, True)
        ) + gate_leg(
            p_book,
            polymarket_books,
            polymarket,
            now=now,
            config=gate,
            venue_trading=trading.get(Venue.POLYMARKET, True),
        )
        capital_rate, capital_note = capital_cost_rate(
            cfg.capital_cost_annual_rate, now, kalshi.resolution_time, polymarket.resolution_time
        )
        econ = BundleEconomics(
            payout_per_unit=payout_per_unit,
            settlement_buffer_per_unit=cfg.settlement_buffer,
            latency_buffer_per_unit=cfg.latency_buffer,
            capital_cost_rate=capital_rate,
        )
        ladders = [
            Ladder(
                label=f"kalshi:{mapping.kalshi_side.value}",
                asks=k_book.asks,
                fee=kalshi.fee_metadata,
                min_order_size=k_book.min_order_size or kalshi.min_order_size,
            ),
            Ladder(
                label=f"polymarket:{mapping.polymarket_side.value}",
                asks=p_book.asks,
                fee=polymarket.fee_metadata,
                min_order_size=p_book.min_order_size or polymarket.min_order_size,
            ),
        ]
        plan = plan_bundle(
            ladders, econ, quantity_step=cfg.quantity_step, max_quantity=cfg.max_trade_quantity
        )
        status, reason = self._status(plan, suppress, payout_per_unit, strategy)
        METRICS.inc("opportunities_total", status=status.value, strategy=strategy.value)

        assumptions = self._assumptions(
            pair, kalshi, polymarket, k_book, p_book, mapping, payout_per_unit, capital_note, plan
        )
        legs = self._legs(plan, [(Venue.KALSHI, kalshi, k_book), (Venue.POLYMARKET, polymarket, p_book)])
        economics = plan.economics
        observed = min(k_book.observed_at, p_book.observed_at)
        detected_label = now.isoformat()
        opp_id = stable_id(
            "opp",
            pair.id,
            mapping.label,
            k_book.checksum_or_source_hash,
            p_book.checksum_or_source_hash,
            detected_label,
        )
        roc = plan.return_on_capital
        return ArbitrageOpportunity(
            id=opp_id,
            pair_id=pair.id,
            strategy_type=strategy,
            direction=mapping.label,
            relation=pair.relation,
            status=status,
            detected_at=now,
            book_timestamps={
                f"kalshi:{mapping.kalshi_side.value}": k_book.observed_at,
                f"polymarket:{mapping.polymarket_side.value}": p_book.observed_at,
            },
            legs=legs,
            max_executable_quantity=plan.quantity,
            top_of_book_cost_per_unit=plan.top_cost_per_unit,
            top_of_book_net_per_unit=plan.top_net_per_unit,
            gross_cost=economics.gross_cost if economics else ZERO,
            guaranteed_payout=economics.payout if economics else ZERO,
            explicit_fees=economics.explicit_fees if economics else ZERO,
            slippage=plan.slippage,
            safety_buffer=economics.buffers if economics else ZERO,
            expected_net_profit=economics.net_profit if economics else ZERO,
            return_on_capital=round_ratio(roc) if roc is not None else None,
            stale_after=observed + timedelta(seconds=cfg.stale_after_seconds),
            assumptions=tuple(assumptions),
            rejection_reason=reason,
            profit_curve=tuple(plan.curve),
            payoff_states=payoff_states(pair.relation, mapping.kalshi_side, mapping.polymarket_side),
            data_source=data_source,
            label=_label(status, strategy),
        )

    def _status(
        self,
        plan: BundlePlan,
        suppress: list[str],
        payout_per_unit: Decimal,
        strategy: StrategyType,
    ) -> tuple[OpportunityStatus, str | None]:
        cfg = self._config
        if payout_per_unit < ONE:
            return OpportunityStatus.SUPPRESSED, "no verified guaranteed payout for this construction"
        if suppress:
            return OpportunityStatus.SUPPRESSED, "; ".join(suppress)
        if plan.economics is None or plan.quantity <= ZERO:
            top = plan.top_net_per_unit
            detail = f"top-of-book net per unit {top:.6f}" if top is not None else plan.stop_reason
            return OpportunityStatus.NOT_PROFITABLE, f"no profitable executable quantity ({detail})"
        if plan.min_order_violation:
            return OpportunityStatus.SUPPRESSED, f"below_min_order_size: {plan.min_order_violation}"
        roc = plan.return_on_capital or ZERO
        shortfalls = []
        if plan.economics.net_profit < cfg.min_net_profit:
            shortfalls.append(f"net profit {plan.economics.net_profit} < minimum {cfg.min_net_profit}")
        if roc < cfg.min_return_on_capital:
            shortfalls.append(f"return on capital {round_ratio(roc)} < minimum {cfg.min_return_on_capital}")
        if strategy is StrategyType.COMBINATORIAL:
            shortfalls.append("relation is an implication, not equivalence: analytics only in the MVP")
        if shortfalls:
            return OpportunityStatus.CANDIDATE, "; ".join(shortfalls)
        return OpportunityStatus.VALIDATED, None

    @staticmethod
    def _legs(
        plan: BundlePlan,
        books: list[tuple[Venue, NormalizedMarket, NormalizedOrderBook]],
    ) -> tuple[ArbitrageLeg, ...]:
        legs: list[ArbitrageLeg] = []
        for position, (venue, market, book) in enumerate(books):
            best = book.asks[0].price if book.asks else ZERO
            leg_plan = plan.economics.legs[position] if plan.economics else None
            legs.append(
                ArbitrageLeg(
                    venue=venue,
                    market_id=market.venue_market_id,
                    outcome_id=book.outcome_id,
                    outcome_side=book.outcome_side,
                    quantity=leg_plan.quantity if leg_plan else ZERO,
                    best_price=best,
                    worst_price=leg_plan.worst_price if leg_plan else best,
                    vwap=leg_plan.vwap if leg_plan else best,
                    cost=leg_plan.cost if leg_plan else ZERO,
                    fee=leg_plan.fee if leg_plan else ZERO,
                    fills=leg_plan.fills if leg_plan else (),
                    book_observed_at=book.observed_at,
                    book_hash=book.checksum_or_source_hash,
                    derived_asks=book.derived_asks,
                )
            )
        return tuple(legs)

    def _assumptions(
        self,
        pair: MarketPair,
        kalshi: NormalizedMarket,
        polymarket: NormalizedMarket,
        k_book: NormalizedOrderBook,
        p_book: NormalizedOrderBook,
        mapping: LegMapping,
        payout_per_unit: Decimal,
        capital_note: str,
        plan: BundlePlan,
    ) -> list[str]:
        cfg = self._config
        notes = [
            "binary contracts pay exactly 1 unit of collateral per winning contract "
            "(USD on Kalshi, pUSD on Polymarket); collateral treated as 1:1",
            f"pair relation {pair.relation.value} (confidence {pair.confidence}); construction "
            f"'{mapping.label}' has verified minimum payout {payout_per_unit} per unit across "
            "all joint states allowed by the relation",
            f"kalshi fee: {describe_fee(kalshi.fee_metadata)}",
            f"polymarket fee: {describe_fee(polymarket.fee_metadata)}",
            f"settlement/transfer buffer {cfg.settlement_buffer} and latency (execution-risk) "
            f"buffer {cfg.latency_buffer} per unit",
            capital_note,
            f"books older than {cfg.stale_after_seconds}s are stale; opportunity expires at the "
            "oldest book timestamp plus that threshold",
            f"quantity rounded down to multiples of {cfg.quantity_step}; capped at {cfg.max_trade_quantity}",
            f"thresholds: net profit >= {cfg.min_net_profit}, return on capital >= {cfg.min_return_on_capital}",
            f"optimizer stop: {plan.stop_reason}",
            "cross-venue execution is non-atomic: leg risk, partial fills, latency and venue "
            "outages are simulated separately by the paper execution engine",
            "venue settlement rules can still differ in ways text checks miss (basis risk)",
        ]
        if k_book.derived_asks:
            notes.append(
                f"kalshi {k_book.outcome_side.value.upper()} asks derived from the opposite side's bids "
                "(ask = 1 - bid), per Kalshi's documented order-book semantics"
            )
        for book in (k_book, p_book):
            if book.depth_limited:
                notes.append(
                    f"{book.venue.value} book is top-of-book only (depth unavailable); size beyond it assumed zero"
                )
        if kalshi.tick_size or polymarket.tick_size:
            notes.append(
                f"tick sizes: kalshi {kalshi.tick_size}, polymarket {p_book.tick_size or polymarket.tick_size}"
            )
        return notes


def _label(status: OpportunityStatus, strategy: StrategyType) -> str:
    if strategy is StrategyType.COMBINATORIAL:
        return "combinatorial analytics (implication; not eligible for MVP execution)"
    if status is OpportunityStatus.VALIDATED:
        return "validated paper opportunity (equivalence and executable depth passed)"
    if status is OpportunityStatus.CANDIDATE:
        return "candidate arbitrage (below execution thresholds)"
    return "not actionable"


__all__ = ["ArbitrageConfig", "CrossVenueCalculator", "capital_cost_rate"]
