"""Normalize Kalshi Trade API v2 payloads.

Schema notes (official docs, accessed 2026-09-27; see ``docs/SOURCES.md``):

* ``GET /events?with_nested_markets=true`` returns ``events[]`` with nested ``markets[]``.
  Market ``title``/``subtitle`` are deprecated; the proposition is the event title plus
  ``yes_sub_title`` and, authoritatively, ``rules_primary``/``rules_secondary``.
* Market ``status`` values in responses: initialized, inactive, active, closed, determined,
  disputed, amended, finalized (query filters use unopened/open/paused/closed/settled).
* Prices are fixed-point dollar strings (``*_dollars``) and counts fixed-point strings
  (``*_fp``); subpenny prices and fractional contracts exist, so everything is Decimal.
* ``GET /markets/{ticker}/orderbook`` returns ``orderbook_fp.yes_dollars`` and
  ``orderbook_fp.no_dollars`` as ``[price, count]`` *bids*. The order-book guide says they
  are sorted ascending (best last); the API reference says best to worst. Levels are
  re-sorted here, so either ordering is handled. Asks are derived:
  YES ask = 1 - best NO bid, NO ask = 1 - best YES bid.
* Series carry ``fee_type`` and ``fee_multiplier``; events may override both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.clock import parse_iso_datetime
from app.core.decimal_utils import ONE, DecimalParseError, parse_decimal, parse_optional_decimal
from app.core.ids import content_hash
from app.domain.enums import DataSource, MarketStatus, OutcomeSide, Venue
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import (
    BookLevel,
    FeeMetadata,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
)
from app.venues.common import NormalizationError, build_levels, complement_levels, crossed

KALSHI_STATUS_MAP: dict[str, MarketStatus] = {
    # response enum
    "initialized": MarketStatus.NOT_OPEN,
    "inactive": MarketStatus.PAUSED,
    "active": MarketStatus.ACTIVE,
    "closed": MarketStatus.CLOSED,
    "determined": MarketStatus.RESOLVING,
    "disputed": MarketStatus.RESOLVING,
    "amended": MarketStatus.RESOLVING,
    "finalized": MarketStatus.SETTLED,
    # query-filter vocabulary (accepted defensively)
    "unopened": MarketStatus.NOT_OPEN,
    "open": MarketStatus.ACTIVE,
    "paused": MarketStatus.PAUSED,
    "settled": MarketStatus.SETTLED,
}

QUADRATIC_FEE_TYPES = frozenset({"quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"})

MARKET_URL = "https://kalshi.com/markets/{series}/{event}"


@dataclass(frozen=True)
class KalshiFeeDefaults:
    taker_rate: Decimal
    rounding_quantum: Decimal
    fallback_multiplier: Decimal


def _dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return parse_iso_datetime(value)


def _settlement_sources(*sources: object) -> str | None:
    for source in sources:
        if isinstance(source, list) and source:
            parts = []
            for item in source:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    url = str(item.get("url") or "").strip()
                    parts.append(f"{name} ({url})" if url else name)
            text = "; ".join(part for part in parts if part)
            if text:
                return text
    return None


def kalshi_fee_metadata(
    event: dict[str, Any] | None, series: dict[str, Any] | None, defaults: KalshiFeeDefaults
) -> FeeMetadata:
    """Derive the taker-fee model from series/event metadata.

    Kalshi's published schedule (effective 2026-07-07): taker fee
    ``round_up(M * 0.07 * C * P * (1 - P))`` rounded so that fee plus position cost lands on
    a centicent ($0.0001). ``M`` is the series ``fee_multiplier`` (event
    ``fee_multiplier_override`` wins). When metadata is missing or the fee type is not a
    documented quadratic variant, a conservative fallback multiplier is used and flagged.
    """
    event = event or {}
    fee_type = event.get("fee_type_override") or (series or {}).get("fee_type")
    multiplier_raw = event.get("fee_multiplier_override")
    if multiplier_raw is None and series is not None:
        multiplier_raw = series.get("fee_multiplier")
    if fee_type in QUADRATIC_FEE_TYPES and multiplier_raw is not None:
        try:
            multiplier = parse_decimal(multiplier_raw, field="fee_multiplier")
        except DecimalParseError:
            multiplier = None
        if multiplier is not None and multiplier >= 0:
            return FeeMetadata(
                model="kalshi_quadratic",
                rate=defaults.taker_rate,
                multiplier=multiplier,
                rounding_quantum=defaults.rounding_quantum,
                source="documented_metadata",
                notes=f"series fee_type={fee_type}, fee_multiplier={multiplier}",
            )
    reason = (
        f"fee_type={fee_type!r} not a documented quadratic variant"
        if fee_type
        else "series fee metadata unavailable"
    )
    return FeeMetadata(
        model="kalshi_quadratic",
        rate=defaults.taker_rate,
        multiplier=defaults.fallback_multiplier,
        rounding_quantum=defaults.rounding_quantum,
        source="fallback_assumption",
        notes=f"{reason}; using conservative multiplier {defaults.fallback_multiplier}",
    )


def normalize_kalshi_market(
    raw: dict[str, Any],
    *,
    event: dict[str, Any] | None,
    series: dict[str, Any] | None,
    data_source: DataSource,
    fee_defaults: KalshiFeeDefaults,
) -> NormalizedMarket:
    """Convert one Kalshi market (plus its event/series context) into a NormalizedMarket."""
    try:
        ticker = str(raw["ticker"])
        event_ticker = str(raw["event_ticker"])
    except KeyError as exc:
        raise NormalizationError(f"kalshi market missing field {exc}") from exc
    event = event or {}
    event_title = str(event.get("title") or "").strip()
    yes_sub = str(raw.get("yes_sub_title") or "").strip()
    legacy_title = str(raw.get("title") or "").strip()
    if legacy_title:
        title = legacy_title
    elif event_title and yes_sub:
        title = f"{event_title} — {yes_sub}"
    else:
        title = event_title or yes_sub or ticker
    rules_primary = str(raw.get("rules_primary") or "").strip()
    rules_secondary = str(raw.get("rules_secondary") or "").strip()
    rules = "\n\n".join(part for part in (rules_primary, rules_secondary) if part)
    status = KALSHI_STATUS_MAP.get(str(raw.get("status") or "").lower(), MarketStatus.UNKNOWN)
    series_ticker = str(event.get("series_ticker") or (series or {}).get("ticker") or "")
    category = str((series or {}).get("category") or event.get("category") or "")

    tick_size: Decimal | None = None
    ranges = raw.get("price_ranges")
    if isinstance(ranges, list):
        steps = [
            parse_optional_decimal(item.get("step"), field="price_ranges.step")
            for item in ranges
            if isinstance(item, dict)
        ]
        positive = [step for step in steps if step is not None and step > 0]
        tick_size = min(positive) if positive else None

    proposition = rules_primary or title
    outcomes = (
        NormalizedOutcome(
            outcome_id=f"{ticker}:yes",
            label=yes_sub or "Yes",
            proposition_text=proposition,
            side=OutcomeSide.YES,
            settlement_value=ONE,
        ),
        NormalizedOutcome(
            outcome_id=f"{ticker}:no",
            label=str(raw.get("no_sub_title") or "No"),
            proposition_text=f"NOT ({proposition})",
            side=OutcomeSide.NO,
            settlement_value=ONE,
        ),
    )
    strike: dict[str, Any] = {
        key: raw.get(key)
        for key in ("strike_type", "floor_strike", "cap_strike", "functional_strike")
        if raw.get(key) is not None
    }
    return NormalizedMarket(
        venue=Venue.KALSHI,
        venue_market_id=ticker,
        venue_event_id=event_ticker,
        title=title,
        event_title=event_title,
        description=str(event.get("sub_title") or ""),
        rules=rules,
        category=category,
        status=status,
        market_type=str(raw.get("market_type") or "binary"),
        open_time=_dt(raw.get("open_time")),
        close_time=_dt(raw.get("close_time")),
        resolution_time=_dt(raw.get("expected_expiration_time")) or _dt(raw.get("latest_expiration_time")),
        resolution_source=_settlement_sources(
            (series or {}).get("settlement_sources"), event.get("settlement_sources")
        ),
        outcomes=outcomes,
        tick_size=tick_size,
        min_order_size=ONE,
        fee_metadata=kalshi_fee_metadata(event, series, fee_defaults),
        raw_metadata_hash=content_hash({"market": raw, "event": event, "series": series}),
        data_source=data_source,
        url=MARKET_URL.format(series=series_ticker.lower(), event=event_ticker.lower())
        if series_ticker
        else None,
        extra={
            "series_ticker": series_ticker,
            "mutually_exclusive": bool(event.get("mutually_exclusive", False)),
            "can_close_early": bool(raw.get("can_close_early", False)),
            "early_close_condition": raw.get("early_close_condition"),
            "strike": strike,
            "yes_sub_title": yes_sub,
            "mve": bool(raw.get("mve_collection_ticker")),
            "series_enriched": series is not None,
        },
    )


def normalize_kalshi_orderbook(
    payload: dict[str, Any],
    *,
    market: NormalizedMarket,
    received_at: datetime,
    data_source: DataSource,
) -> BinaryBookSnapshot:
    """Normalize ``GET /markets/{ticker}/orderbook`` into YES and NO books.

    Kalshi returns bids only. For each outcome we keep its own bids and derive its asks
    from the complementary outcome's bids (``ask_yes = 1 - bid_no``), which is valid
    because a YES and a NO contract on the same market together always pay exactly 1.
    """
    book = payload.get("orderbook_fp")
    if not isinstance(book, dict):
        raise NormalizationError("kalshi orderbook payload lacks 'orderbook_fp'")
    issues: list[str] = []
    yes_raw = book.get("yes_dollars") or []
    no_raw = book.get("no_dollars") or []
    if not isinstance(yes_raw, list) or not isinstance(no_raw, list):
        raise NormalizationError("kalshi orderbook sides must be arrays")
    yes_bids = build_levels(_pairs(yes_raw, issues, "yes"), descending=True, issues=issues, label="yes")
    no_bids = build_levels(_pairs(no_raw, issues, "no"), descending=True, issues=issues, label="no")
    yes_asks = complement_levels(no_bids)
    no_asks = complement_levels(yes_bids)
    if crossed(yes_bids, yes_asks):
        issues.append("crossed_book: best YES bid + best NO bid >= 1")
    source_hash = content_hash(payload)
    return _snapshot(
        market=market,
        yes_bids=yes_bids,
        yes_asks=yes_asks,
        no_bids=no_bids,
        no_asks=no_asks,
        received_at=received_at,
        source_hash=source_hash,
        data_source=data_source,
        issues=tuple(issues),
        depth_limited=False,
    )


def normalize_kalshi_top_of_book(
    raw_market: dict[str, Any],
    *,
    market: NormalizedMarket,
    received_at: datetime,
    data_source: DataSource,
) -> BinaryBookSnapshot:
    """Build a one-level book from market-level quotes (fallback when depth is unavailable).

    Uses ``yes_bid_dollars``/``yes_bid_size_fp`` and ``yes_ask_dollars``/``yes_ask_size_fp``.
    The YES ask is itself a NO bid at ``1 - yes_ask``. The snapshot is marked
    ``depth_limited`` so the optimizer never assumes size beyond the best level.
    """
    issues: list[str] = []
    yes_bid = parse_optional_decimal(raw_market.get("yes_bid_dollars"), field="yes_bid_dollars")
    yes_bid_size = parse_optional_decimal(raw_market.get("yes_bid_size_fp"), field="yes_bid_size_fp")
    yes_ask = parse_optional_decimal(raw_market.get("yes_ask_dollars"), field="yes_ask_dollars")
    yes_ask_size = parse_optional_decimal(raw_market.get("yes_ask_size_fp"), field="yes_ask_size_fp")
    yes_levels: list[tuple[object, object]] = []
    no_levels: list[tuple[object, object]] = []
    if yes_bid is not None and yes_bid_size is not None and yes_bid > 0:
        yes_levels.append((yes_bid, yes_bid_size))
    if yes_ask is not None and yes_ask_size is not None and 0 < yes_ask < 1:
        no_levels.append((Decimal(1) - yes_ask, yes_ask_size))
    yes_bids = build_levels(yes_levels, descending=True, issues=issues, label="yes")
    no_bids = build_levels(no_levels, descending=True, issues=issues, label="no")
    return _snapshot(
        market=market,
        yes_bids=yes_bids,
        yes_asks=complement_levels(no_bids),
        no_bids=no_bids,
        no_asks=complement_levels(yes_bids),
        received_at=received_at,
        source_hash=content_hash(raw_market),
        data_source=data_source,
        issues=tuple(issues),
        depth_limited=True,
    )


def _pairs(raw: list[Any], issues: list[str], label: str) -> list[tuple[object, object]]:
    pairs: list[tuple[object, object]] = []
    for index, item in enumerate(raw):
        if isinstance(item, list | tuple) and len(item) == 2:
            pairs.append((item[0], item[1]))
        else:
            issues.append(f"malformed_level:{label}[{index}]: expected [price, count]")
    return pairs


def _snapshot(
    *,
    market: NormalizedMarket,
    yes_bids: tuple[BookLevel, ...],
    yes_asks: tuple[BookLevel, ...],
    no_bids: tuple[BookLevel, ...],
    no_asks: tuple[BookLevel, ...],
    received_at: datetime,
    source_hash: str,
    data_source: DataSource,
    issues: tuple[str, ...],
    depth_limited: bool,
) -> BinaryBookSnapshot:
    common = {
        "venue": Venue.KALSHI,
        "market_id": market.venue_market_id,
        "sequence_or_timestamp": received_at.isoformat(),
        "received_at": received_at,
        "source_timestamp": None,
        "checksum_or_source_hash": source_hash,
        "tick_size": market.tick_size,
        "min_order_size": market.min_order_size,
        "derived_asks": True,
        "depth_limited": depth_limited,
        "data_source": data_source,
        "integrity_issues": issues,
    }
    yes = NormalizedOrderBook(
        outcome_id=market.outcome(OutcomeSide.YES).outcome_id,
        outcome_side=OutcomeSide.YES,
        bids=yes_bids,
        asks=yes_asks,
        **common,  # type: ignore[arg-type]
    )
    no = NormalizedOrderBook(
        outcome_id=market.outcome(OutcomeSide.NO).outcome_id,
        outcome_side=OutcomeSide.NO,
        bids=no_bids,
        asks=no_asks,
        **common,  # type: ignore[arg-type]
    )
    return BinaryBookSnapshot(market_id=market.venue_market_id, yes=yes, no=no)
