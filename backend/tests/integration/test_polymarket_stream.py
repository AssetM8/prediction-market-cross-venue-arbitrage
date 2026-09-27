"""Polymarket WebSocket message handling and REST polling fallback (no network)."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

from app.core.clock import FrozenClock
from app.core.metrics import METRICS
from app.domain.enums import DataSource, OutcomeSide
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import NormalizedMarket
from app.venues.fixture import FixtureSet, FixtureTransport
from app.venues.polymarket.client import PolymarketMarketDataVenue
from app.venues.polymarket.normalize import PolymarketFeeDefaults, normalize_polymarket_market
from app.venues.polymarket.stream import PolymarketBookStream
from tests.conftest import NOW

D = Decimal


def _market(fixture_set: FixtureSet) -> NormalizedMarket:
    raw = next(m for m in fixture_set.polymarket_markets if m["id"] == "610001")
    return normalize_polymarket_market(
        raw, data_source=DataSource.LIVE, fee_defaults=PolymarketFeeDefaults(D("0.07"), D("0.00001"))
    )


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


def _stream(
    market: NormalizedMarket, *, ws_url: str | None, connect: Any = None, rest: Any = None
) -> PolymarketBookStream:
    async def default_rest(markets: Any) -> dict[str, BinaryBookSnapshot]:
        return {}

    kwargs: dict[str, Any] = {}
    if connect is not None:
        kwargs["connect"] = connect
    return PolymarketBookStream(
        ws_url=ws_url,
        markets=[market],
        clock=FrozenClock(NOW),
        data_source=DataSource.LIVE,
        rest_fetch=rest or default_rest,
        poll_interval_seconds=0.01,
        max_ws_failures=2,
        sleep=no_sleep,
        **kwargs,
    )


def test_book_and_price_change_messages_update_local_state(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    yes = market.outcome(OutcomeSide.YES).token_id
    no = market.outcome(OutcomeSide.NO).token_id
    stream = _stream(market, ws_url=None)
    touched = stream.apply_message(
        [
            {
                "event_type": "book",
                "asset_id": yes,
                "market": "c",
                "timestamp": "1790344798000",
                "hash": "h1",
                "bids": [{"price": "0.47", "size": "150"}],
                "asks": [{"price": "0.51", "size": "200"}],
            },
            {
                "event_type": "book",
                "asset_id": no,
                "market": "c",
                "timestamp": "1790344798000",
                "hash": "h2",
                "bids": [{"price": "0.48", "size": "150"}],
                "asks": [{"price": "0.52", "size": "120"}],
            },
        ]
    )
    assert touched == {"610001"}
    stream.apply_message(
        {
            "event_type": "price_change",
            "market": "c",
            "timestamp": "1790344799000",
            "price_changes": [
                {"asset_id": no, "price": "0.52", "size": "0", "side": "SELL", "hash": "h3"},  # level removed
                {"asset_id": no, "price": "0.53", "size": "80", "side": "SELL", "hash": "h3"},
                {"asset_id": yes, "price": "0.49", "size": "10", "side": "BUY", "hash": "h4"},
            ],
        }
    )
    stream.apply_message(
        {"event_type": "tick_size_change", "asset_id": yes, "old_tick_size": "0.01", "new_tick_size": "0.001"}
    )
    snapshot = stream.snapshot("610001", NOW)
    assert snapshot is not None
    assert [(lv.price, lv.quantity) for lv in snapshot.no.asks] == [(D("0.53"), D(80))]
    assert snapshot.yes.bids[0].price == D("0.49")
    assert snapshot.yes.tick_size == D("0.001")


def test_price_change_before_snapshot_is_ignored(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    no = market.outcome(OutcomeSide.NO).token_id
    stream = _stream(market, ws_url=None)
    assert (
        stream.apply_message(
            {
                "event_type": "price_change",
                "price_changes": [{"asset_id": no, "price": "0.5", "size": "1", "side": "BUY"}],
            }
        )
        == set()
    )
    assert stream.snapshot("610001", NOW) is None


async def test_websocket_failure_falls_back_to_rest_polling(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    calls = {"connect": 0, "rest": 0}

    def failing_connect(*_: Any, **__: Any) -> Any:
        calls["connect"] += 1
        raise OSError("connection refused")

    clock = FrozenClock(NOW)
    venue = PolymarketMarketDataVenue(
        FixtureTransport("gamma", fixture_set.gamma_handler, now=clock.now),
        FixtureTransport(
            "clob", fixture_set.clob_handler, readonly_post_paths=frozenset({"/books"}), now=clock.now
        ),
        clock=clock,
        data_source=DataSource.FIXTURE,
        fee_defaults=PolymarketFeeDefaults(D("0.07"), D("0.00001")),
    )

    async def rest(markets: Any) -> dict[str, BinaryBookSnapshot]:
        calls["rest"] += 1
        return await venue.get_order_books(markets)

    stream = _stream(market, ws_url="wss://example.invalid/ws/market", connect=failing_connect, rest=rest)
    generator = stream.run()
    snapshot = await asyncio.wait_for(generator.__anext__(), timeout=5)
    await generator.aclose()
    assert calls["connect"] == 2 and calls["rest"] == 1
    assert stream.mode == "rest_polling"
    assert snapshot.market_id == "610001"
    assert METRICS.counter("ws_fallback_to_rest_total", venue="polymarket") == 1


class FakeSocket:
    def __init__(self, messages: list[str]) -> None:
        self.sent: list[str] = []
        self._messages = messages

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            await asyncio.sleep(3600)
        return self._messages.pop(0)


async def test_websocket_subscription_and_snapshots(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    yes = market.outcome(OutcomeSide.YES).token_id
    no = market.outcome(OutcomeSide.NO).token_id
    socket = FakeSocket(
        [
            json.dumps(
                [
                    {
                        "event_type": "book",
                        "asset_id": yes,
                        "bids": [],
                        "asks": [{"price": "0.51", "size": "5"}],
                    },
                    {
                        "event_type": "book",
                        "asset_id": no,
                        "bids": [],
                        "asks": [{"price": "0.52", "size": "7"}],
                    },
                ]
            ),
            "PONG",
        ]
    )
    stream = _stream(market, ws_url="wss://example.invalid/ws/market", connect=lambda *_a, **_k: socket)
    generator = stream.run()
    snapshot = await asyncio.wait_for(generator.__anext__(), timeout=5)
    await generator.aclose()
    subscription = json.loads(socket.sent[0])
    assert subscription["type"] == "market" and set(subscription["assets_ids"]) == {yes, no}
    assert snapshot.no.asks[0].quantity == D(7)
    assert stream.mode == "websocket"


def _seed(stream: PolymarketBookStream, yes: str, no: str) -> None:
    stream.apply_message(
        [
            {
                "event_type": "book",
                "asset_id": yes,
                "bids": [{"price": "0.47", "size": "150"}],
                "asks": [{"price": "0.51", "size": "200"}],
            },
            {
                "event_type": "book",
                "asset_id": no,
                "bids": [{"price": "0.48", "size": "150"}],
                "asks": [{"price": "0.52", "size": "120"}],
            },
        ]
    )


def test_envelope_wire_shape_is_accepted(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    yes = market.outcome(OutcomeSide.YES).token_id
    no = market.outcome(OutcomeSide.NO).token_id
    assert yes is not None and no is not None
    stream = _stream(market, ws_url=None)
    _seed(stream, yes, no)
    touched = stream.apply_message(
        {
            "topic": "market",
            "type": "price_change",
            "payload": {
                "market": "c",
                "priceChanges": [
                    {
                        "tokenId": yes,
                        "price": "0.49",
                        "size": "25",
                        "side": "BUY",
                        "bestBid": "0.49",
                        "bestAsk": "0.51",
                    }
                ],
                "timestamp": "1790344799000",
            },
        }
    )
    assert touched == {"610001"}
    assert not stream.desynced
    snapshot = stream.snapshot("610001", NOW)
    assert snapshot is not None and snapshot.yes.bids[0].price == D("0.49")


def test_best_price_disagreement_marks_book_desynced(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    yes = market.outcome(OutcomeSide.YES).token_id
    no = market.outcome(OutcomeSide.NO).token_id
    assert yes is not None and no is not None
    stream = _stream(market, ws_url=None)
    _seed(stream, yes, no)
    before = METRICS.counter("ws_book_desync_total", venue="polymarket")
    # The venue says the best ask is 0.50, but applying the change locally leaves 0.51 best.
    stream.apply_message(
        {
            "event_type": "price_change",
            "price_changes": [
                {
                    "asset_id": yes,
                    "price": "0.55",
                    "size": "10",
                    "side": "SELL",
                    "best_bid": "0.47",
                    "best_ask": "0.50",
                }
            ],
        }
    )
    assert stream.desynced == {yes}
    assert stream.snapshot("610001", NOW) is None  # never serve a book we know is wrong
    assert METRICS.counter("ws_book_desync_total", venue="polymarket") == before + 1


async def test_desynced_book_is_reseeded_from_rest(fixture_set: FixtureSet) -> None:
    market = _market(fixture_set)
    yes = market.outcome(OutcomeSide.YES).token_id
    no = market.outcome(OutcomeSide.NO).token_id
    assert yes is not None and no is not None
    clock = FrozenClock(NOW)
    venue = PolymarketMarketDataVenue(
        FixtureTransport("gamma", fixture_set.gamma_handler, now=clock.now),
        FixtureTransport(
            "clob", fixture_set.clob_handler, readonly_post_paths=frozenset({"/books"}), now=clock.now
        ),
        clock=clock,
        data_source=DataSource.FIXTURE,
        fee_defaults=PolymarketFeeDefaults(D("0.07"), D("0.00001")),
    )
    rest_books = await venue.get_order_books([market])

    async def rest(markets: Any) -> dict[str, BinaryBookSnapshot]:
        return rest_books

    stream = _stream(market, ws_url=None, rest=rest)
    _seed(stream, yes, no)
    stream.apply_message(
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": no, "price": "0.52", "size": "5", "side": "SELL", "best_ask": "0.60"}
            ],
        }
    )
    assert stream.desynced == {no}
    assert await stream.resync() == {"610001"}
    assert not stream.desynced
    snapshot = stream.snapshot("610001", NOW)
    assert snapshot is not None
    assert snapshot.no.asks == rest_books["610001"].no.asks
