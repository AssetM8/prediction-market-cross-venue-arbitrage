"""Market-rebalancing arbitrage (paper Definition 3), depth-aware, intra-venue analytics.

For mutually exclusive and collectively exhaustive (MECE) conditions C_1..C_n the paper
defines, on prices ``val(Y_i, t)``:

* long rebalancing when ``sum_i val(Y_i, t) < 1`` - buying every YES pays exactly 1;
* short rebalancing when ``sum_i val(Y_i, t) > 1`` - selling every YES (equivalently,
  buying every NO) pays ``n - 1``.

Here displayed prices are replaced by depth-walked asks, fees and buffers, using the same
optimizer as the cross-venue engine. Long rebalancing requires *exhaustiveness* to be
verified (Polymarket negative-risk groups whose full membership is present); short
rebalancing needs only mutual exclusivity (buying every NO pays at least ``n - 1``).

These results are **paper-derived intra-market analytics**. They are never marked
``VALIDATED`` and the paper execution engine does not execute them in this MVP.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.arbitrage.cross_venue import ArbitrageConfig, capital_cost_rate
from app.arbitrage.depth import BundleEconomics, Ladder, plan_bundle
from app.arbitrage.gating import GateConfig, gate_leg
from app.core.decimal_utils import ONE, ZERO, round_ratio
from app.core.ids import stable_id
from app.domain.enums import DataSource, MarketStatus, OpportunityStatus, OutcomeSide, StrategyType, Venue
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import ArbitrageLeg, ArbitrageOpportunity, NormalizedMarket

PAPER_DETECTION_THRESHOLD = Decimal("0.02")  # |1 - sum of prices| used by the paper to flag cases


@dataclass(frozen=True)
class RebalancingGroup:
    venue: Venue
    group_id: str
    title: str
    markets: tuple[NormalizedMarket, ...]
    mutually_exclusive: bool
    exhaustive: bool
    evidence: tuple[str, ...]


def build_groups(markets: list[NormalizedMarket]) -> list[RebalancingGroup]:
    """Find multi-condition groups whose structure the venue metadata vouches for."""
    kalshi: dict[str, list[NormalizedMarket]] = defaultdict(list)
    poly: dict[str, list[NormalizedMarket]] = defaultdict(list)
    for market in markets:
        if market.status is not MarketStatus.ACTIVE or market.market_type != "binary":
            continue
        if market.venue is Venue.KALSHI and market.extra.get("mutually_exclusive"):
            kalshi[market.venue_event_id].append(market)
        if (
            market.venue is Venue.POLYMARKET
            and market.extra.get("neg_risk")
            and market.extra.get("neg_risk_market_id")
        ):
            poly[str(market.extra["neg_risk_market_id"])].append(market)
    groups: list[RebalancingGroup] = []
    for event_id, members in sorted(kalshi.items()):
        if len(members) < 2:
            continue
        groups.append(
            RebalancingGroup(
                venue=Venue.KALSHI,
                group_id=event_id,
                title=members[0].event_title or event_id,
                markets=tuple(sorted(members, key=lambda m: m.venue_market_id)),
                mutually_exclusive=True,
                exhaustive=False,
                evidence=(
                    "Kalshi event flag mutually_exclusive=true",
                    "exhaustiveness is not asserted by Kalshi metadata; long rebalancing is not riskless",
                ),
            )
        )
    for group_id, members in sorted(poly.items()):
        if len(members) < 2:
            continue
        listed = set(members[0].extra.get("event_market_ids") or [])
        present = {m.venue_market_id for m in members}
        complete = bool(listed) and listed == present
        groups.append(
            RebalancingGroup(
                venue=Venue.POLYMARKET,
                group_id=group_id,
                title=members[0].event_title or group_id,
                markets=tuple(sorted(members, key=lambda m: m.venue_market_id)),
                mutually_exclusive=True,
                exhaustive=complete,
                evidence=(
                    "Polymarket negative-risk group: exactly one market resolves YES",
                    "all group markets present and active"
                    if complete
                    else "group membership incomplete in fetched data; exhaustiveness not verified",
                ),
            )
        )
    return groups


def evaluate_group(
    group: RebalancingGroup,
    snapshots: dict[str, BinaryBookSnapshot],
    *,
    now: datetime,
    config: ArbitrageConfig,
    data_source: DataSource,
) -> list[ArbitrageOpportunity]:
    members = [m for m in group.markets if m.venue_market_id in snapshots]
    if len(members) != len(group.markets):
        return []
    n = Decimal(len(members))
    results = []
    for strategy, side, payout in (
        (StrategyType.MARKET_REBALANCING_LONG, OutcomeSide.YES, ONE),
        (StrategyType.MARKET_REBALANCING_SHORT, OutcomeSide.NO, n - ONE),
    ):
        results.append(_evaluate(group, members, snapshots, strategy, side, payout, now, config, data_source))
    return results


def _evaluate(
    group: RebalancingGroup,
    members: list[NormalizedMarket],
    snapshots: dict[str, BinaryBookSnapshot],
    strategy: StrategyType,
    side: OutcomeSide,
    payout: Decimal,
    now: datetime,
    config: ArbitrageConfig,
    data_source: DataSource,
) -> ArbitrageOpportunity:
    gate = GateConfig(
        stale_after_seconds=config.stale_after_seconds, min_top_of_book_depth=config.min_top_of_book_depth
    )
    books = [snapshots[m.venue_market_id].side(side) for m in members]
    suppress: list[str] = []
    for market, book in zip(members, books, strict=True):
        suppress += gate_leg(book, snapshots[market.venue_market_id], market, now=now, config=gate)
    rate, capital_note = capital_cost_rate(
        config.capital_cost_annual_rate, now, *[m.resolution_time for m in members]
    )
    ladders = [
        Ladder(
            label=f"{m.venue_market_id}:{side.value}",
            asks=b.asks,
            fee=m.fee_metadata,
            min_order_size=b.min_order_size,
        )
        for m, b in zip(members, books, strict=True)
    ]
    plan = plan_bundle(
        ladders,
        BundleEconomics(
            payout_per_unit=payout,
            settlement_buffer_per_unit=config.settlement_buffer,
            latency_buffer_per_unit=config.latency_buffer * len(members),
            capital_cost_rate=rate,
        ),
        quantity_step=config.quantity_step,
        max_quantity=config.max_trade_quantity,
    )
    yes_asks = [snapshots[m.venue_market_id].yes.best_ask for m in members]
    yes_bids = [snapshots[m.venue_market_id].yes.best_bid for m in members]
    sum_asks = sum((a.price for a in yes_asks if a), ZERO) if all(yes_asks) else None
    sum_bids = sum((b.price for b in yes_bids if b), ZERO) if all(yes_bids) else None

    reason: str | None
    if suppress:
        status, reason = OpportunityStatus.SUPPRESSED, "; ".join(suppress)
    elif strategy is StrategyType.MARKET_REBALANCING_LONG and not group.exhaustive:
        status = OpportunityStatus.CANDIDATE if plan.quantity > ZERO else OpportunityStatus.NOT_PROFITABLE
        reason = "exhaustiveness not verified: buying every YES is not guaranteed to pay 1"
        if plan.quantity <= ZERO:
            reason = f"no profitable depth; also {reason}"
    elif plan.quantity <= ZERO or plan.economics is None:
        status = OpportunityStatus.NOT_PROFITABLE
        top = plan.top_net_per_unit
        reason = (
            f"no profitable depth (top-of-book net per bundle {top:.6f})"
            if top is not None
            else plan.stop_reason
        )
    else:
        status = OpportunityStatus.CANDIDATE
        reason = "intra-market analytics only; not executed by the MVP paper engine"

    economics = plan.economics
    legs = []
    for position, (market, book) in enumerate(zip(members, books, strict=True)):
        leg_plan = economics.legs[position] if economics else None
        best = book.asks[0].price if book.asks else ZERO
        legs.append(
            ArbitrageLeg(
                venue=group.venue,
                market_id=market.venue_market_id,
                outcome_id=book.outcome_id,
                outcome_side=side,
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
    observed = min(b.observed_at for b in books)
    paper_note = "paper price sum unavailable (a leg has no quote)"
    if sum_asks is not None and sum_bids is not None:
        paper_note = (
            f"paper-style check: sum of best YES asks {sum_asks}, sum of best YES bids {sum_bids}; "
            f"paper flags |1 - sum| > {PAPER_DETECTION_THRESHOLD}"
        )
    roc = plan.return_on_capital
    return ArbitrageOpportunity(
        id=stable_id(
            "rebal",
            group.venue.value,
            group.group_id,
            strategy.value,
            now.isoformat(),
            *[b.checksum_or_source_hash for b in books],
        ),
        pair_id=None,
        strategy_type=strategy,
        direction=(
            f"buy YES on all {len(members)} conditions (payout 1)"
            if side is OutcomeSide.YES
            else f"buy NO on all {len(members)} conditions (payout {payout})"
        ),
        relation=None,
        status=status,
        detected_at=now,
        book_timestamps={
            f"{group.venue.value}:{m.venue_market_id}:{side.value}": b.observed_at
            for m, b in zip(members, books, strict=True)
        },
        legs=tuple(legs),
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
        stale_after=observed + timedelta(seconds=config.stale_after_seconds),
        assumptions=(
            f"group '{group.title}' ({group.venue.value} {group.group_id}), {len(members)} conditions",
            *group.evidence,
            paper_note,
            capital_note,
            "latency buffer applied per leg; settlement buffer per bundle",
            "paper Definition 3 with depth-walked asks instead of displayed prices",
        ),
        rejection_reason=reason,
        profit_curve=tuple(plan.curve),
        payoff_states=(),
        data_source=data_source,
        label="paper-derived intra-market analytics (not a cross-venue execution candidate)",
    )
