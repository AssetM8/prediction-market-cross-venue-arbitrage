from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.enums import DataSource, MarketStatus, OutcomeSide
from app.venues.common import NormalizationError
from app.venues.fixture import FixtureSet
from app.venues.polymarket.normalize import (
    PolymarketFeeDefaults,
    normalize_polymarket_book,
    normalize_polymarket_market,
)

D = Decimal
NOW = datetime(2026, 9, 25, 14, tzinfo=UTC)
DEFAULTS = PolymarketFeeDefaults(fallback_rate=D("0.07"), rounding_quantum=D("0.00001"))


def _raw(fixture_set: FixtureSet, market_id: str) -> dict:  # type: ignore[type-arg]
    return dict(next(m for m in fixture_set.polymarket_markets if m["id"] == market_id))


def test_market_maps_tokens_to_outcomes(fixture_set: FixtureSet) -> None:
    raw = _raw(fixture_set, "610001")
    market = normalize_polymarket_market(raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS)
    tokens = json.loads(raw["clobTokenIds"])
    assert market.market_type == "binary"
    assert market.outcome(OutcomeSide.YES).token_id == tokens[0]
    assert market.outcome(OutcomeSide.NO).token_id == tokens[1]
    assert market.extra["condition_id"] == raw["conditionId"]
    assert market.event_title == "Fed decision in December 2026"
    assert market.fee_metadata.rate == D("0.05")
    assert market.fee_metadata.source == "documented_metadata"
    assert market.tick_size == D("0.01")
    assert market.status is MarketStatus.ACTIVE


def test_outcome_order_is_not_assumed(fixture_set: FixtureSet) -> None:
    raw = _raw(fixture_set, "610001")
    tokens = json.loads(raw["clobTokenIds"])
    raw["outcomes"] = json.dumps(["No", "Yes"])
    market = normalize_polymarket_market(raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS)
    assert market.outcome(OutcomeSide.YES).token_id == tokens[1]
    assert market.outcome(OutcomeSide.NO).token_id == tokens[0]


def test_non_binary_market_has_no_outcomes(fixture_set: FixtureSet) -> None:
    market = normalize_polymarket_market(
        _raw(fixture_set, "610011"), data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )
    assert market.market_type == "non_binary"
    assert market.outcomes == ()


def test_fee_metadata_variants(fixture_set: FixtureSet) -> None:
    raw = _raw(fixture_set, "610001")
    raw["feesEnabled"] = False
    assert (
        normalize_polymarket_market(
            raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
        ).fee_metadata.rate
        == 0
    )
    raw["feesEnabled"] = True
    raw["feeSchedule"] = {"rate": 9, "exponent": 1}
    implausible = normalize_polymarket_market(
        raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    ).fee_metadata
    assert implausible.source == "fallback_assumption" and implausible.rate == D("0.07")
    missing = normalize_polymarket_market(
        _raw(fixture_set, "610010"), data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )
    assert missing.fee_metadata.source == "fallback_assumption"


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"closed": True}, MarketStatus.CLOSED),
        ({"closed": True, "umaResolutionStatus": "resolved"}, MarketStatus.SETTLED),
        ({"active": False}, MarketStatus.NOT_OPEN),
        ({"acceptingOrders": False}, MarketStatus.PAUSED),
        ({"archived": True}, MarketStatus.SETTLED),
    ],
)
def test_status_mapping(fixture_set: FixtureSet, patch: dict, expected: MarketStatus) -> None:  # type: ignore[type-arg]
    raw = _raw(fixture_set, "610002") | patch
    assert (
        normalize_polymarket_market(raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS).status
        is expected
    )


def test_book_is_resorted_regardless_of_wire_order(fixture_set: FixtureSet) -> None:
    raw = _raw(fixture_set, "610001")
    market = normalize_polymarket_market(raw, data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS)
    token = market.outcome(OutcomeSide.NO).token_id
    payload = fixture_set.polymarket_books[str(token)]
    # fixture wire order: bids ascending, asks descending (best last)
    assert payload["asks"][0]["price"] == "0.57"
    book = normalize_polymarket_book(
        payload, market=market, side=OutcomeSide.NO, received_at=NOW, data_source=DataSource.FIXTURE
    )
    assert [lv.price for lv in book.asks] == [D("0.52"), D("0.54"), D("0.57")]
    assert [lv.price for lv in book.bids] == [D("0.48"), D("0.46")]
    assert book.source_timestamp == datetime(2026, 9, 25, 13, 59, 58, tzinfo=UTC)
    assert book.min_order_size == D(5)
    assert not book.derived_asks


def test_book_asset_mismatch_raises(fixture_set: FixtureSet) -> None:
    market = normalize_polymarket_market(
        _raw(fixture_set, "610001"), data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )
    with pytest.raises(NormalizationError):
        normalize_polymarket_book(
            {"asset_id": "123", "bids": [], "asks": []},
            market=market,
            side=OutcomeSide.YES,
            received_at=NOW,
            data_source=DataSource.FIXTURE,
        )


def test_book_crossed_and_malformed(fixture_set: FixtureSet) -> None:
    market = normalize_polymarket_market(
        _raw(fixture_set, "610001"), data_source=DataSource.FIXTURE, fee_defaults=DEFAULTS
    )
    token = market.outcome(OutcomeSide.YES).token_id
    payload = {
        "asset_id": token,
        "bids": [{"price": "0.60", "size": "10"}, {"price": "0", "size": "5"}],
        "asks": [{"price": "0.55", "size": "10"}, {"size": "3"}],
        "timestamp": "1790344798000",
    }
    book = normalize_polymarket_book(
        payload, market=market, side=OutcomeSide.YES, received_at=NOW, data_source=DataSource.FIXTURE
    )
    kinds = {issue.split(":")[0] for issue in book.integrity_issues}
    assert {"crossed_book", "malformed_level"} <= kinds
