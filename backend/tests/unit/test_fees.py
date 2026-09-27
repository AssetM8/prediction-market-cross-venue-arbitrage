from __future__ import annotations

from decimal import Decimal

import pytest

from app.arbitrage.fees import describe, fill_fee, unit_fee_unrounded
from app.domain.models import FeeMetadata
from tests.conftest import KALSHI_FEE, NO_FEE

D = Decimal


def poly(rate: str, exponent: str = "1") -> FeeMetadata:
    return FeeMetadata(
        model="polymarket_curve",
        rate=D(rate),
        exponent=D(exponent),
        rounding_quantum=D("0.00001"),
        source="test",
    )


@pytest.mark.parametrize(
    ("price", "quantity", "expected"),
    [
        ("0.50", "100", "1.75"),  # 0.07 * 100 * 0.25
        ("0.4125", "3", "0.0509"),  # subpenny: fee + cost rounded up to a centicent
        ("0.99", "1", "0.0007"),
        ("0.01", "1", "0.0007"),
    ],
)
def test_kalshi_quadratic_fee_with_centicent_rounding(price: str, quantity: str, expected: str) -> None:
    assert fill_fee(KALSHI_FEE, D(price), D(quantity)) == D(expected)


def test_kalshi_cent_precision_tier_rounds_fee_plus_cost_to_a_cent() -> None:
    # Non-direct members settle to $0.01: 3 @ 0.50 -> raw 0.0525, cost 1.50, debit 1.56.
    cent_tier = KALSHI_FEE.model_copy(update={"rounding_quantum": D("0.01")})
    assert fill_fee(cent_tier, D("0.50"), D(3)) == D("0.06")


def test_kalshi_trade_fee_is_rounded_up_to_micro_dollars_first() -> None:
    # Fractional contracts at a subpenny price: cost has more than 6 decimals.
    fee = fill_fee(KALSHI_FEE, D("0.4125"), D("0.37"))
    raw = D("0.07") * D("0.4125") * D("0.5875") * D("0.37")
    assert fee >= raw
    assert (D("0.4125") * D("0.37") + fee) % D("0.0001") == 0


def test_kalshi_multiplier_scales_fee() -> None:
    doubled = KALSHI_FEE.model_copy(update={"multiplier": D(2)})
    assert fill_fee(doubled, D("0.50"), D(100)) == D("3.50")
    free = KALSHI_FEE.model_copy(update={"multiplier": D(0)})
    assert fill_fee(free, D("0.50"), D(100)) == 0


@pytest.mark.parametrize(
    ("rate", "price", "quantity", "expected"),
    [
        ("0.07", "0.50", "100", "1.75"),  # Polymarket fee guide worked example (crypto)
        ("0.07", "0.30", "100", "1.47"),
        ("0.07", "0.70", "100", "1.47"),  # symmetric around 0.5
        ("0.05", "0.33", "1", "0.01106"),  # 0.011055 rounded up to 5 decimals
        ("0", "0.50", "100", "0"),
    ],
)
def test_polymarket_fee_curve(rate: str, price: str, quantity: str, expected: str) -> None:
    assert fill_fee(poly(rate), D(price), D(quantity)) == D(expected)


def test_exponent_generalisation_and_marginal_fee() -> None:
    assert unit_fee_unrounded(poly("0.04", "2"), D("0.5")) == D("0.04") * D("0.0625")
    assert unit_fee_unrounded(NO_FEE, D("0.5")) == 0
    assert fill_fee(poly("0.05"), D("0.5"), D(0)) == 0


def test_fee_description_mentions_source() -> None:
    assert "fixture" in describe(KALSHI_FEE)
    assert "kalshi_quadratic" in describe(KALSHI_FEE)
