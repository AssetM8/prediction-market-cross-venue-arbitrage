"""Stage 1 - market eligibility for the MVP cross-venue pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import MarketStatus, OutcomeSide
from app.domain.models import NormalizedMarket


@dataclass(frozen=True)
class Eligibility:
    market_key: str
    eligible: bool
    reasons: tuple[str, ...]


def check_eligibility(market: NormalizedMarket, now: datetime) -> Eligibility:
    """Return whether ``market`` can enter candidate generation, with reasons if not.

    Criteria: active status, binary Yes/No structure, non-empty rules, a title, a close
    time in the future and (when known) a resolution time not in the past.
    """
    reasons: list[str] = []
    if market.status is not MarketStatus.ACTIVE:
        reasons.append(f"status is {market.status.value}, not active")
    sides = {outcome.side for outcome in market.outcomes}
    if market.market_type != "binary" or sides != {OutcomeSide.YES, OutcomeSide.NO}:
        reasons.append(
            f"unsupported contract structure ({market.market_type}); MVP handles binary Yes/No only"
        )
    if not market.rules.strip():
        reasons.append("rules text is empty; equivalence cannot be verified")
    if not market.title.strip():
        reasons.append("title missing")
    if market.close_time is None:
        reasons.append("close time missing")
    elif market.close_time <= now:
        reasons.append("trading already closed")
    if market.resolution_time is not None and market.resolution_time < now:
        reasons.append("resolution time already passed")
    if market.extra.get("mve"):
        reasons.append("multivariate combo market (out of scope)")
    return Eligibility(market_key=market.key, eligible=not reasons, reasons=tuple(reasons))
