"""Polymarket public market WebSocket client with REST polling fallback.

Protocol (official docs, market channel): connect to
``wss://ws-subscriptions-clob.polymarket.com/ws/market``, send
``{"assets_ids": [...], "type": "market"}``, send the text frame ``PING`` every 10 s.
Messages: ``book`` (full snapshot), ``price_change``, ``tick_size_change`` and
``last_trade_price``. The server may send a JSON array of events.

Two wire shapes appear in the official docs: the classic one (``event_type``,
``price_changes[]``, ``asset_id``, ``best_bid``/``best_ask``) and an envelope form
(``type`` + ``payload`` with ``priceChanges[]``, ``tokenId``, ``bestBid``/``bestAsk``). Both
are accepted. A ``price_change`` entry is applied as the new aggregate size at that level
(0 removes it; ``side`` BUY = bid, SELL = ask). The docs are not explicit about that
semantic, so every entry that carries the venue's best bid/ask is cross-checked against the
local book; on any disagreement the token is marked desynchronized, produces no snapshots,
and is re-seeded from a REST snapshot. A wrong interpretation therefore degrades to REST
data rather than to a wrong book.

Reconnects use bounded exponential backoff. After ``max_ws_failures`` consecutive
failures the stream switches to REST polling for the rest of its life, so callers always
receive data when REST is reachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from app.core.clock import Clock
from app.core.decimal_utils import DecimalParseError, parse_decimal
from app.core.http import VenueHttpError
from app.core.ids import content_hash
from app.core.logging import get_logger, log_event
from app.core.metrics import METRICS
from app.domain.enums import DataSource, OutcomeSide
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import NormalizedMarket
from app.venues.common import NormalizationError
from app.venues.polymarket.normalize import normalize_polymarket_book

logger = get_logger(__name__)

PING_INTERVAL_SECONDS = 10.0
RECONNECT_BASE_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 30.0

RestFetch = Callable[[Sequence[NormalizedMarket]], Awaitable[dict[str, BinaryBookSnapshot]]]


class LocalBook:
    """Level-2 state for one token maintained from WebSocket messages."""

    def __init__(self) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.timestamp: str | None = None
        self.hash: str | None = None
        self.tick_size: str | None = None
        self.initialized = False

    def replace(self, bids: list[dict[str, Any]], asks: list[dict[str, Any]]) -> None:
        self.bids = _levels(bids)
        self.asks = _levels(asks)
        self.initialized = True

    def update(self, side: str, price: Decimal, size: Decimal) -> None:
        target = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            target.pop(price, None)
        else:
            target[price] = size

    def payload(self, asset_id: str) -> dict[str, Any]:
        return {
            "asset_id": asset_id,
            "bids": [{"price": str(p), "size": str(s)} for p, s in self.bids.items()],
            "asks": [{"price": str(p), "size": str(s)} for p, s in self.asks.items()],
            "timestamp": self.timestamp,
            "hash": self.hash,
            "tick_size": self.tick_size,
        }


def _unwrap(event: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Return ``(kind, body)`` for either documented wire shape."""
    payload = event.get("payload")
    if isinstance(payload, dict):
        body = {**payload, **{k: v for k, v in event.items() if k != "payload"}}
        return event.get("event_type") or event.get("type"), body
    return event.get("event_type") or event.get("type"), event


def _asset_id(body: dict[str, Any]) -> str:
    for key in ("asset_id", "assetId", "tokenId", "token_id"):
        value = body.get(key)
        if value:
            return str(value)
    return ""


def _agrees(raw_expected: Any, levels: dict[Decimal, Decimal], *, best: Callable[..., Decimal]) -> bool:
    """True unless the venue's reported best price contradicts the local book.

    Only strictly-inside prices are compared; the encoding of an empty side is undocumented.
    """
    if raw_expected in (None, ""):
        return True
    try:
        expected = parse_decimal(raw_expected, field="best")
    except DecimalParseError:
        return True
    if not Decimal(0) < expected < Decimal(1):
        return True
    return bool(levels) and best(levels) == expected


def _levels(raw: list[dict[str, Any]]) -> dict[Decimal, Decimal]:
    levels: dict[Decimal, Decimal] = {}
    for item in raw:
        try:
            price = parse_decimal(item.get("price"), field="price")
            size = parse_decimal(item.get("size"), field="size")
        except (DecimalParseError, AttributeError):
            continue
        if size > 0:
            levels[price] = size
    return levels


