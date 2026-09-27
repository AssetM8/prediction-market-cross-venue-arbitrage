"""Quote-level gating (Stage 6, part two).

An opportunity is suppressed - recorded with a machine-readable reason but never
executable - when any book it relies on is stale, malformed, crossed, from the future,
empty on the side being bought, or thinner than the configured minimum depth, or when
either market or venue is not trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.core.metrics import METRICS
from app.domain.enums import MarketStatus
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import NormalizedMarket, NormalizedOrderBook

MAX_CLOCK_SKEW = timedelta(seconds=5)


@dataclass(frozen=True)
class GateConfig:
    stale_after_seconds: int
    min_top_of_book_depth: Decimal


def gate_leg(
    book: NormalizedOrderBook,
    snapshot: BinaryBookSnapshot,
    market: NormalizedMarket,
    *,
    now: datetime,
    config: GateConfig,
    venue_trading: bool = True,
) -> list[str]:
    """Return reasons to suppress buying from ``book`` (empty list means the leg passes)."""
    label = f"{book.venue.value}:{book.outcome_side.value}"
    reasons: list[str] = []
    if market.status is not MarketStatus.ACTIVE:
        reasons.append(f"market_not_active: {label} market status {market.status.value}")
    if not venue_trading:
        reasons.append(f"venue_trading_inactive: {book.venue.value}")
    age = book.age(now)
    if age > timedelta(seconds=config.stale_after_seconds):
        METRICS.inc("stale_books_total", venue=book.venue.value)
        reasons.append(f"stale_book: {label} age {int(age.total_seconds())}s > {config.stale_after_seconds}s")
    if book.observed_at - now > MAX_CLOCK_SKEW:
        reasons.append(f"future_timestamp: {label} book timestamp is ahead of the clock")
    issues = set(snapshot.yes.integrity_issues) | set(snapshot.no.integrity_issues)
    for issue in sorted(issues):
        kind = issue.split(":", 1)[0]
        reasons.append(f"{kind}: {label} {issue}")
    if not book.asks:
        reasons.append(f"no_ask_liquidity: {label}")
    elif book.asks[0].quantity < config.min_top_of_book_depth:
        reasons.append(
            f"insufficient_depth: {label} best ask size {book.asks[0].quantity} "
            f"< {config.min_top_of_book_depth}"
        )
    return reasons
