"""Shared test fixtures and builders. The suite never touches the network."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.core.config import DataMode, Settings
from app.core.metrics import METRICS
from app.domain.enums import AdjudicationSource, DataSource, MarketStatus, OutcomeSide, Relation, Venue
from app.domain.interfaces import BinaryBookSnapshot
from app.domain.models import (
    BookLevel,
    FeeMetadata,
    MarketPair,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    SemanticJudgement,
)
from app.matching.rules import leg_mappings_for
from app.services.container import AppContainer
from app.venues.fixture import FixtureSet

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures"
NOW = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)

D = Decimal

KALSHI_FEE = FeeMetadata(
    model="kalshi_quadratic", rate=D("0.07"), multiplier=D(1), rounding_quantum=D("0.0001"), source="fixture"
)
POLY_FEE = FeeMetadata(
    model="polymarket_curve", rate=D("0.05"), rounding_quantum=D("0.00001"), source="fixture"
)
NO_FEE = FeeMetadata(model="none", rate=D(0), rounding_quantum=D("0.0001"), source="fixture")


@pytest.fixture(autouse=True)
def _reset_metrics() -> Iterator[None]:
    METRICS.reset()
    yield


@pytest.fixture
def fixture_set() -> FixtureSet:
    return FixtureSet.load(FIXTURES_DIR)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        "data_mode": DataMode.FIXTURE,
        "fixtures_dir": FIXTURES_DIR,
        "log_json": True,
        "auto_scan_on_startup": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[AppContainer]:
    active = AppContainer(settings, data_mode=DataMode.FIXTURE)
    await active.db.reset()
    try:
        yield active
    finally:
        await active.aclose()


def levels(*pairs: tuple[str, str]) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(price=D(p), quantity=D(q)) for p, q in pairs)


def make_book(
    *,
    venue: Venue = Venue.KALSHI,
    market_id: str = "M",
    side: OutcomeSide = OutcomeSide.YES,
    bids: tuple[BookLevel, ...] = (),
    asks: tuple[BookLevel, ...] = (),
    observed_at: datetime = NOW,
    issues: tuple[str, ...] = (),
    min_order_size: Decimal | None = None,
    book_hash: str = "hash",
) -> NormalizedOrderBook:
    return NormalizedOrderBook(
        venue=venue,
        market_id=market_id,
        outcome_id=f"{market_id}:{side.value}",
        outcome_side=side,
        sequence_or_timestamp=observed_at.isoformat(),
        received_at=observed_at,
        bids=tuple(sorted(bids, key=lambda lv: lv.price, reverse=True)),
        asks=tuple(sorted(asks, key=lambda lv: lv.price)),
        checksum_or_source_hash=f"{book_hash}:{market_id}:{side.value}",
        data_source=DataSource.FIXTURE,
        integrity_issues=issues,
        min_order_size=min_order_size,
    )


def make_snapshot(
    market_id: str,
    venue: Venue,
    *,
    yes_asks: tuple[BookLevel, ...],
    no_asks: tuple[BookLevel, ...],
    yes_bids: tuple[BookLevel, ...] = (),
    no_bids: tuple[BookLevel, ...] = (),
    observed_at: datetime = NOW,
    issues: tuple[str, ...] = (),
) -> BinaryBookSnapshot:
    return BinaryBookSnapshot(
        market_id=market_id,
        yes=make_book(
            venue=venue,
            market_id=market_id,
            side=OutcomeSide.YES,
            bids=yes_bids,
            asks=yes_asks,
            observed_at=observed_at,
            issues=issues,
        ),
        no=make_book(
            venue=venue,
            market_id=market_id,
            side=OutcomeSide.NO,
            bids=no_bids,
            asks=no_asks,
            observed_at=observed_at,
            issues=issues,
        ),
    )


def make_market(
    market_id: str,
    venue: Venue,
    *,
    title: str = "Test market",
    rules: str = "Resolves Yes if the thing happens.",
    status: MarketStatus = MarketStatus.ACTIVE,
    fee: FeeMetadata | None = None,
    resolution_time: datetime | None = None,
    close_time: datetime | None = None,
    resolution_source: str | None = None,
    category: str = "",
    extra: dict[str, Any] | None = None,
    event_id: str = "E",
    event_title: str = "",
    market_type: str = "binary",
) -> NormalizedMarket:
    return NormalizedMarket(
        venue=venue,
        venue_market_id=market_id,
        venue_event_id=event_id,
        title=title,
        event_title=event_title,
        rules=rules,
        category=category,
        status=status,
        market_type=market_type,
        close_time=close_time or NOW + timedelta(days=30),
        resolution_time=resolution_time or NOW + timedelta(days=30),
        resolution_source=resolution_source,
        outcomes=(
            NormalizedOutcome(
                outcome_id=f"{market_id}:yes", label="Yes", proposition_text=title, side=OutcomeSide.YES
            ),
            NormalizedOutcome(
                outcome_id=f"{market_id}:no", label="No", proposition_text=f"NOT {title}", side=OutcomeSide.NO
            ),
        ),
        fee_metadata=fee or (KALSHI_FEE if venue is Venue.KALSHI else POLY_FEE),
        raw_metadata_hash=f"raw:{market_id}",
        data_source=DataSource.FIXTURE,
        min_order_size=D(1),
        extra=extra or {},
    )


def make_pair(
    relation: Relation, *, approved: bool = True, kalshi_id: str = "K", poly_id: str = "P"
) -> MarketPair:

    judgement = SemanticJudgement(
        relation=relation,
        confidence=D("1.00"),
        shared_event=True,
        same_resolution_criteria=True,
        safe_for_cross_venue_arbitrage=approved,
        reason="test pair",
    )
    return MarketPair(
        id=f"pair_{kalshi_id}_{poly_id}",
        kalshi_market_id=kalshi_id,
        polymarket_market_id=poly_id,
        kalshi_title="K",
        polymarket_title="P",
        similarity_score=D("0.9"),
        relation=relation,
        confidence=D("1.00"),
        deterministic_checks=(),
        semantic_explanation=judgement,
        blocking_mismatches=(),
        adjudication_source=AdjudicationSource.DETERMINISTIC,
        approved_for_arbitrage_calculation=approved,
        decision_reasons=("test",),
        leg_mappings=leg_mappings_for(relation) if approved else (),
        data_source=DataSource.FIXTURE,
        evaluated_at=NOW,
    )
