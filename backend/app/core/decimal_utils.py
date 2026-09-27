"""Decimal helpers.

All prices, quantities and money in this project are :class:`decimal.Decimal`.
Binary floating point is never used for accounting. Venue payloads arrive as JSON
strings (Kalshi fixed-point strings such as ``"0.4200"`` / ``"13.00"``; Polymarket
strings such as ``"0.45"``); :func:`parse_decimal` converts them without passing
through ``float``.

Rounding policy (explicit, conservative):

* Costs and fees are rounded *up* (``ROUND_CEILING``) when quantized, so estimated
  outlays are never understated.
* Proceeds and payouts are rounded *down* (``ROUND_FLOOR``) when quantized, so estimated
  receipts are never overstated.
* Ratios shown to humans (return on capital, confidence) are rounded half-even for display.
* Executable quantities are rounded *down* to the configured lot size.
"""

from __future__ import annotations

import decimal
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

# 28 significant digits is Python's default; we set it explicitly so behaviour does not
# depend on a caller mutating the global context.
DECIMAL_CONTEXT: Final = decimal.Context(prec=34, rounding=ROUND_HALF_EVEN)

ZERO: Final = Decimal(0)
ONE: Final = Decimal(1)

#: Money is stored to one micro-unit (Polymarket collateral has 6 decimals; Kalshi
#: fixed-point dollars have 4). Quantizing to 1e-6 never loses venue precision.
MONEY_QUANTUM: Final = Decimal("0.000001")

#: Ratios for display (return on capital, confidence).
RATIO_QUANTUM: Final = Decimal("0.000001")


class DecimalParseError(ValueError):
    """Raised when a venue value cannot be parsed as a finite decimal."""


def parse_decimal(value: object, *, field: str = "value") -> Decimal:
    """Parse a venue-supplied numeric value into a finite :class:`Decimal`.

    Strings and integers are parsed exactly. Floats are rejected unless they are
    integral, because a float has already lost precision before we see it; the only
    documented float fields we read (e.g. Polymarket ``orderPriceMinTickSize``) are
    converted through ``repr`` which yields the shortest round-tripping literal.
    """
    if isinstance(value, bool):
        raise DecimalParseError(f"{field}: booleans are not numeric")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        # repr() gives the shortest string that round-trips, e.g. 0.01 -> "0.01".
        result = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise DecimalParseError(f"{field}: empty string")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise DecimalParseError(f"{field}: {value!r} is not a decimal") from exc
    else:
        raise DecimalParseError(f"{field}: unsupported type {type(value).__name__}")
    if not result.is_finite():
        raise DecimalParseError(f"{field}: {value!r} is not finite")
    return result


def parse_optional_decimal(value: object, *, field: str = "value") -> Decimal | None:
    """Like :func:`parse_decimal` but maps ``None`` and ``""`` to ``None``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_decimal(value, field=field)


def round_cost_up(value: Decimal, quantum: Decimal = MONEY_QUANTUM) -> Decimal:
    """Quantize a cost or fee, rounding toward +infinity (never understate a cost)."""
    return value.quantize(quantum, rounding=ROUND_CEILING, context=DECIMAL_CONTEXT)


def round_proceeds_down(value: Decimal, quantum: Decimal = MONEY_QUANTUM) -> Decimal:
    """Quantize proceeds or payouts, rounding toward -infinity (never overstate)."""
    return value.quantize(quantum, rounding=ROUND_FLOOR, context=DECIMAL_CONTEXT)


def round_ratio(value: Decimal, quantum: Decimal = RATIO_QUANTUM) -> Decimal:
    """Quantize a ratio for display using banker's rounding."""
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN, context=DECIMAL_CONTEXT)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` down to a whole multiple of ``step`` (lot size / tick)."""
    if step <= ZERO:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def is_multiple_of(value: Decimal, step: Decimal) -> bool:
    """Return True when ``value`` is an exact multiple of ``step``."""
    if step <= ZERO:
        raise ValueError("step must be positive")
    return (value % step) == ZERO


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Divide, returning ``None`` instead of raising on a zero denominator."""
    if denominator == ZERO:
        return None
    return numerator / denominator


def dstr(value: Decimal | None) -> str | None:
    """Canonical string form used in logs and hashes (no exponent notation)."""
    if value is None:
        return None
    normalized = value.normalize(context=DECIMAL_CONTEXT)
    # normalize() may produce exponent form for integers like 1E+2; format fixes that.
    return format(normalized, "f")
