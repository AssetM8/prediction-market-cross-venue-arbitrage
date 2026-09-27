from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.decimal_utils import (
    DecimalParseError,
    dstr,
    floor_to_step,
    is_multiple_of,
    parse_decimal,
    parse_optional_decimal,
    round_cost_up,
    round_proceeds_down,
    round_ratio,
    safe_div,
)

D = Decimal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.4200", D("0.42")),
        ("13.00", D(13)),
        (" 0.5 ", D("0.5")),
        (7, D(7)),
        (0.01, D("0.01")),
        (D("1.5"), D("1.5")),
    ],
)
def test_parse_decimal_exact(raw: object, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


def test_float_is_parsed_via_repr_not_binary_expansion() -> None:
    # Decimal(0.1) would be 0.1000000000000000055511151231257827...
    assert parse_decimal(0.1) == D("0.1")


@pytest.mark.parametrize("raw", ["", "abc", "NaN", "Infinity", True, None, [1]])
def test_parse_decimal_rejects_invalid(raw: object) -> None:
    with pytest.raises(DecimalParseError):
        parse_decimal(raw)


def test_parse_optional_decimal() -> None:
    assert parse_optional_decimal(None) is None
    assert parse_optional_decimal("") is None
    assert parse_optional_decimal("0.25") == D("0.25")


def test_rounding_directions_are_conservative() -> None:
    assert round_cost_up(D("1.0000001")) == D("1.000001")
    assert round_proceeds_down(D("1.0000019")) == D("1.000001")
    assert round_cost_up(D("-0.0000001")) == D("0.000000")
    assert round_ratio(D("0.0000005")) == D("0.000000")  # half-even
    assert round_ratio(D("0.0000015")) == D("0.000002")


def test_floor_to_step_and_multiples() -> None:
    assert floor_to_step(D("149.99"), D(1)) == D(149)
    assert floor_to_step(D("0.057"), D("0.01")) == D("0.05")
    assert is_multiple_of(D("0.30"), D("0.01"))
    assert not is_multiple_of(D("0.305"), D("0.01"))
    with pytest.raises(ValueError, match="positive"):
        floor_to_step(D(1), D(0))


def test_safe_div_and_dstr() -> None:
    assert safe_div(D(1), D(0)) is None
    assert safe_div(D(1), D(4)) == D("0.25")
    assert dstr(D("1E+2")) == "100"
    assert dstr(D("0.4200")) == "0.42"
    assert dstr(None) is None
