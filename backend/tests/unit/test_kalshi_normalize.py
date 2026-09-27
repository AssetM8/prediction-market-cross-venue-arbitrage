from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.enums import DataSource, MarketStatus, OutcomeSide
from app.venues.common import NormalizationError, complement_levels
from app.venues.fixture import FixtureSet
from app.venues.kalshi.normalize import (
    KalshiFeeDefaults,
    normalize_kalshi_market,
    normalize_kalshi_orderbook,
    normalize_kalshi_top_of_book,
)
from tests.conftest import levels

D = Decimal
NOW = datetime(2026, 9, 25, 14, tzinfo=UTC)
DEFAULTS = KalshiFeeDefaults(taker_rate=D("0.07"), rounding_quantum=D("0.0001"), fallback_multiplier=D(2))


def _market(fixture_set: FixtureSet, ticker: str, *, with_series: bool = True):  # type: ignore[no-untyped-def]
    raw = fixture_set.kalshi_markets[ticker]
    event = next(e for e in fixture_set.kalshi_events if e["event_ticker"] == raw["event_ticker"])
    series = fixture_set.kalshi_series.get(event["series_ticker"]) if with_series else None
    return normalize_kalshi_market(
        raw, event=event, series=series, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )


def test_market_normalization(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXFEDDECISION-26DEC-C25")
    assert market.title == "Fed decision in December 2026? — Cut 25bps"
    assert market.status is MarketStatus.ACTIVE
    assert market.tick_size == D("0.01")
    assert "exactly 25 basis points" in market.rules
    assert "rescheduled meeting" in market.rules
    assert market.resolution_source == "Federal Reserve (https://www.federalreserve.gov)"
    assert market.fee_metadata.source == "documented_metadata"
    assert market.fee_metadata.multiplier == D(1)
    assert market.outcome(OutcomeSide.YES).outcome_id == "KXFEDDECISION-26DEC-C25:yes"
    assert market.extra["mutually_exclusive"] is True
    assert market.category == "Economics"


def test_missing_series_uses_conservative_fee_fallback(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXARTEMIS3-27JUL", with_series=True)  # series absent from fixtures
    assert market.fee_metadata.source == "fallback_assumption"
    assert market.fee_metadata.multiplier == D(2)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", MarketStatus.ACTIVE),
        ("initialized", MarketStatus.NOT_OPEN),
        ("inactive", MarketStatus.PAUSED),
        ("closed", MarketStatus.CLOSED),
        ("determined", MarketStatus.RESOLVING),
        ("finalized", MarketStatus.SETTLED),
        ("open", MarketStatus.ACTIVE),
        ("mystery", MarketStatus.UNKNOWN),
    ],
)
def test_status_mapping(fixture_set: FixtureSet, status: str, expected: MarketStatus) -> None:
    raw = dict(fixture_set.kalshi_markets["KXUNRATE-26OCT-T4.5"], status=status)
    market = normalize_kalshi_market(
        raw, event=None, series=None, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )
    assert market.status is expected


def test_orderbook_bids_only_are_normalized_with_complement_asks(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXFEDDECISION-26DEC-C25")
    snapshot = normalize_kalshi_orderbook(
        fixture_set.kalshi_orderbooks["KXFEDDECISION-26DEC-C25"],
        market=market,
        received_at=NOW,
        data_source=DataSource.FIXTURE,
    )
    yes, no = snapshot.yes, snapshot.no
    # YES bids best-first straight from yes_dollars (which Kalshi sends ascending, best last)
    assert [lv.price for lv in yes.bids] == [D("0.36"), D("0.34"), D("0.31")]
    # YES asks = 1 - NO bids, best (lowest) first: NO bids 0.60/0.58/0.55 -> 0.40/0.42/0.45
    assert [(lv.price, lv.quantity) for lv in yes.asks] == [
        (D("0.40"), D(150)),
        (D("0.42"), D(250)),
        (D("0.45"), D(400)),
    ]
    assert [lv.price for lv in no.asks] == [D("0.64"), D("0.66"), D("0.69")]
    assert yes.derived_asks and no.derived_asks
    assert yes.integrity_issues == ()
    assert yes.outcome_id.endswith(":yes") and no.outcome_id.endswith(":no")


def test_orderbook_aggregates_duplicates_and_records_malformed_levels(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXUNRATE-26OCT-T4.5")
    payload = {
        "orderbook_fp": {
            "yes_dollars": [
                ["0.3000", "10.00"],
                ["0.3000", "5.00"],
                ["1.2000", "3.00"],
                ["abc", "1"],
                "junk",
            ],
            "no_dollars": [["0.6000", "7.50"], ["0.6500", "-1.00"]],
        }
    }
    snapshot = normalize_kalshi_orderbook(
        payload, market=market, received_at=NOW, data_source=DataSource.FIXTURE
    )
    assert snapshot.yes.bids[0].quantity == D(15)
    assert len(snapshot.yes.integrity_issues) == 4
    assert any("malformed_level" in issue for issue in snapshot.yes.integrity_issues)
    assert snapshot.yes.asks[0].price == D("0.40")  # from the one valid NO bid


def test_orderbook_crossed_book_is_flagged(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXUNRATE-26OCT-T4.5")
    payload = {"orderbook_fp": {"yes_dollars": [["0.55", "1"]], "no_dollars": [["0.50", "1"]]}}
    snapshot = normalize_kalshi_orderbook(
        payload, market=market, received_at=NOW, data_source=DataSource.FIXTURE
    )
    assert any(issue.startswith("crossed_book") for issue in snapshot.yes.integrity_issues)


def test_orderbook_schema_mismatch_raises(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXUNRATE-26OCT-T4.5")
    with pytest.raises(NormalizationError):
        normalize_kalshi_orderbook(
            {"orderbook": {"yes": [[42, 10]]}}, market=market, received_at=NOW, data_source=DataSource.FIXTURE
        )


def test_top_of_book_fallback_is_depth_limited(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set, "KXUNRATE-26OCT-T4.5")
    raw = dict(fixture_set.kalshi_markets["KXUNRATE-26OCT-T4.5"])
    raw.update(
        yes_bid_dollars="0.3100", yes_bid_size_fp="400.00", yes_ask_dollars="0.3400", yes_ask_size_fp="300.00"
    )
    snapshot = normalize_kalshi_top_of_book(
        raw, market=market, received_at=NOW, data_source=DataSource.FIXTURE
    )
    assert snapshot.yes.depth_limited
    assert [(lv.price, lv.quantity) for lv in snapshot.yes.asks] == [(D("0.34"), D(300))]
    assert [(lv.price, lv.quantity) for lv in snapshot.no.asks] == [(D("0.69"), D(400))]


def test_complement_levels() -> None:
    derived = complement_levels(levels(("0.55", "10"), ("0.60", "5")))
    assert [(lv.price, lv.quantity) for lv in derived] == [(D("0.40"), D(5)), (D("0.45"), D(10))]
