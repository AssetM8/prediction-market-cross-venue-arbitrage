"""SQLAlchemy 2.x async persistence (SQLite by default, PostgreSQL optional).

Domain objects are stored as JSON documents (Pydantic ``model_dump(mode="json")`` renders
Decimals as strings, so nothing passes through floats) alongside a few indexed scalar
columns used for lookups. Timestamps are stored as UTC ISO-8601 strings via
:class:`UTCDateTime` so timezone information survives SQLite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Dialect, Integer, String, Text, TypeDecorator, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes stored as sortable ISO strings."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes cannot be stored")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value).astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base for all tables."""


class ScanRunRow(Base):
    __tablename__ = "scan_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    data_source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MarketRow(Base):
    __tablename__ = "markets"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    venue: Mapped[str] = mapped_column(String(16), index=True)
    venue_market_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16))
    data_source: Mapped[str] = mapped_column(String(16))
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    eligibility_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class OrderBookRow(Base):
    __tablename__ = "order_books"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    venue: Mapped[str] = mapped_column(String(16))
    market_id: Mapped[str] = mapped_column(String(160), index=True)
    outcome_id: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    data_source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class MarketPairRow(Base):
    __tablename__ = "market_pairs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    kalshi_market_id: Mapped[str] = mapped_column(String(160))
    polymarket_market_id: Mapped[str] = mapped_column(String(160))
    relation: Mapped[str] = mapped_column(String(32), index=True)
    approved: Mapped[bool] = mapped_column(Boolean, index=True)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    data_source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class RelationshipEdgeRow(Base):
    __tablename__ = "relationship_edges"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(200))
    target: Mapped[str] = mapped_column(String(200))
    relation: Mapped[str] = mapped_column(String(32))
    pair_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    pair_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    strategy_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    data_source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PaperExecutionRow(Base):
    __tablename__ = "paper_executions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PaperOrderRow(Base):
    __tablename__ = "paper_orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(64))
    venue: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PaperFillRow(Base):
    __tablename__ = "paper_fills"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True)
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PaperStateRow(Base):
    __tablename__ = "paper_state"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    message: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pair_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paper_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class VenueHealthRow(Base):
    __tablename__ = "venue_health"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime())
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Database:
    """Engine + session factory; ``init`` creates tables, ``reset`` recreates them."""

    def __init__(self, url: str) -> None:
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["timeout"] = 30
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, connect_args=connect_args, future=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def reset(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def ping(self) -> bool:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
