"""Live adapters against mocked official-schema responses (respx). No network access."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from app.core.clock import FrozenClock
from app.core.http import (
    AsyncRateLimiter,
    ReadOnlyHttpTransport,
    ReadOnlyViolationError,
    RetryPolicy,
    VenueAuthRequiredError,
    VenueHttpError,
)
from app.core.metrics import METRICS
from app.domain.enums import DataSource, OutcomeSide
from app.venues.fixture import FixtureSet
from app.venues.kalshi.client import KalshiMarketDataVenue
from app.venues.kalshi.normalize import KalshiFeeDefaults
from app.venues.polymarket.client import PolymarketMarketDataVenue
from app.venues.polymarket.normalize import PolymarketFeeDefaults

D = Decimal
NOW = datetime(2026, 9, 25, 14, tzinfo=UTC)
KALSHI = "https://kalshi.test/trade-api/v2"
GAMMA = "https://gamma.test"
CLOB = "https://clob.test"


async def no_sleep(_: float) -> None:
    return None


def transport(
    name: str, base: str, *, attempts: int = 3, posts: frozenset[str] = frozenset()
) -> ReadOnlyHttpTransport:
    return ReadOnlyHttpTransport(
        name=name,
        base_url=base,
        connect_timeout=1,
        read_timeout=1,
        rate_per_second=1000,
        retry=RetryPolicy(max_attempts=attempts, base_delay=0.01, max_delay=0.05),
        readonly_post_paths=posts,
        clock=FrozenClock(NOW),
        sleep=no_sleep,
        rng=random.Random(7),
    )


def kalshi_venue(t: ReadOnlyHttpTransport) -> KalshiMarketDataVenue:
    return KalshiMarketDataVenue(
        t,
        clock=FrozenClock(NOW),
        data_source=DataSource.LIVE,
        fee_defaults=KalshiFeeDefaults(D("0.07"), D("0.0001"), D(2)),
    )


def poly_venue(gamma: ReadOnlyHttpTransport, clob: ReadOnlyHttpTransport) -> PolymarketMarketDataVenue:
    return PolymarketMarketDataVenue(
        gamma,
        clob,
        clock=FrozenClock(NOW),
        data_source=DataSource.LIVE,
        fee_defaults=PolymarketFeeDefaults(D("0.07"), D("0.00001")),
    )


# ---- transport ------------------------------------------------------------------------------


@respx.mock
async def test_retries_transient_errors_then_succeeds() -> None:
    route = respx.get(f"{KALSHI}/exchange/status").mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("boom"), httpx.Response(200, json={"ok": True})]
    )
    t = transport("kalshi", KALSHI)
    assert await t.get_json("/exchange/status") == {"ok": True}
    assert route.call_count == 3
    assert METRICS.counter("http_retries_total", venue="kalshi") == 2
    assert t.last_success_at == NOW


@respx.mock
async def test_retries_are_bounded() -> None:
    route = respx.get(f"{KALSHI}/markets").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "0"})
    )
    t = transport("kalshi", KALSHI, attempts=3)
    with pytest.raises(VenueHttpError, match="429"):
        await t.get_json("/markets")
    assert route.call_count == 3
    assert t.last_error is not None and "429" in t.last_error


@respx.mock
async def test_non_retriable_and_auth_errors() -> None:
    respx.get(f"{KALSHI}/markets/X").mock(return_value=httpx.Response(404))
    respx.get(f"{KALSHI}/markets/X/orderbook").mock(return_value=httpx.Response(401))
    t = transport("kalshi", KALSHI)
    with pytest.raises(VenueHttpError, match="404"):
        await t.get_json("/markets/X")
    with pytest.raises(VenueAuthRequiredError):
        await t.get_json("/markets/X/orderbook")


async def test_post_outside_allow_list_is_impossible() -> None:
    t = transport("polymarket-clob", CLOB, posts=frozenset({"/books"}))
    with pytest.raises(ReadOnlyViolationError):
        await t.post_json_readonly("/order", {"side": "BUY"})
    public = {name for name in dir(t) if not name.startswith("_")}
    assert public == {"aclose", "get_json", "last_error", "last_success_at", "name", "post_json_readonly"}


def test_retry_delay_is_capped_with_jitter() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=0.5, max_delay=2.0)
    rng = random.Random(1)
    for attempt in range(2, 10):
        assert 0 <= policy.delay(attempt, rng, None) <= 2.0
    assert policy.delay(2, rng, retry_after=30) == 2.0


async def test_rate_limiter_spaces_requests() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = AsyncRateLimiter(2, monotonic=lambda: now[0], sleep=sleep)
    for _ in range(3):
        await limiter.acquire()
    assert sum(slept) == pytest.approx(1.0)


# ---- Kalshi ----------------------------------------------------------------------------------


@respx.mock
async def test_kalshi_pagination_enrichment_and_books(fixture_set: FixtureSet) -> None:
    events = fixture_set.kalshi_events
    first, second = events[:2], events[2:]
    listing = respx.get(f"{KALSHI}/events").mock(
        side_effect=[
            httpx.Response(200, json={"events": first, "cursor": "page2"}),
            httpx.Response(200, json={"events": second, "cursor": ""}),
        ]
    )
    raw = fixture_set.kalshi_markets["KXFEDDECISION-26DEC-C25"]
    respx.get(f"{KALSHI}/markets/KXFEDDECISION-26DEC-C25").mock(
        return_value=httpx.Response(200, json={"market": raw})
    )
    respx.get(f"{KALSHI}/series/KXFEDDECISION").mock(
        return_value=httpx.Response(200, json={"series": fixture_set.kalshi_series["KXFEDDECISION"]})
    )
    respx.get(f"{KALSHI}/markets/KXFEDDECISION-26DEC-C25/orderbook").mock(
        return_value=httpx.Response(200, json=fixture_set.kalshi_orderbooks["KXFEDDECISION-26DEC-C25"])
    )
    venue = kalshi_venue(transport("kalshi", KALSHI))
    markets = await venue.list_active_markets()
    assert listing.call_count == 2
    assert listing.calls[1].request.url.params["cursor"] == "page2"
    assert listing.calls[0].request.url.params["with_nested_markets"] == "true"
    assert len(markets) == 15
    target = next(m for m in markets if m.venue_market_id == "KXFEDDECISION-26DEC-C25")
    assert target.fee_metadata.source == "fallback_assumption"  # series not fetched during listing
    enriched = (await venue.enrich_markets([target]))[0]
    assert enriched.fee_metadata.source == "documented_metadata"
    snapshot = await venue.get_order_book(enriched)
    assert snapshot.yes.asks[0].price == D("0.40") and snapshot.yes.data_source is DataSource.LIVE


@respx.mock
async def test_kalshi_orderbook_auth_falls_back_to_top_of_book(fixture_set: FixtureSet) -> None:
    raw = dict(fixture_set.kalshi_markets["KXUNRATE-26OCT-T4.5"])
    raw.update(
        yes_bid_dollars="0.3100", yes_bid_size_fp="400.00", yes_ask_dollars="0.3400", yes_ask_size_fp="300.00"
    )
    event = next(e for e in fixture_set.kalshi_events if e["event_ticker"] == raw["event_ticker"])
    respx.get(f"{KALSHI}/markets/{raw['ticker']}").mock(
        return_value=httpx.Response(200, json={"market": raw})
    )
    respx.get(f"{KALSHI}/events/{raw['event_ticker']}").mock(
        return_value=httpx.Response(200, json={"event": {k: v for k, v in event.items() if k != "markets"}})
    )
    respx.get(f"{KALSHI}/series/KXUNRATE").mock(return_value=httpx.Response(404))
    orderbook = respx.get(f"{KALSHI}/markets/{raw['ticker']}/orderbook").mock(
        return_value=httpx.Response(401)
    )
    venue = kalshi_venue(transport("kalshi", KALSHI))
    market = await venue.get_market(raw["ticker"])
    first = await venue.get_order_book(market)
    second = await venue.get_order_book(market)
    assert first.yes.depth_limited and second.yes.depth_limited
    assert orderbook.call_count == 1  # remembers that depth requires credentials
    assert first.yes.asks[0].price == D("0.34")


@respx.mock
async def test_kalshi_healthcheck() -> None:
    respx.get(f"{KALSHI}/exchange/status").mock(
        return_value=httpx.Response(200, json={"exchange_active": True, "trading_active": False})
    )
    health = await kalshi_venue(transport("kalshi", KALSHI)).healthcheck()
    assert health.reachable and health.trading_active is False


# ---- Polymarket -------------------------------------------------------------------------------


@respx.mock
async def test_polymarket_keyset_pagination_and_batch_books(fixture_set: FixtureSet) -> None:
    markets = fixture_set.polymarket_markets
    pages = [markets[:100], markets[100:]]
    route = respx.get(f"{GAMMA}/markets/keyset").mock(
        side_effect=[
            httpx.Response(200, json={"markets": pages[0], "next_cursor": "abc"}),
        ]
    )
    books_route = respx.post(f"{CLOB}/books").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=[
                fixture_set.polymarket_books[item["token_id"]]
                for item in json.loads(request.content)
                if item["token_id"] in fixture_set.polymarket_books
            ],
        )
    )
    venue = poly_venue(transport("gamma", GAMMA), transport("clob", CLOB, posts=frozenset({"/books"})))
    listed = await venue.list_active_markets(max_pages=5)
    assert route.call_count == 1  # fewer than 100 results: last page
    assert len(listed) == len(markets)
    fed = next(m for m in listed if m.venue_market_id == "610001")
    snapshot = await venue.get_order_book(fed)
    assert books_route.call_count == 1
    assert snapshot.no.asks[0].price == D("0.52")
    assert snapshot.yes.outcome_side is OutcomeSide.YES


@respx.mock
async def test_polymarket_follows_next_cursor() -> None:
    def market(index: int) -> dict[str, Any]:
        return {
            "id": str(index),
            "question": f"Q{index}?",
            "outcomes": '["Yes","No"]',
            "clobTokenIds": f'["{index}1","{index}2"]',
            "active": True,
            "closed": False,
        }

    route = respx.get(f"{GAMMA}/markets/keyset").mock(
        side_effect=[
            httpx.Response(200, json={"markets": [market(i) for i in range(100)], "next_cursor": "c2"}),
            httpx.Response(200, json={"markets": [market(i) for i in range(100, 130)]}),
        ]
    )
    venue = poly_venue(transport("gamma", GAMMA), transport("clob", CLOB))
    listed = await venue.list_active_markets()
    assert len(listed) == 130
    assert route.calls[1].request.url.params["after_cursor"] == "c2"
    assert "offset" not in route.calls[0].request.url.params


@respx.mock
async def test_polymarket_batch_failure_falls_back_to_single_books(fixture_set: FixtureSet) -> None:
    raw = next(m for m in fixture_set.polymarket_markets if m["id"] == "610002")
    respx.get(f"{GAMMA}/markets/610002").mock(return_value=httpx.Response(200, json=raw))
    respx.post(f"{CLOB}/books").mock(return_value=httpx.Response(400))
    single = respx.get(f"{CLOB}/book").mock(
        side_effect=lambda request: httpx.Response(
            200, json=fixture_set.polymarket_books[request.url.params["token_id"]]
        )
    )
    venue = poly_venue(transport("gamma", GAMMA), transport("clob", CLOB, posts=frozenset({"/books"})))
    market = await venue.get_market("610002")
    snapshot = await venue.get_order_book(market)
    assert single.call_count == 2
    assert snapshot.yes.asks[0].price == D("0.33")
    assert METRICS.counter("book_batch_fallbacks_total", venue="polymarket") == 1


@respx.mock
async def test_polymarket_unreachable_healthcheck() -> None:
    respx.get(f"{GAMMA}/markets/keyset").mock(return_value=httpx.Response(403))
    health = await poly_venue(transport("gamma", GAMMA), transport("clob", CLOB)).healthcheck()
    assert not health.reachable and "403" in (health.last_error or "")
