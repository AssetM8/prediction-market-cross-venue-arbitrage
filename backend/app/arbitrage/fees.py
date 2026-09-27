"""Venue taker-fee models (Decimal, explicit rounding).

Kalshi (fee schedule effective 2026-07-07)::

    fee = round_up(M * 0.07 * C * P * (1 - P))

"round up" is defined so that *fee + position cost* lands on a centicent ($0.0001). The
API fee-rounding guide adds that each fill's model fee is first rounded up to $0.000001
(``trade_fee = ceil_6dp(model_fee)``) and the balance change is then aligned to the
member's precision tier ($0.0001 direct, $0.01 non-direct) with a rounding fee. We apply
both steps per fill and ignore the accumulator rebate (which can only lower fees). ``M`` is
the series ``fee_multiplier`` (default 1; the conservative fallback when metadata is
missing is configurable). Each price level consumed is priced as a separate fill, which can
only increase the total, a conservative choice.

Polymarket (trading/fees guide)::

    fee = C * feeRate * p * (1 - p)        # generalised as (p * (1 - p)) ** exponent

charged to takers in collateral and rounded to 5 decimals (smallest fee 0.00001). The docs
do not state the rounding direction; we round *up*, which never understates cost.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from app.core.decimal_utils import DECIMAL_CONTEXT, ONE, ZERO
from app.domain.models import FeeMetadata

KALSHI_TRADE_FEE_QUANTUM = Decimal("0.000001")


class UnsupportedFeeModelError(ValueError):
    pass


def _curve(price: Decimal, exponent: Decimal) -> Decimal:
    base = price * (ONE - price)
    if exponent == ONE:
        return base
    return DECIMAL_CONTEXT.power(base, exponent)


def unit_fee_unrounded(fee: FeeMetadata, price: Decimal) -> Decimal:
    """Marginal fee for one contract at ``price`` before venue rounding (used to rank depth)."""
    if fee.model == "none" or ZERO in (fee.rate, fee.multiplier):
        return ZERO
    if fee.model == "kalshi_quadratic":
        return fee.multiplier * fee.rate * _curve(price, ONE)
    if fee.model == "polymarket_curve":
        return fee.multiplier * fee.rate * _curve(price, fee.exponent)
    raise UnsupportedFeeModelError(fee.model)


def fill_fee(fee: FeeMetadata, price: Decimal, quantity: Decimal) -> Decimal:
    """Taker fee for one fill of ``quantity`` contracts at ``price`` (venue rounding applied)."""
    if quantity <= ZERO:
        return ZERO
    raw = unit_fee_unrounded(fee, price) * quantity
    if raw == ZERO:
        return ZERO
    quantum = fee.rounding_quantum
    if fee.model == "kalshi_quadratic":
        trade_fee = raw.quantize(KALSHI_TRADE_FEE_QUANTUM, rounding=ROUND_CEILING)
        position_cost = price * quantity
        total = (position_cost + trade_fee).quantize(quantum, rounding=ROUND_CEILING)
        return total - position_cost
    return raw.quantize(quantum, rounding=ROUND_CEILING)


def describe(fee: FeeMetadata) -> str:
    formula = (
        f"round_up(M*rate*C*P*(1-P)) to fee+cost on {fee.rounding_quantum}"
        if fee.model == "kalshi_quadratic"
        else f"C*rate*(P*(1-P))^{fee.exponent} rounded up to {fee.rounding_quantum}"
    )
    return (
        f"{fee.model}: rate={fee.rate} multiplier={fee.multiplier} [{fee.source}] "
        f"{formula}; {fee.notes}".strip()
    )
