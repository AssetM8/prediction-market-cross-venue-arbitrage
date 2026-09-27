"""Read-only Polymarket market-data adapter (Gamma metadata + CLOB books + public WS).

Endpoints used (public; no credentials, no signing):

* Gamma ``GET /markets/keyset`` (cursor pagination via ``after_cursor``/``next_cursor``,
  ``limit`` <= 100) and ``GET /markets/{id}``.
* CLOB ``POST /books`` (read-only batch, body ``[{"token_id": ...}]``) with
  ``GET /book?token_id=`` as a per-token fallback.
* Market WebSocket ``wss://ws-subscriptions-clob.polymarket.com/ws/market`` for streaming,
  with REST polling as fallback (see :mod:`app.venues.polymarket.stream`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.core.clock import Clock
from app.core.http import Transport, VenueHttpError
from app.core.logging import get_logger, log_event
from app.core.metrics import METRICS
from app.domain.enums import DataSource, OutcomeSide, Venue
from app.domain.interfaces import BinaryBookSnapshot, VenueHealth
from app.domain.models import NormalizedMarket, NormalizedOrderBook
from app.venues.common import NormalizationError
from app.venues.polymarket.normalize import (
    PolymarketFeeDefaults,
    normalize_polymarket_book,
    normalize_polymarket_market,
)
from app.venues.polymarket.stream import PolymarketBookStream

logger = get_logger(__name__)

KEYSET_PAGE_LIMIT = 100  # documented maximum since 2026-05-14
BOOKS_BATCH_LIMIT = 500  # documented maximum for POST /books


class PolymarketMarketDataVenue:
    """Implements :class:`app.domain.interfaces.MarketDataVenue` for Polymarket."""

    def __init__(
        self,
        gamma: Transport,
        clob: Transport,
        *,
        clock: Clock,
        data_source: DataSource,
        fee_defaults: PolymarketFeeDefaults,
        ws_url: str | None = None,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._gamma = gamma
        self._clob = clob
        self._clock = clock
        self._data_source = data_source
        self._fee_defaults = fee_defaults
        self._ws_url = ws_url
        self._poll_interval = poll_interval_seconds

    @property
    def venue(self) -> Venue:
        return Venue.POLYMARKET

    @property
    def data_source(self) -> DataSource:
        return self._data_source

    async def list_active_markets(self, *, max_pages: int | None = None) -> list[NormalizedMarket]:
        markets: list[NormalizedMarket] = []
        cursor: str | None = None
        pages = 0
        while True:
            payload = await self._gamma.get_json(
                "/markets/keyset",
                {"closed": "false", "limit": KEYSET_PAGE_LIMIT, "after_cursor": cursor},
            )
            pages += 1
            raw_markets = payload.get("markets") if isinstance(payload, dict) else None
            if not isinstance(raw_markets, list):
                raise NormalizationError("polymarket /markets/keyset lacks 'markets' array")
            for raw in raw_markets:
                if not isinstance(raw, dict):
                    continue
                try:
                    markets.append(
                        normalize_polymarket_market(
                            raw, data_source=self._data_source, fee_defaults=self._fee_defaults
                        )
                    )
                except NormalizationError as exc:
                    METRICS.inc("normalization_errors_total", venue="polymarket")
                    log_event(logger, logging.WARNING, "skipping polymarket market", error=str(exc))
            cursor = payload.get("next_cursor") or None
            short_page = len(raw_markets) < KEYSET_PAGE_LIMIT
            if not cursor or short_page or (max_pages is not None and pages >= max_pages):
                break
        METRICS.set_gauge("markets_listed", len(markets), venue="polymarket")
        return markets

    async def enrich_markets(self, markets: Sequence[NormalizedMarket]) -> list[NormalizedMarket]:
        """Gamma listings already carry full metadata; nothing to add."""
        return list(markets)

    async def get_market(self, market_id: str) -> NormalizedMarket:
        payload = await self._gamma.get_json(f"/markets/{market_id}")
        if not isinstance(payload, dict):
            raise NormalizationError(f"polymarket /markets/{market_id} is not an object")
        return normalize_polymarket_market(
            payload, data_source=self._data_source, fee_defaults=self._fee_defaults
        )

    async def get_order_book(self, market: NormalizedMarket) -> BinaryBookSnapshot:
        books = await self.get_order_books([market])
        if market.venue_market_id not in books:
            raise VenueHttpError("polymarket", f"no book for market {market.venue_market_id}")
        return books[market.venue_market_id]

    async def get_order_books(self, markets: Sequence[NormalizedMarket]) -> dict[str, BinaryBookSnapshot]:
        token_index: dict[str, tuple[NormalizedMarket, OutcomeSide]] = {}
        for market in markets:
            for outcome in market.outcomes:
                if outcome.token_id:
                    token_index[outcome.token_id] = (market, outcome.side)
        raw_books = await self._fetch_raw_books(list(token_index))
        per_market: dict[str, dict[OutcomeSide, NormalizedOrderBook]] = {}
        received_at = self._clock.now()
        for token_id, payload in raw_books.items():
            market, side = token_index[token_id]
            try:
                book = normalize_polymarket_book(
                    payload,
                    market=market,
                    side=side,
                    received_at=received_at,
                    data_source=self._data_source,
                )
            except NormalizationError as exc:
                METRICS.inc("normalization_errors_total", venue="polymarket")
                log_event(
                    logger, logging.WARNING, "discarding polymarket book", token=token_id[:16], error=str(exc)
                )
                continue
            per_market.setdefault(market.venue_market_id, {})[side] = book
        result: dict[str, BinaryBookSnapshot] = {}
        for market_id, sides in per_market.items():
            if OutcomeSide.YES in sides and OutcomeSide.NO in sides:
                result[market_id] = BinaryBookSnapshot(
                    market_id=market_id, yes=sides[OutcomeSide.YES], no=sides[OutcomeSide.NO]
                )
        return result

    async def _fetch_raw_books(self, token_ids: list[str]) -> dict[str, dict[str, Any]]:
        books: dict[str, dict[str, Any]] = {}
        for start in range(0, len(token_ids), BOOKS_BATCH_LIMIT):
            chunk = token_ids[start : start + BOOKS_BATCH_LIMIT]
            try:
                payload = await self._clob.post_json_readonly(
                    "/books", [{"token_id": token_id} for token_id in chunk]
                )
            except VenueHttpError as exc:
                log_event(
                    logger, logging.WARNING, "POST /books failed; falling back to GET /book", error=str(exc)
                )
                METRICS.inc("book_batch_fallbacks_total", venue="polymarket")
                payload = await self._fetch_individually(chunk)
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict) and item.get("asset_id"):
                        books[str(item["asset_id"])] = item
        return books

    async def _fetch_individually(self, token_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for token_id in token_ids:
            try:
                payload = await self._clob.get_json("/book", {"token_id": token_id})
            except VenueHttpError as exc:
                METRICS.inc("book_fetch_failures_total", venue="polymarket")
                log_event(
                    logger,
                    logging.WARNING,
                    "polymarket book unavailable",
                    token=token_id[:16],
                    error=str(exc),
                )
                continue
            if isinstance(payload, dict):
                payload.setdefault("asset_id", token_id)
                results.append(payload)
        return results

    async def stream_order_books(
        self, markets: Sequence[NormalizedMarket]
    ) -> AsyncIterator[BinaryBookSnapshot]:
        """Stream via the public market WebSocket; falls back to REST polling."""
        stream = PolymarketBookStream(
            ws_url=self._ws_url,
            markets=list(markets),
            clock=self._clock,
            data_source=self._data_source,
            rest_fetch=self.get_order_books,
            poll_interval_seconds=self._poll_interval,
        )
        async for snapshot in stream.run():
            yield snapshot

    async def healthcheck(self) -> VenueHealth:
        now = self._clock.now()
        try:
            await self._gamma.get_json("/markets/keyset", {"limit": 1, "closed": "false"})
        except VenueHttpError as exc:
            return VenueHealth(
                venue=Venue.POLYMARKET,
                data_source=self._data_source,
                reachable=False,
                checked_at=now,
                last_success_at=self._gamma.last_success_at,
                last_error=str(exc),
                detail="Gamma GET /markets/keyset failed",
            )
        return VenueHealth(
            venue=Venue.POLYMARKET,
            data_source=self._data_source,
            reachable=True,
            checked_at=now,
            last_success_at=self._gamma.last_success_at,
            last_error=self._clob.last_error,
            detail="Gamma reachable; CLOB status reflects the last book request",
        )