class PolymarketBookStream:
    """Yields :class:`BinaryBookSnapshot` s for the given markets as books change."""

    def __init__(
        self,
        *,
        ws_url: str | None,
        markets: list[NormalizedMarket],
        clock: Clock,
        data_source: DataSource,
        rest_fetch: RestFetch,
        poll_interval_seconds: float,
        max_ws_failures: int = 3,
        connect: Callable[..., Any] = ws_connect,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ws_url = ws_url
        self._markets = {m.venue_market_id: m for m in markets}
        self._clock = clock
        self._data_source = data_source
        self._rest_fetch = rest_fetch
        self._poll_interval = poll_interval_seconds
        self._max_ws_failures = max_ws_failures
        self._connect = connect
        self._sleep = sleep
        self._token_to_market: dict[str, tuple[str, OutcomeSide]] = {}
        for market in markets:
            for outcome in market.outcomes:
                if outcome.token_id:
                    self._token_to_market[outcome.token_id] = (market.venue_market_id, outcome.side)
        self._books: dict[str, LocalBook] = {token: LocalBook() for token in self._token_to_market}
        self.desynced: set[str] = set()
        self.mode = "websocket" if ws_url else "rest_polling"

    # ---- message handling (pure, unit-tested) -----------------------------------------

    def apply_message(self, message: Any) -> set[str]:
        """Apply one decoded WS message (dict or list of dicts); return touched market ids."""
        events = message if isinstance(message, list) else [message]
        touched: set[str] = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            kind, body = _unwrap(event)
            if kind == "book":
                asset = _asset_id(body)
                if asset in self._books:
                    book = self._books[asset]
                    book.replace(list(body.get("bids") or []), list(body.get("asks") or []))
                    book.timestamp = str(body.get("timestamp") or "") or None
                    book.hash = str(body.get("hash") or "") or None
                    self.desynced.discard(asset)
                    touched.add(self._token_to_market[asset][0])
            elif kind == "price_change":
                touched |= self._apply_price_changes(body)
            elif kind == "tick_size_change":
                asset = _asset_id(body)
                if asset in self._books:
                    tick = body.get("new_tick_size") or body.get("newTickSize")
                    self._books[asset].tick_size = str(tick or "") or None
                    touched.add(self._token_to_market[asset][0])
        return touched

    def _apply_price_changes(self, body: dict[str, Any]) -> set[str]:
        touched: set[str] = set()
        expected: dict[str, tuple[Any, Any]] = {}
        for change in body.get("price_changes") or body.get("priceChanges") or []:
            if not isinstance(change, dict):
                continue
            asset = _asset_id(change)
            if asset not in self._books or not self._books[asset].initialized:
                continue
            try:
                price = parse_decimal(change.get("price"), field="price")
                size = parse_decimal(change.get("size"), field="size")
            except DecimalParseError:
                self._mark_desynced(asset, "malformed price_change")
                continue
            book = self._books[asset]
            book.update(str(change.get("side") or ""), price, size)
            book.timestamp = str(body.get("timestamp") or book.timestamp or "") or None
            book.hash = str(change.get("hash") or "") or None
            expected[asset] = (
                change.get("best_bid", change.get("bestBid")),
                change.get("best_ask", change.get("bestAsk")),
            )
            touched.add(self._token_to_market[asset][0])
        for asset, (best_bid, best_ask) in expected.items():
            book = self._books[asset]
            if not (_agrees(best_bid, book.bids, best=max) and _agrees(best_ask, book.asks, best=min)):
                self._mark_desynced(asset, "best bid/ask disagrees with local book")
        return touched

    def _mark_desynced(self, asset: str, reason: str) -> None:
        book = self._books[asset]
        book.initialized = False
        self.desynced.add(asset)
        METRICS.inc("ws_book_desync_total", venue="polymarket")
        log_event(
            logger,
            logging.WARNING,
            "polymarket local book desynchronized; will re-seed from REST",
            market_id=self._token_to_market[asset][0],
            reason=reason,
        )

    async def resync(self) -> set[str]:
        """Re-seed desynchronized tokens from a REST snapshot; return re-seeded market ids."""
        if not self.desynced:
            return set()
        market_ids = sorted({self._token_to_market[token][0] for token in self.desynced})
        try:
            snapshots = await self._rest_fetch([self._markets[m] for m in market_ids])
        except VenueHttpError as exc:
            log_event(logger, logging.WARNING, "polymarket REST resync failed", error=str(exc))
            return set()
        reseeded: set[str] = set()
        for market_id, snapshot in snapshots.items():
            market = self._markets.get(market_id)
            if market is None:
                continue
            for outcome in market.outcomes:
                token = outcome.token_id
                if token is None or token not in self._books:
                    continue
                normalized = snapshot.yes if outcome.side is OutcomeSide.YES else snapshot.no
                book = self._books[token]
                book.bids = {level.price: level.quantity for level in normalized.bids}
                book.asks = {level.price: level.quantity for level in normalized.asks}
                book.timestamp = normalized.sequence_or_timestamp
                book.hash = normalized.checksum_or_source_hash
                book.initialized = True
                self.desynced.discard(token)
            reseeded.add(market_id)
        return reseeded

    def snapshot(self, market_id: str, received_at: datetime) -> BinaryBookSnapshot | None:
        market = self._markets[market_id]
        books = {}
        for outcome in market.outcomes:
            token = outcome.token_id
            if token is None or not self._books[token].initialized:
                return None
            payload = self._books[token].payload(token)
            if payload["hash"] is None:
                payload["hash"] = content_hash(payload)
            try:
                books[outcome.side] = normalize_polymarket_book(
                    payload,
                    market=market,
                    side=outcome.side,
                    received_at=received_at,
                    data_source=self._data_source,
                )
            except NormalizationError:
                return None
        return BinaryBookSnapshot(market_id=market_id, yes=books[OutcomeSide.YES], no=books[OutcomeSide.NO])

    # ---- transports -------------------------------------------------------------------

    async def run(self) -> AsyncGenerator[BinaryBookSnapshot, None]:
        failures = 0
        while self._ws_url and failures < self._max_ws_failures:
            try:
                async for snapshot in self._run_websocket():
                    failures = 0
                    yield snapshot
            except (OSError, WebSocketException, TimeoutError, json.JSONDecodeError) as exc:
                failures += 1
                METRICS.inc("ws_failures_total", venue="polymarket")
                delay = min(RECONNECT_MAX_SECONDS, RECONNECT_BASE_SECONDS * (2 ** (failures - 1)))
                log_event(
                    logger,
                    logging.WARNING,
                    "polymarket websocket failed",
                    error=f"{type(exc).__name__}: {exc}",
                    failures=failures,
                    retry_in=delay,
                )
                if failures < self._max_ws_failures:
                    await self._sleep(delay)
        self.mode = "rest_polling"
        METRICS.inc("ws_fallback_to_rest_total", venue="polymarket")
        async for snapshot in self._run_polling():
            yield snapshot

    async def _run_websocket(self) -> AsyncIterator[BinaryBookSnapshot]:
        async with self._connect(self._ws_url, open_timeout=10, ping_interval=None) as ws:
            await ws.send(
                json.dumps({"assets_ids": list(self._books), "type": "market", "initial_dump": True})
            )
            self.mode = "websocket"
            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                async for raw in ws:
                    if raw == "PONG" or not raw:
                        continue
                    touched = self.apply_message(json.loads(raw))
                    if self.desynced:
                        touched |= await self.resync()
                    now = self._clock.now()
                    for market_id in sorted(touched):
                        snapshot = self.snapshot(market_id, now)
                        if snapshot is not None:
                            yield snapshot
            finally:
                heartbeat.cancel()
        raise WebSocketException("market websocket closed by server")

    async def _heartbeat(self, ws: Any) -> None:
        while True:
            await self._sleep(PING_INTERVAL_SECONDS)
            await ws.send("PING")

    async def _run_polling(self) -> AsyncIterator[BinaryBookSnapshot]:
        last: dict[str, str] = {}
        while True:
            try:
                books = await self._rest_fetch(list(self._markets.values()))
            except VenueHttpError as exc:
                log_event(logger, logging.WARNING, "polymarket REST poll failed", error=str(exc))
                books = {}
            for market_id, snapshot in sorted(books.items()):
                digest = snapshot.yes.checksum_or_source_hash + snapshot.no.checksum_or_source_hash
                if last.get(market_id) != digest:
                    last[market_id] = digest
                    yield snapshot
            await self._sleep(self._poll_interval)
