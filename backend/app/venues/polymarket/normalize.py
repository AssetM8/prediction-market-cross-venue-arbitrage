"""Normalize Polymarket Gamma (metadata) and CLOB (order book) payloads.

Schema notes (official docs, accessed 2026-09-27; see ``docs/SOURCES.md``):

* Gamma ``GET /markets/keyset`` returns ``{"markets": [...], "next_cursor": ...}``;
  ``outcomes`` and ``clobTokenIds`` are JSON-encoded string arrays aligned by index.
* ``feesEnabled`` plus ``feeSchedule {rate, exponent, takerOnly, rebateRate}`` describe
  taker fees; the fee guide gives ``fee = C * feeRate * p * (1 - p)`` (in collateral),
  rounded to 5 decimals, takers only.
* CLOB ``GET /book`` / ``POST /books`` return ``bids``/``asks`` of ``{price, size}``
  strings plus ``timestamp`` (epoch ms string), ``hash``, ``tick_size`` and
  ``min_order_size``. The API reference and the market-data guide disagree on level
  ordering, so levels are always re-sorted here.
* Each YES/NO share pays 1 unit of collateral (pUSD since the April 2026 CLOB V2 upgrade).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.clock import from_epoch_millis, parse_iso_datetime
from app.core.decimal_utils import ONE, DecimalParseError, parse_decimal, parse_optional_decimal
from app.core.ids import content_hash
from app.domain.enums import DataSource, MarketStatus, OutcomeSide, Venue
from app.domain.models import FeeMetadata, NormalizedMarket, NormalizedOrderBook, NormalizedOutcome
from app.venues.common import NormalizationError, build_levels, crossed

EVENT_URL = "https://polymarket.com/event/{slug}"
MAX_PLAUSIBLE_FEE_RATE = Decimal("0.25")


@dataclass(frozen=True)
class PolymarketFeeDefaults:
    fallback_rate: Decimal
    rounding_quantum: Decimal


def _json_list(value: object, field: str) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise NormalizationError(f"polymarket {field} is not valid JSON") from exc
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    raise NormalizationError(f"polymarket {field} missing or not a list")


def _dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return parse_iso_datetime(value)


def polymarket_fee_metadata(raw: dict[str, Any], defaults: PolymarketFeeDefaults) -> FeeMetadata:
    """Derive the taker-fee model from Gamma market metadata.

    Documented metadata wins; missing or implausible values fall back to the configured
    conservative rate (the highest published category rate) and are flagged.
    """
    fees_enabled = raw.get("feesEnabled")
    schedule = raw.get("feeSchedule") if isinstance(raw.get("feeSchedule"), dict) else None
    if fees_enabled is False:
        return FeeMetadata(
            model="polymarket_curve",
            rate=Decimal(0),
            rounding_quantum=defaults.rounding_quantum,
            source="documented_metadata",
            notes="feesEnabled=false",
        )
    if fees_enabled is True and schedule is not None:
        try:
            rate = parse_decimal(schedule.get("rate"), field="feeSchedule.rate")
            exponent = parse_optional_decimal(schedule.get("exponent"), field="feeSchedule.exponent")
        except DecimalParseError:
            rate, exponent = Decimal(-1), None
        if Decimal(0) <= rate <= MAX_PLAUSIBLE_FEE_RATE:
            effective_exponent = exponent if exponent is not None and exponent > 0 else ONE
            return FeeMetadata(
                model="polymarket_curve",
                rate=rate,
                exponent=effective_exponent,
                rounding_quantum=defaults.rounding_quantum,
                taker_only=bool(schedule.get("takerOnly", True)),
                source="documented_metadata",
                notes=f"feeSchedule.rate={rate}, exponent={effective_exponent}",
            )
    return FeeMetadata(
        model="polymarket_curve",
        rate=defaults.fallback_rate,
        rounding_quantum=defaults.rounding_quantum,
        source="fallback_assumption",
        notes=(
            f"fee metadata missing or implausible (feesEnabled={fees_enabled!r}); "
            f"using conservative rate {defaults.fallback_rate}"
        ),
    )


def _status(raw: dict[str, Any]) -> MarketStatus:
    if raw.get("archived"):
        return MarketStatus.SETTLED
    if raw.get("closed"):
        resolution = str(raw.get("umaResolutionStatus") or "").lower()
        return MarketStatus.SETTLED if resolution == "resolved" else MarketStatus.CLOSED
    if not raw.get("active"):
        return MarketStatus.NOT_OPEN
    if raw.get("acceptingOrders") is False or raw.get("enableOrderBook") is False:
        return MarketStatus.PAUSED
    return MarketStatus.ACTIVE


def normalize_polymarket_market(
    raw: dict[str, Any], *, data_source: DataSource, fee_defaults: PolymarketFeeDefaults
) -> NormalizedMarket:
    """Convert a Gamma market object. Non Yes/No markets keep ``market_type='non_binary'``
    and no outcomes, so Stage-1 eligibility rejects them with an explicit reason."""
    try:
        market_id = str(raw["id"])
        question = str(raw["question"]).strip()
    except KeyError as exc:
        raise NormalizationError(f"polymarket market missing field {exc}") from exc
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    event_id = str(event.get("id") or raw.get("negRiskMarketID") or raw.get("conditionId") or market_id)
    description = str(raw.get("description") or "").strip()

    outcome_labels: list[str] = []
    token_ids: list[str] = []
    try:
        outcome_labels = _json_list(raw.get("outcomes"), "outcomes")
        token_ids = _json_list(raw.get("clobTokenIds"), "clobTokenIds")
    except NormalizationError:
        outcome_labels, token_ids = [], []
    lowered = [label.strip().lower() for label in outcome_labels]
    outcomes: tuple[NormalizedOutcome, ...] = ()
    market_type = "non_binary"
    if len(outcome_labels) == 2 and len(token_ids) == 2 and sorted(lowered) == ["no", "yes"]:
        market_type = "binary"
        yes_index = lowered.index("yes")
        no_index = lowered.index("no")
        outcomes = (
            NormalizedOutcome(
                outcome_id=token_ids[yes_index],
                label=outcome_labels[yes_index],
                proposition_text=question,
                side=OutcomeSide.YES,
                settlement_value=ONE,
                token_id=token_ids[yes_index],
            ),
            NormalizedOutcome(
                outcome_id=token_ids[no_index],
                label=outcome_labels[no_index],
                proposition_text=f"NOT ({question})",
                side=OutcomeSide.NO,
                settlement_value=ONE,
                token_id=token_ids[no_index],
            ),
        )

    categories = raw.get("categories") if isinstance(raw.get("categories"), list) else []
    category = str(raw.get("category") or "")
    if not category and categories and isinstance(categories[0], dict):
        category = str(categories[0].get("label") or categories[0].get("slug") or "")
    tick = parse_optional_decimal(raw.get("orderPriceMinTickSize"), field="orderPriceMinTickSize")
    min_size = parse_optional_decimal(raw.get("orderMinSize"), field="orderMinSize")
    slug = str(event.get("slug") or raw.get("slug") or "")
    raw_event_markets = event.get("markets")
    event_markets: list[Any] = raw_event_markets if isinstance(raw_event_markets, list) else []
    event_market_ids = [
        str(item["id"]) for item in event_markets if isinstance(item, dict) and item.get("id") is not None
    ]
    return NormalizedMarket(
        venue=Venue.POLYMARKET,
        venue_market_id=market_id,
        venue_event_id=event_id,
        title=question,
        event_title=str(event.get("title") or ""),
        description=description,
        rules=description,
        category=category,
        status=_status(raw),
        market_type=market_type,
        open_time=_dt(raw.get("startDate")),
        close_time=_dt(raw.get("endDate")),
        resolution_time=_dt(raw.get("endDate")),
        resolution_source=str(raw.get("resolutionSource") or "").strip() or None,
        outcomes=outcomes,
        tick_size=tick,
        min_order_size=min_size,
        fee_metadata=polymarket_fee_metadata(raw, fee_defaults),
        raw_metadata_hash=content_hash(raw),
        data_source=data_source,
        url=EVENT_URL.format(slug=slug) if slug else None,
        extra={
            "condition_id": raw.get("conditionId"),
            "slug": raw.get("slug"),
            "neg_risk": bool(raw.get("negRisk", False)),
            "neg_risk_market_id": raw.get("negRiskMarketID"),
            "event_slug": event.get("slug"),
            "outcome_labels": outcome_labels,
            "restricted": bool(raw.get("restricted", False)),
            "event_market_ids": event_market_ids,
        },
    )


def normalize_polymarket_book(
    payload: dict[str, Any],
    *,
    market: NormalizedMarket,
    side: OutcomeSide,
    received_at: datetime,
    data_source: DataSource,
) -> NormalizedOrderBook:
    """Normalize one CLOB ``OrderBookSummary`` for the given outcome token."""
    outcome = market.outcome(side)
    asset_id = str(payload.get("asset_id") or "")
    issues: list[str] = []
    if asset_id and outcome.token_id and asset_id != outcome.token_id:
        raise NormalizationError(
            f"book asset_id {asset_id[:12]}... does not match token {outcome.token_id[:12]}..."
        )
    raw_bids = payload.get("bids") or []
    raw_asks = payload.get("asks") or []
    if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
        raise NormalizationError("polymarket book sides must be arrays")
    bids = build_levels(_levels(raw_bids, issues, "bids"), descending=True, issues=issues, label="bids")
    asks = build_levels(_levels(raw_asks, issues, "asks"), descending=False, issues=issues, label="asks")
    if crossed(bids, asks):
        issues.append("crossed_book: best bid >= best ask")
    source_ts: datetime | None = None
    raw_ts = payload.get("timestamp")
    if raw_ts not in (None, ""):
        try:
            source_ts = from_epoch_millis(str(raw_ts))
        except ValueError:
            issues.append("malformed_timestamp")
    tick = parse_optional_decimal(payload.get("tick_size"), field="tick_size") or market.tick_size
    min_size = (
        parse_optional_decimal(payload.get("min_order_size"), field="min_order_size") or market.min_order_size
    )
    return NormalizedOrderBook(
        venue=Venue.POLYMARKET,
        market_id=market.venue_market_id,
        outcome_id=outcome.outcome_id,
        outcome_side=side,
        sequence_or_timestamp=str(raw_ts or received_at.isoformat()),
        received_at=received_at,
        source_timestamp=source_ts,
        bids=bids,
        asks=asks,
        checksum_or_source_hash=str(payload.get("hash") or content_hash(payload)),
        tick_size=tick,
        min_order_size=min_size,
        derived_asks=False,
        depth_limited=False,
        data_source=data_source,
        integrity_issues=tuple(issues),
    )


def _levels(raw: list[Any], issues: list[str], label: str) -> list[tuple[object, object]]:
    pairs: list[tuple[object, object]] = []
    for index, item in enumerate(raw):
        if isinstance(item, dict) and "price" in item and "size" in item:
            pairs.append((item["price"], item["size"]))
        else:
            issues.append(f"malformed_level:{label}[{index}]: expected {{price, size}}")
    return pairs
