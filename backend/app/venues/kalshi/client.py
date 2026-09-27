"""Read-only Kalshi market-data adapter (REST).

Endpoints used (all public per the official docs; no credentials are sent):

* ``GET /events?status=open&with_nested_markets=true`` (cursor pagination, limit <= 200)
* ``GET /events/{event_ticker}``
* ``GET /series/{series_ticker}`` (fee type/multiplier, settlement sources)
* ``GET /markets/{ticker}``
* ``GET /markets/{ticker}/orderbook`` - documented as public in the order-book guide but
  listed with auth headers in the API reference. A 401 is handled by falling back to the
  market-level top of book (flagged ``depth_limited``).
* ``GET /exchange/status``

Kalshi's WebSocket requires API-key authentication even for public channels, so
:meth:`stream_order_books` polls REST instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.core.clock import Clock
from app.core.http import Transport, VenueAuthRequiredError, VenueHttpError
from app.core.logging import get_logger, log_context, log_event
from app.core.metrics import METRICS
from app.domain.enums import DataSource, Venue
from app.domain.interfaces import BinaryBookSnapshot, VenueHealth
from app.domain.models import NormalizedMarket
from app.venues.common import NormalizationError
from app.venues.kalshi.normalize import (
    KalshiFeeDefaults,
    normalize_kalshi_market,
    normalize_kalshi_orderbook,
    normalize_kalshi_top_of_book,
)

logger = get_logger(__name__)

EVENTS_PAGE_LIMIT = 200  # documented maximum for GET /events
BOOK_CONCURRENCY = 4


class KalshiMarketDataVenue:
    """Implements :class:`app.domain.interfaces.MarketDataVenue` for Kalshi."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        data_source: DataSource,
        fee_defaults: KalshiFeeDefaults,
        book_depth: int = 0,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._data_source = data_source
        self._fee_defaults = fee_defaults
        self._book_depth = book_depth
        self._poll_interval = poll_interval_seconds
        self._series_cache: dict[str, dict[str, Any] | None] = {}
        self._event_cache: dict[str, dict[str, Any]] = {}
        self._orderbook_requires_auth = False

    @property
    def venue(self) -> Venue:
        return Venue.KALSHI

    @property
    def data_source(self) -> DataSource:
        return self._data_source

    async def list_active_markets(self, *, max_pages: int | None = None) -> list[NormalizedMarket]:
        markets: list[NormalizedMarket] = []
        cursor: str | None = None
        pages = 0
        while True:
            params: dict[str, str | int | bool | None] = {
                "status": "open",
                "with_nested_markets": "true",
                "limit": EVENTS_PAGE_LIMIT,
                "cursor": cursor,
            }
            payload = await self._transport.get_json("/events", params)
            pages += 1
            events = payload.get("events") if isinstance(payload, dict) else None
            if not isinstance(events, list):
                raise NormalizationError("kalshi /events response lacks 'events' array")
            for event in events:
                if not isinstance(event, dict):
                    continue
                self._event_cache[str(event.get("event_ticker"))] = event
                for raw in event.get("markets") or []:
                    if not isinstance(raw, dict) or raw.get("mve_collection_ticker"):
                        continue  # multivariate combos are out of scope
                    try:
                        markets.append(
                            normalize_kalshi_market(
                                raw,
                                event=event,
                                series=None,
                                data_source=self._data_source,
                                fee_defaults=self._fee_defaults,
                            )
                        )
                    except NormalizationError as exc:
                        METRICS.inc("normalization_errors_total", venue="kalshi")
                        log_event(logger, logging.WARNING, "skipping kalshi market", error=str(exc))
            cursor = payload.get("cursor") or None
            if not cursor or (max_pages is not None and pages >= max_pages):
                break
        METRICS.set_gauge("markets_listed", len(markets), venue="kalshi")
        return markets

    async def _series(self, series_ticker: str) -> dict[str, Any] | None:
        if not series_ticker:
            return None
        if series_ticker not in self._series_cache:
            try:
                payload = await self._transport.get_json(f"/series/{series_ticker}")
            except VenueHttpError as exc:
                log_event(
                    logger, logging.WARNING, "kalshi series unavailable", series=series_ticker, error=str(exc)
                )
                self._series_cache[series_ticker] = None
            else:
                series = payload.get("series") if isinstance(payload, dict) else None
                self._series_cache[series_ticker] = series if isinstance(series, dict) else None
        return self._series_cache[series_ticker]

    async def enrich_markets(self, markets: Sequence[NormalizedMarket]) -> list[NormalizedMarket]:
        """Re-normalize markets with series metadata (fees, settlement sources, category).

        Called only for candidate markets so the scan does not fetch every series.
        """
        enriched: list[NormalizedMarket] = []
        for market in markets:
            if market.venue is not Venue.KALSHI or market.extra.get("series_enriched"):
                enriched.append(market)
                continue
            enriched.append(await self.get_market(market.venue_market_id))
        return enriched

    async def get_market(self, market_id: str) -> NormalizedMarket:
        payload = await self._transport.get_json(f"/markets/{market_id}")
        raw = payload.get("market") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise NormalizationError(f"kalshi /markets/{market_id} lacks 'market'")
        event_ticker = str(raw.get("event_ticker") or "")
        event = self._event_cache.get(event_ticker)
        if event is None and event_ticker:
            event_payload = await self._transport.get_json(f"/events/{event_ticker}")
            candidate = event_payload.get("event") if isinstance(event_payload, dict) else None
            event = candidate if isinstance(candidate, dict) else None
            if event is not None:
                self._event_cache[event_ticker] = event
        series_ticker = str((event or {}).get("series_ticker") or "")
        series = await self._series(series_ticker)
        return normalize_kalshi_market(
            raw,
            event=event,
            series=series,
            data_source=self._data_source,
            fee_defaults=self._fee_defaults,
        )

    async def get_order_book(self, market: NormalizedMarket) -> BinaryBookSnapshot:
        ticker = market.venue_market_id
        with log_context(venue="kalshi", market_id=ticker):
            if not self._orderbook_requires_auth:
                try:
                    payload = await self._transport.get_json(
                        f"/markets/{ticker}/orderbook",
                        {"depth": self._book_depth} if self._book_depth else None,
                    )
                except VenueAuthRequiredError:
                    self._orderbook_requires_auth = True
                    log_event(
                        logger,
                        logging.WARNING,
                        "kalshi orderbook endpoint requires credentials; "
                        "falling back to market-level top of book",
                    )
                else:
                    return normalize_kalshi_orderbook(
                        payload,
                        market=market,
                        received_at=self._clock.now(),
                        data_source=self._data_source,
                    )
            payload = await self._transport.get_json(f"/markets/{ticker}")
            raw = payload.get("market") if isinstance(payload, dict) else None
            if not isinstance(raw, dict):
                raise NormalizationError(f"kalshi /markets/{ticker} lacks 'market'")
            return normalize_kalshi_top_of_book(
                raw, market=market, received_at=self._clock.now(), data_source=self._data_source
            )

    async def get_order_books(self, markets: Sequence[NormalizedMarket]) -> dict[str, BinaryBookSnapshot]:
        semaphore = asyncio.Semaphore(BOOK_CONCURRENCY)
        results: dict[str, BinaryBookSnapshot] = {}

        async def fetch(market: NormalizedMarket) -> None:
            async with semaphore:
                try:
                    results[market.venue_market_id] = await self.get_order_book(market)
                except (VenueHttpError, NormalizationError) as exc:
                    METRICS.inc("book_fetch_failures_total", venue="kalshi")
                    log_event(
                        logger,
                        logging.WARNING,
                        "kalshi book unavailable",
                        market=market.venue_market_id,
                        error=str(exc),
                    )

        await asyncio.gather(*(fetch(market) for market in markets))
        return results

    async def stream_order_books(
        self, markets: Sequence[NormalizedMarket]
    ) -> AsyncIterator[BinaryBookSnapshot]:
        """REST polling stream (Kalshi WebSockets require authentication)."""
        last_hash: dict[str, str] = {}
        while True:
            books = await self.get_order_books(markets)
            for market_id, snapshot in books.items():
                digest = snapshot.yes.checksum_or_source_hash
                if last_hash.get(market_id) != digest:
                    last_hash[market_id] = digest
                    yield snapshot
            await asyncio.sleep(self._poll_interval)

    async def healthcheck(self) -> VenueHealth:
        now = self._clock.now()
        credentials_note = (
            ("orderbook depth (fell back to top of book)",) if self._orderbook_requires_auth else ()
        )
        try:
            payload = await self._transport.get_json("/exchange/status")
        except VenueHttpError as exc:
            return VenueHealth(
                venue=Venue.KALSHI,
                data_source=self._data_source,
                reachable=False,
                checked_at=now,
                last_success_at=self._transport.last_success_at,
                last_error=str(exc),
                credentials_required_for=credentials_note,
                detail="GET /exchange/status failed",
            )
        trading = bool(payload.get("trading_active")) if isinstance(payload, dict) else False
        exchange = bool(payload.get("exchange_active")) if isinstance(payload, dict) else False
        return VenueHealth(
            venue=Venue.KALSHI,
            data_source=self._data_source,
            reachable=True,
            checked_at=now,
            last_success_at=self._transport.last_success_at,
            last_error=self._transport.last_error,
            trading_active=exchange and trading,
            credentials_required_for=credentials_note,
            detail=f"exchange_active={exchange} trading_active={trading}",
        )
