"""Venue-agnostic errors and helpers used by the Kalshi and Polymarket normalizers."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import ValidationError

from app.core.decimal_utils import DecimalParseError, parse_decimal
from app.domain.models import BookLevel


class NormalizationError(ValueError):
    """A venue payload did not match the documented schema."""


def build_levels(
    raw_levels: Iterable[tuple[object, object]],
    *,
    descending: bool,
    issues: list[str],
    label: str,
) -> tuple[BookLevel, ...]:
    """Parse ``(price, quantity)`` pairs into sorted, de-duplicated :class:`BookLevel` s.

    Invalid levels (unparseable, price outside (0, 1), non-positive size) are dropped and
    recorded in ``issues``; the gating stage suppresses any book with integrity issues, so
    a malformed level can never silently feed an opportunity.
    """
    aggregated: dict[Decimal, Decimal] = {}
    for index, (raw_price, raw_quantity) in enumerate(raw_levels):
        try:
            price = parse_decimal(raw_price, field=f"{label}[{index}].price")
            quantity = parse_decimal(raw_quantity, field=f"{label}[{index}].quantity")
            level = BookLevel(price=price, quantity=quantity)
        except (DecimalParseError, ValidationError) as exc:
            issues.append(f"malformed_level:{label}[{index}]:{_first_line(exc)}")
            continue
        aggregated[level.price] = aggregated.get(level.price, Decimal(0)) + level.quantity
    ordered = sorted(aggregated.items(), key=lambda item: item[0], reverse=descending)
    return tuple(BookLevel(price=price, quantity=quantity) for price, quantity in ordered)


def complement_levels(levels: Iterable[BookLevel]) -> tuple[BookLevel, ...]:
    """Map bids on one outcome to asks on the complementary outcome.

    In a binary market a bid for NO at ``p`` is an offer to take the other side of YES at
    ``1 - p`` (the two contracts together always pay exactly 1). Kalshi documents this:
    "A YES BID at price X is equivalent to a NO ASK at price ($1.00 - X)". The result is
    sorted best (lowest) ask first.
    """
    derived = [BookLevel(price=Decimal(1) - level.price, quantity=level.quantity) for level in levels]
    return tuple(sorted(derived, key=lambda level: level.price))


def crossed(bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...]) -> bool:
    return bool(bids and asks and bids[0].price >= asks[0].price)


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0][:160]
