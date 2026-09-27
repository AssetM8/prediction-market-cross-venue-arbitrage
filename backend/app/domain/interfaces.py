"""Venue-boundary interfaces (dependency inversion).

Services depend on these protocols, never on concrete venue clients. There are three:

* :class:`MarketDataVenue` - read-only market metadata and order books. Implemented by the
  Kalshi and Polymarket adapters over either a live HTTP transport or a fixture transport.
* :class:`PaperExecutionVenue` - local simulation only; mutates nothing but local state.
* :class:`LiveExecutionVenue` - a *future* interface. The only implementation in this
  repository raises :class:`NotImplementedError` and is not wired into any service or
  configuration path (enforced by ``tests/security``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.domain.enums import DataSource, ExecutionScenario, OrderAction, OutcomeSide, Venue
from app.domain.models import (
    ArbitrageOpportunity,
    DomainModel,
    FeeMetadata,
    NormalizedMarket,
    NormalizedOrderBook,
)
from app.domain.paper import PaperExecution, PaperOrder


class BinaryBookSnapshot(DomainModel):
    """YES and NO books for one binary market captured together."""

    market_id: str
    yes: NormalizedOrderBook
    no: NormalizedOrderBook

    def side(self, side: OutcomeSide) -> NormalizedOrderBook:
        return self.yes if side is OutcomeSide.YES else self.no


class VenueHealth(DomainModel):
    venue: Venue
    data_source: DataSource
    reachable: bool
    checked_at: datetime
    last_success_at: datetime | None = None
    last_error: str | None = None
    trading_active: bool | None = None
    credentials_required_for: tuple[str, ...] = ()
    detail: str = ""


@runtime_checkable
class MarketDataVenue(Protocol):
    """Read-only market data for one venue."""

    @property
    def venue(self) -> Venue: ...

    @property
    def data_source(self) -> DataSource: ...

    async def list_active_markets(self, *, max_pages: int | None = None) -> list[NormalizedMarket]:
        """Return currently active markets (paginated internally, bounded by ``max_pages``)."""
        ...

    async def get_market(self, market_id: str) -> NormalizedMarket:
        """Return one market by its venue identifier."""
        ...

    async def get_order_book(self, market: NormalizedMarket) -> BinaryBookSnapshot:
        """Return normalized YES and NO books for a binary market."""
        ...

    async def get_order_books(self, markets: Sequence[NormalizedMarket]) -> dict[str, BinaryBookSnapshot]:
        """Batch variant; missing or failed books are omitted from the result."""
        ...

    def stream_order_books(self, markets: Sequence[NormalizedMarket]) -> AsyncIterator[BinaryBookSnapshot]:
        """Yield book snapshots as they change (WebSocket where public, else REST polling)."""
        ...

    async def healthcheck(self) -> VenueHealth: ...


class PaperOrderRequest(DomainModel):
    execution_id: str
    opportunity_id: str
    leg_index: int
    purpose: str
    venue: Venue
    market_id: str
    outcome_id: str
    outcome_side: OutcomeSide
    action: OrderAction
    quantity: Decimal
    limit_price: Decimal
    fee: FeeMetadata


@runtime_checkable
class PaperExecutionVenue(Protocol):
    """Local paper execution. Implementations must never contact a venue."""

    def simulate_order(
        self,
        request: PaperOrderRequest,
        book: NormalizedOrderBook,
        *,
        fill_cap: Decimal | None = None,
    ) -> PaperOrder: ...

    def simulate_hedged_execution(
        self,
        opportunity: ArbitrageOpportunity,
        books: dict[str, NormalizedOrderBook],
        fees: dict[Venue, FeeMetadata],
        *,
        quantity: Decimal,
        scenario: ExecutionScenario,
        idempotency_key: str,
        execution_id: str,
    ) -> PaperExecution: ...

    def cancel_simulated_order(self, order: PaperOrder) -> PaperOrder: ...


class LiveExecutionVenue(Protocol):
    """Placeholder for a future, separately reviewed live-trading adapter.

    Not implemented. See ``docs/LIVE_TRADING_GAP_ANALYSIS.md`` for what would be required.
    """

    async def submit_order(self, *args: object, **kwargs: object) -> object: ...

    async def cancel_order(self, *args: object, **kwargs: object) -> object: ...
