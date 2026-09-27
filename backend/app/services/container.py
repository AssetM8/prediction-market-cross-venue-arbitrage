"""Composition root: builds clocks, venues, pipeline, calculator and persistence from settings.

Fixture mode wires the real Kalshi/Polymarket adapters to :class:`FixtureTransport` and a
:class:`FrozenClock` pinned to the fixture ``as_of`` instant. Live mode wires them to
:class:`ReadOnlyHttpTransport` and the system clock. No credentials are read in either mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from app.arbitrage.cross_venue import ArbitrageConfig
from app.core.clock import Clock, FrozenClock, SystemClock
from app.core.config import DataMode, EmbeddingProviderName, LLMProvider, Settings
from app.core.http import ReadOnlyHttpTransport, RetryPolicy, Transport
from app.domain.enums import DataSource, Venue
from app.execution.simulator import ExecutionConfig
from app.matching.adjudicator import AnthropicAdjudicator, RelationAdjudicator
from app.matching.embeddings import EmbeddingProvider, HashingEmbeddingProvider
from app.matching.pipeline import MatchingConfig, MatchingPipeline
from app.persistence.db import Database
from app.persistence.repository import Repository
from app.venues.fixture import FixtureSet, FixtureTransport
from app.venues.kalshi.client import KalshiMarketDataVenue
from app.venues.kalshi.normalize import KalshiFeeDefaults
from app.venues.polymarket.client import PolymarketMarketDataVenue
from app.venues.polymarket.normalize import PolymarketFeeDefaults

POLYMARKET_READONLY_POSTS = frozenset({"/books"})


@dataclass
class VenueBundle:
    kalshi: KalshiMarketDataVenue
    polymarket: PolymarketMarketDataVenue
    data_source: DataSource
    transports: list[Transport] = field(default_factory=list)

    async def aclose(self) -> None:
        for transport in self.transports:
            await transport.aclose()


def kalshi_fee_defaults(settings: Settings) -> KalshiFeeDefaults:
    return KalshiFeeDefaults(
        taker_rate=settings.kalshi_taker_fee_rate,
        rounding_quantum=settings.kalshi_fee_rounding_quantum,
        fallback_multiplier=settings.kalshi_fallback_fee_multiplier,
    )


def polymarket_fee_defaults(settings: Settings) -> PolymarketFeeDefaults:
    return PolymarketFeeDefaults(
        fallback_rate=settings.polymarket_fallback_fee_rate,
        rounding_quantum=settings.polymarket_fee_rounding_quantum,
    )


def build_fixture_venues(settings: Settings, fixtures: FixtureSet, clock: Clock) -> VenueBundle:
    kalshi_t = FixtureTransport("kalshi", fixtures.kalshi_handler, now=clock.now)
    gamma_t = FixtureTransport("polymarket-gamma", fixtures.gamma_handler, now=clock.now)
    clob_t = FixtureTransport(
        "polymarket-clob", fixtures.clob_handler, readonly_post_paths=POLYMARKET_READONLY_POSTS, now=clock.now
    )
    return VenueBundle(
        kalshi=KalshiMarketDataVenue(
            kalshi_t,
            clock=clock,
            data_source=DataSource.FIXTURE,
            fee_defaults=kalshi_fee_defaults(settings),
            book_depth=settings.book_depth_levels,
            poll_interval_seconds=settings.poll_interval_seconds,
        ),
        polymarket=PolymarketMarketDataVenue(
            gamma_t,
            clob_t,
            clock=clock,
            data_source=DataSource.FIXTURE,
            fee_defaults=polymarket_fee_defaults(settings),
            ws_url=None,
            poll_interval_seconds=settings.poll_interval_seconds,
        ),
        data_source=DataSource.FIXTURE,
        transports=[kalshi_t, gamma_t, clob_t],
    )


def build_live_venues(settings: Settings, clock: Clock) -> VenueBundle:
    retry = RetryPolicy(
        max_attempts=settings.http_max_attempts,
        base_delay=settings.http_backoff_base_seconds,
        max_delay=settings.http_backoff_max_seconds,
    )

    def transport(
        name: str, url: str, rate: float, posts: frozenset[str] = frozenset()
    ) -> ReadOnlyHttpTransport:
        return ReadOnlyHttpTransport(
            name=name,
            base_url=url,
            connect_timeout=settings.http_connect_timeout_seconds,
            read_timeout=settings.http_read_timeout_seconds,
            rate_per_second=rate,
            retry=retry,
            readonly_post_paths=posts,
            clock=clock,
        )

    kalshi_t = transport("kalshi", settings.kalshi_base_url, settings.kalshi_max_requests_per_second)
    gamma_t = transport(
        "polymarket-gamma", settings.polymarket_gamma_url, settings.polymarket_max_requests_per_second
    )
    clob_t = transport(
        "polymarket-clob",
        settings.polymarket_clob_url,
        settings.polymarket_max_requests_per_second,
        POLYMARKET_READONLY_POSTS,
    )
    return VenueBundle(
        kalshi=KalshiMarketDataVenue(
            kalshi_t,
            clock=clock,
            data_source=DataSource.LIVE,
            fee_defaults=kalshi_fee_defaults(settings),
            book_depth=settings.book_depth_levels,
            poll_interval_seconds=settings.poll_interval_seconds,
        ),
        polymarket=PolymarketMarketDataVenue(
            gamma_t,
            clob_t,
            clock=clock,
            data_source=DataSource.LIVE,
            fee_defaults=polymarket_fee_defaults(settings),
            ws_url=settings.polymarket_ws_url if settings.polymarket_ws_enabled else None,
            poll_interval_seconds=settings.poll_interval_seconds,
        ),
        data_source=DataSource.LIVE,
        transports=[kalshi_t, gamma_t, clob_t],
    )


def build_embedder(settings: Settings) -> EmbeddingProvider | None:
    if settings.optional_embeddings is EmbeddingProviderName.HASHING:
        return HashingEmbeddingProvider()
    return None


def build_adjudicator(settings: Settings) -> RelationAdjudicator | None:
    if settings.optional_llm_provider is LLMProvider.ANTHROPIC:
        return AnthropicAdjudicator(
            api_key=settings.optional_llm_api_key.get_secret_value(),
            model=settings.optional_llm_model,
            base_url=settings.optional_llm_base_url,
            timeout_seconds=settings.optional_llm_timeout_seconds,
        )
    return None


def arbitrage_config(settings: Settings) -> ArbitrageConfig:
    return ArbitrageConfig(
        stale_after_seconds=settings.book_stale_after_seconds,
        min_top_of_book_depth=settings.min_top_of_book_depth,
        min_net_profit=settings.min_net_profit,
        min_return_on_capital=settings.min_return_on_capital,
        settlement_buffer=settings.settlement_cost_buffer,
        latency_buffer=settings.latency_risk_buffer,
        capital_cost_annual_rate=settings.capital_cost_annual_rate,
        quantity_step=settings.quantity_step,
        max_trade_quantity=settings.max_trade_quantity,
    )


def execution_config(settings: Settings) -> ExecutionConfig:
    return ExecutionConfig(
        policy=settings.execution_policy,
        max_hedge_slippage=settings.max_hedge_slippage,
        max_unhedged_notional=settings.max_unhedged_notional,
        unwind_haircut=settings.unwind_haircut,
        kill_switch_max_failures=settings.kill_switch_max_consecutive_failures,
        partial_fill_fraction=settings.partial_fill_fraction,
        adverse_price_move=settings.adverse_price_move,
        quantity_step=settings.quantity_step,
    )


def matching_config(settings: Settings) -> MatchingConfig:
    return MatchingConfig(
        min_confidence=settings.market_pair_min_confidence,
        min_similarity=settings.candidate_min_similarity,
        max_candidates_per_market=settings.max_candidates_per_market,
        candidate_time_window=timedelta(days=settings.candidate_time_window_days),
        max_resolution_gap=timedelta(hours=settings.max_close_time_diff_hours),
    )


class AppContainer:
    """Long-lived application services for the API or a CLI command."""

    def __init__(self, settings: Settings, *, data_mode: DataMode | None = None) -> None:
        self.settings = settings
        self.data_mode = data_mode or settings.data_mode
        self.fixtures: FixtureSet | None = None
        self.clock: Clock
        if self.data_mode is DataMode.FIXTURE:
            self.fixtures = FixtureSet.load(settings.fixtures_dir)
            self.clock = FrozenClock(self.fixtures.as_of)
        else:
            self.clock = SystemClock()
        self.db = Database(settings.database_url)
        self.repo = Repository(self.db)
        self.pipeline = MatchingPipeline(
            matching_config(settings),
            embedder=build_embedder(settings),
            adjudicator=build_adjudicator(settings),
        )
        self.arbitrage = arbitrage_config(settings)
        self.execution = execution_config(settings)
        self.refresh_lock = asyncio.Lock()
        self.execution_lock = asyncio.Lock()

    @property
    def data_source(self) -> DataSource:
        return DataSource.FIXTURE if self.data_mode is DataMode.FIXTURE else DataSource.LIVE

    def starting_cash(self) -> dict[Venue, Decimal]:
        return {
            Venue.KALSHI: self.settings.paper_starting_cash_kalshi,
            Venue.POLYMARKET: self.settings.paper_starting_cash_polymarket,
        }

    def venues(self) -> VenueBundle:
        if self.fixtures is not None:
            return build_fixture_venues(self.settings, self.fixtures, self.clock)
        return build_live_venues(self.settings, self.clock)

    async def start(self) -> None:
        await self.db.init()

    async def aclose(self) -> None:
        await self.db.dispose()
