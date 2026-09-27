"""Depth-aware bundle optimizer shared by cross-venue and rebalancing strategies.

A *bundle* is one unit of each leg (e.g. one YES on Kalshi plus one NO on Polymarket)
with a verified guaranteed payout. The optimizer walks every leg's ask ladder at once:
at each step the marginal unit costs the sum of the current level prices plus their
marginal taker fees plus per-unit buffers. Because each ladder's prices are non-decreasing
and ``p + r * p * (1 - p)`` is increasing in ``p`` for any fee rate ``r < 1``, the marginal
net profit is non-increasing, so taking every unit with positive marginal net profit
maximises total profit (greedy is optimal). The walk stops at the first unprofitable
marginal unit, when any ladder is exhausted, or at ``max_quantity``. The executable
quantity is therefore bounded by the depth of *every* leg.

The result is then re-priced exactly: each consumed level becomes a fill whose fee uses
the venue's rounding, and buffers are added. If rounding erases the edge at the greedy
quantity, smaller breakpoints are tried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.arbitrage.fees import fill_fee, unit_fee_unrounded
from app.core.decimal_utils import ZERO, floor_to_step, round_cost_up, round_proceeds_down, safe_div
from app.domain.models import BookLevel, FeeMetadata, LevelFill, ProfitPoint

EXTRA_CURVE_SEGMENTS = 3


class InsufficientDepthError(ValueError):
    pass


@dataclass(frozen=True)
class Ladder:
    label: str
    asks: tuple[BookLevel, ...]
    fee: FeeMetadata
    min_order_size: Decimal | None = None

    @property
    def depth(self) -> Decimal:
        return sum((level.quantity for level in self.asks), ZERO)


@dataclass(frozen=True)
class BundleEconomics:
    """Per-unit payout and cost buffers.

    ``per_unit_buffer`` covers settlement/transfer costs and latency (execution-risk)
    allowance per bundle; ``capital_cost_rate`` is the fraction of deployed capital charged
    for locking it up until resolution (annual rate x years to resolution).
    """

    payout_per_unit: Decimal
    settlement_buffer_per_unit: Decimal
    latency_buffer_per_unit: Decimal
    capital_cost_rate: Decimal

    @property
    def per_unit_buffer(self) -> Decimal:
        return self.settlement_buffer_per_unit + self.latency_buffer_per_unit


@dataclass(frozen=True)
class LegPlan:
    label: str
    quantity: Decimal
    fills: tuple[LevelFill, ...]
    cost: Decimal
    fee: Decimal

    @property
    def vwap(self) -> Decimal:
        return self.cost / self.quantity if self.quantity > ZERO else ZERO

    @property
    def best_price(self) -> Decimal:
        return self.fills[0].price if self.fills else ZERO

    @property
    def worst_price(self) -> Decimal:
        return self.fills[-1].price if self.fills else ZERO


@dataclass(frozen=True)
class Economics:
    quantity: Decimal
    legs: tuple[LegPlan, ...]
    gross_cost: Decimal
    explicit_fees: Decimal
    buffers: Decimal
    payout: Decimal
    net_profit: Decimal


@dataclass
class BundlePlan:
    quantity: Decimal
    economics: Economics | None
    top_cost_per_unit: Decimal | None
    top_net_per_unit: Decimal | None
    top_of_book_cost: Decimal
    curve: list[ProfitPoint] = field(default_factory=list)
    stop_reason: str = ""
    greedy_quantity: Decimal = ZERO
    min_order_violation: str | None = None

    @property
    def slippage(self) -> Decimal:
        if self.economics is None:
            return ZERO
        return self.economics.gross_cost - self.top_of_book_cost

    @property
    def return_on_capital(self) -> Decimal | None:
        if self.economics is None:
            return None
        capital = self.economics.gross_cost + self.economics.explicit_fees
        return safe_div(self.economics.net_profit, capital)


def walk_ladder(asks: tuple[BookLevel, ...], quantity: Decimal) -> list[tuple[Decimal, Decimal]]:
    """Consume ``quantity`` from the best asks; returns (price, qty) per level touched."""
    remaining = quantity
    fills: list[tuple[Decimal, Decimal]] = []
    for level in asks:
        if remaining <= ZERO:
            break
        take = min(level.quantity, remaining)
        fills.append((level.price, take))
        remaining -= take
    if remaining > ZERO:
        raise InsufficientDepthError(f"only {quantity - remaining} of {quantity} available")
    return fills


def price_bundle(ladders: list[Ladder], econ: BundleEconomics, quantity: Decimal) -> Economics:
    """Exact economics of buying ``quantity`` bundles (venue fee rounding, buffers rounded up)."""
    legs: list[LegPlan] = []
    for ladder in ladders:
        fills = [
            LevelFill(price=price, quantity=qty, fee=fill_fee(ladder.fee, price, qty))
            for price, qty in walk_ladder(ladder.asks, quantity)
        ]
        cost = sum((f.price * f.quantity for f in fills), ZERO)
        fee = sum((f.fee for f in fills), ZERO)
        legs.append(LegPlan(label=ladder.label, quantity=quantity, fills=tuple(fills), cost=cost, fee=fee))
    gross = sum((leg.cost for leg in legs), ZERO)
    fees = sum((leg.fee for leg in legs), ZERO)
    buffers = round_cost_up(econ.per_unit_buffer * quantity + econ.capital_cost_rate * (gross + fees))
    payout = round_proceeds_down(econ.payout_per_unit * quantity)
    net = payout - round_cost_up(gross) - round_cost_up(fees) - buffers
    return Economics(
        quantity=quantity,
        legs=tuple(legs),
        gross_cost=gross,
        explicit_fees=fees,
        buffers=buffers,
        payout=payout,
        net_profit=net,
    )


def _marginal(ladders: list[Ladder], indices: list[int], econ: BundleEconomics) -> tuple[Decimal, Decimal]:
    prices = [ladder.asks[i].price for ladder, i in zip(ladders, indices, strict=True)]
    unit_cost = sum(prices, ZERO)
    unit_fee = sum(
        (unit_fee_unrounded(ladder.fee, price) for ladder, price in zip(ladders, prices, strict=True)), ZERO
    )
    unit_buffer = econ.per_unit_buffer + econ.capital_cost_rate * (unit_cost + unit_fee)
    return unit_cost, econ.payout_per_unit - unit_cost - unit_fee - unit_buffer


def plan_bundle(
    ladders: list[Ladder],
    econ: BundleEconomics,
    *,
    quantity_step: Decimal,
    max_quantity: Decimal,
) -> BundlePlan:
    """Find the profit-maximising executable quantity across all ``ladders``."""
    if not ladders or any(not ladder.asks for ladder in ladders):
        return BundlePlan(
            quantity=ZERO,
            economics=None,
            top_cost_per_unit=None,
            top_net_per_unit=None,
            top_of_book_cost=ZERO,
            stop_reason="a leg has no asks",
        )
    top_cost, top_net = _marginal(ladders, [0] * len(ladders), econ)
    indices = [0] * len(ladders)
    remaining = [ladder.asks[0].quantity for ladder in ladders]
    cumulative = ZERO
    greedy = ZERO
    breakpoints: list[tuple[Decimal, Decimal]] = []  # (quantity after segment, marginal net)
    stop_reason = "reached maximum trade quantity"
    extra_segments = 0
    stopped = False
    while cumulative < max_quantity:
        _, marginal = _marginal(ladders, indices, econ)
        if marginal <= ZERO and not stopped:
            stopped = True
            stop_reason = f"marginal net per unit {marginal:.6f} <= 0 at depth {cumulative}"
        if stopped:
            extra_segments += 1
            if extra_segments > EXTRA_CURVE_SEGMENTS:
                break
        chunk = min([*remaining, max_quantity - cumulative])
        cumulative += chunk
        breakpoints.append((cumulative, marginal))
        if not stopped:
            greedy = cumulative
        exhausted = False
        for position, ladder in enumerate(ladders):
            remaining[position] -= chunk
            if remaining[position] <= ZERO:
                indices[position] += 1
                if indices[position] >= len(ladder.asks):
                    exhausted = True
                else:
                    remaining[position] = ladder.asks[indices[position]].quantity
        if exhausted:
            if not stopped:
                stop_reason = f"book depth exhausted at {cumulative}"
            break

    candidates = sorted(
        {floor_to_step(q, quantity_step) for q, _ in breakpoints if q <= greedy}
        | {floor_to_step(greedy, quantity_step)},
        reverse=True,
    )
    chosen: Economics | None = None
    for quantity in candidates:
        if quantity <= ZERO:
            continue
        economics = price_bundle(ladders, econ, quantity)
        if economics.net_profit > ZERO:
            chosen = economics
            break

    curve: list[ProfitPoint] = []
    seen: set[Decimal] = set()
    for quantity, marginal in breakpoints:
        q = floor_to_step(quantity, quantity_step)
        if q <= ZERO or q in seen:
            continue
        seen.add(q)
        economics = price_bundle(ladders, econ, q)
        curve.append(
            ProfitPoint(
                quantity=q,
                cumulative_cost=economics.gross_cost,
                cumulative_fees=economics.explicit_fees,
                cumulative_buffers=economics.buffers,
                cumulative_net_profit=economics.net_profit,
                marginal_net_per_unit=marginal,
            )
        )

    quantity = chosen.quantity if chosen else ZERO
    top_of_book_cost = sum((ladder.asks[0].price for ladder in ladders), ZERO) * quantity
    violation = None
    for ladder in ladders:
        if chosen and ladder.min_order_size and quantity < ladder.min_order_size:
            violation = (
                f"{ladder.label}: quantity {quantity} below minimum order size {ladder.min_order_size}"
            )
    return BundlePlan(
        quantity=quantity,
        economics=chosen,
        top_cost_per_unit=top_cost
        + sum((unit_fee_unrounded(ladder.fee, ladder.asks[0].price) for ladder in ladders), ZERO),
        top_net_per_unit=top_net,
        top_of_book_cost=top_of_book_cost,
        curve=curve,
        stop_reason=stop_reason,
        greedy_quantity=greedy,
        min_order_violation=violation,
    )
