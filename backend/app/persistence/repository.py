"""Repository: the only module that talks to the ORM."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, desc, select

from app.domain.enums import OutcomeSide, Venue
from app.domain.interfaces import VenueHealth
from app.domain.models import ArbitrageOpportunity, MarketPair, NormalizedMarket, NormalizedOrderBook
from app.domain.paper import HedgedBundle, PaperExecution, PaperFill, PaperOrder
from app.execution.ledger import PaperLedger, PositionState
from app.matching.eligibility import Eligibility
from app.persistence.db import (
    AuditEventRow,
    Database,
    IdempotencyRow,
    MarketPairRow,
    MarketRow,
    OpportunityRow,
    OrderBookRow,
    PaperExecutionRow,
    PaperFillRow,
    PaperOrderRow,
    PaperStateRow,
    RelationshipEdgeRow,
    ScanRunRow,
    VenueHealthRow,
)

LEDGER_KEY = "ledger"


def _dump(model: Any) -> dict[str, Any]:
    payload: dict[str, Any] = model.model_dump(mode="json")
    return payload


def book_key(book: NormalizedOrderBook) -> str:
    return f"{book.venue.value}:{book.market_id}:{book.outcome_id}"


def ledger_to_dict(ledger: PaperLedger) -> dict[str, Any]:
    return {
        "starting_cash": {v.value: str(a) for v, a in ledger.starting_cash.items()},
        "cash": {v.value: str(a) for v, a in ledger.cash.items()},
        "positions": {
            key: {
                "venue": s.venue.value,
                "market_id": s.market_id,
                "outcome_id": s.outcome_id,
                "outcome_side": s.outcome_side.value,
                "quantity": str(s.quantity),
                "cost_basis": str(s.cost_basis),
                "fees_paid": str(s.fees_paid),
                "realized_pnl": str(s.realized_pnl),
                "hedged_quantity": str(s.hedged_quantity),
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for key, s in ledger.positions.items()
        },
        "bundles": [b.model_dump(mode="json") for b in ledger.bundles],
        "consecutive_failures": ledger.consecutive_failures,
        "kill_switch_engaged": ledger.kill_switch_engaged,
    }


def ledger_from_dict(payload: dict[str, Any]) -> PaperLedger:
    ledger = PaperLedger(
        starting_cash={Venue(k): Decimal(v) for k, v in payload["starting_cash"].items()},
        cash={Venue(k): Decimal(v) for k, v in payload["cash"].items()},
        consecutive_failures=int(payload.get("consecutive_failures", 0)),
        kill_switch_engaged=bool(payload.get("kill_switch_engaged", False)),
    )
    for key, item in payload.get("positions", {}).items():
        ledger.positions[key] = PositionState(
            venue=Venue(item["venue"]),
            market_id=item["market_id"],
            outcome_id=item["outcome_id"],
            outcome_side=OutcomeSide(item["outcome_side"]),
            quantity=Decimal(item["quantity"]),
            cost_basis=Decimal(item["cost_basis"]),
            fees_paid=Decimal(item["fees_paid"]),
            realized_pnl=Decimal(item["realized_pnl"]),
            hedged_quantity=Decimal(item["hedged_quantity"]),
            updated_at=datetime.fromisoformat(item["updated_at"]) if item.get("updated_at") else None,
        )
    ledger.bundles = [HedgedBundle.model_validate(b) for b in payload.get("bundles", [])]
    return ledger


class Repository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- scan runs -------------------------------------------------------------------------

    async def save_run(
        self,
        run_id: str,
        *,
        kind: str,
        data_source: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        summary: dict[str, Any],
    ) -> None:
        async with self._db.sessions.begin() as session:
            await session.merge(
                ScanRunRow(
                    id=run_id,
                    kind=kind,
                    data_source=data_source,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    summary=summary,
                )
            )

    async def latest_run(self) -> ScanRunRow | None:
        async with self._db.sessions() as session:
            result = await session.execute(
                select(ScanRunRow)
                .where(ScanRunRow.status == "completed")
                .order_by(desc(ScanRunRow.started_at))
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def list_runs(self, limit: int = 20) -> list[ScanRunRow]:
        async with self._db.sessions() as session:
            result = await session.execute(
                select(ScanRunRow).order_by(desc(ScanRunRow.started_at)).limit(limit)
            )
            return list(result.scalars())

    # ---- markets ------------------------------------------------------------------------------

    async def upsert_markets(
        self, markets: Sequence[NormalizedMarket], eligibility: Sequence[Eligibility], now: datetime
    ) -> None:
        verdicts = {e.market_key: e for e in eligibility}
        async with self._db.sessions.begin() as session:
            for market in markets:
                verdict = verdicts.get(market.key)
                await session.merge(
                    MarketRow(
                        key=market.key,
                        venue=market.venue.value,
                        venue_market_id=market.venue_market_id,
                        status=market.status.value,
                        data_source=market.data_source.value,
                        eligible=bool(verdict and verdict.eligible),
                        eligibility_reasons=list(verdict.reasons) if verdict else ["not evaluated"],
                        updated_at=now,
                        payload=_dump(market),
                    )
                )

    async def list_markets(self, venue: str | None = None) -> list[MarketRow]:
        async with self._db.sessions() as session:
            query = select(MarketRow).order_by(MarketRow.key)
            if venue:
                query = query.where(MarketRow.venue == venue)
            return list((await session.execute(query)).scalars())

    async def get_market(self, key: str) -> NormalizedMarket | None:
        async with self._db.sessions() as session:
            row = await session.get(MarketRow, key)
            return NormalizedMarket.model_validate(row.payload) if row else None

    # ---- books --------------------------------------------------------------------------------

    async def upsert_books(self, books: Sequence[NormalizedOrderBook]) -> None:
        async with self._db.sessions.begin() as session:
            for book in books:
                await session.merge(
                    OrderBookRow(
                        key=book_key(book),
                        venue=book.venue.value,
                        market_id=book.market_id,
                        outcome_id=book.outcome_id,
                        observed_at=book.observed_at,
                        data_source=book.data_source.value,
                        payload=_dump(book),
                    )
                )

    async def books_for_market(self, venue: str, market_id: str) -> list[NormalizedOrderBook]:
        async with self._db.sessions() as session:
            rows = (
                await session.execute(
                    select(OrderBookRow).where(
                        OrderBookRow.venue == venue, OrderBookRow.market_id == market_id
                    )
                )
            ).scalars()
            return [NormalizedOrderBook.model_validate(r.payload) for r in rows]

    async def all_books(self) -> list[NormalizedOrderBook]:
        async with self._db.sessions() as session:
            rows = (await session.execute(select(OrderBookRow))).scalars()
            return [NormalizedOrderBook.model_validate(r.payload) for r in rows]

    # ---- pairs and edges ----------------------------------------------------------------------

    async def replace_pairs(
        self, run_id: str, pairs: Sequence[MarketPair], edges: Sequence[dict[str, Any]]
    ) -> None:
        async with self._db.sessions.begin() as session:
            await session.execute(delete(MarketPairRow))
            await session.execute(delete(RelationshipEdgeRow))
            for pair in pairs:
                session.add(
                    MarketPairRow(
                        id=pair.id,
                        run_id=run_id,
                        kalshi_market_id=pair.kalshi_market_id,
                        polymarket_market_id=pair.polymarket_market_id,
                        relation=pair.relation.value,
                        approved=pair.approved_for_arbitrage_calculation,
                        evaluated_at=pair.evaluated_at,
                        data_source=pair.data_source.value,
                        payload=_dump(pair),
                    )
                )
            for edge in edges:
                session.add(
                    RelationshipEdgeRow(
                        id=str(edge["id"]),
                        run_id=run_id,
                        source=str(edge["source"]),
                        target=str(edge["target"]),
                        relation=str(edge["relation"]),
                        pair_id=str(edge["pair_id"]),
                        payload=edge,
                    )
                )

    async def list_pairs(self) -> list[MarketPair]:
        async with self._db.sessions() as session:
            rows = (await session.execute(select(MarketPairRow).order_by(MarketPairRow.id))).scalars()
            return [MarketPair.model_validate(r.payload) for r in rows]

    async def get_pair(self, pair_id: str) -> MarketPair | None:
        async with self._db.sessions() as session:
            row = await session.get(MarketPairRow, pair_id)
            return MarketPair.model_validate(row.payload) if row else None

    async def list_edges(self) -> list[dict[str, Any]]:
        async with self._db.sessions() as session:
            rows = (
                await session.execute(select(RelationshipEdgeRow).order_by(RelationshipEdgeRow.id))
            ).scalars()
            return [dict(r.payload) for r in rows]

    # ---- opportunities --------------------------------------------------------------------------

    async def save_opportunities(self, run_id: str, opportunities: Sequence[ArbitrageOpportunity]) -> None:
        async with self._db.sessions.begin() as session:
            for opp in opportunities:
                await session.merge(
                    OpportunityRow(
                        id=opp.id,
                        run_id=run_id,
                        pair_id=opp.pair_id,
                        strategy_type=opp.strategy_type.value,
                        status=opp.status.value,
                        detected_at=opp.detected_at,
                        data_source=opp.data_source.value,
                        payload=_dump(opp),
                    )
                )

    async def list_opportunities(self, run_id: str | None) -> list[ArbitrageOpportunity]:
        async with self._db.sessions() as session:
            query = select(OpportunityRow).order_by(desc(OpportunityRow.detected_at), OpportunityRow.id)
            if run_id:
                query = query.where(OpportunityRow.run_id == run_id)
            rows = (await session.execute(query)).scalars()
            return [ArbitrageOpportunity.model_validate(r.payload) for r in rows]

    async def get_opportunity(self, opportunity_id: str) -> tuple[ArbitrageOpportunity, str] | None:
        async with self._db.sessions() as session:
            row = await session.get(OpportunityRow, opportunity_id)
            return (ArbitrageOpportunity.model_validate(row.payload), row.run_id) if row else None

    # ---- paper trading ------------------------------------------------------------------------

    async def load_ledger(self, starting: dict[Venue, Decimal]) -> PaperLedger:
        async with self._db.sessions() as session:
            row = await session.get(PaperStateRow, LEDGER_KEY)
            return ledger_from_dict(row.payload) if row else PaperLedger.fresh(starting)

    async def save_execution(self, execution: PaperExecution, ledger: PaperLedger, now: datetime) -> None:
        async with self._db.sessions.begin() as session:
            session.add(
                PaperExecutionRow(
                    id=execution.id,
                    idempotency_key=execution.idempotency_key,
                    opportunity_id=execution.opportunity_id,
                    outcome=execution.outcome.value,
                    started_at=execution.started_at,
                    payload=_dump(execution),
                )
            )
            for order in execution.orders:
                session.add(
                    PaperOrderRow(
                        id=order.id,
                        execution_id=execution.id,
                        opportunity_id=order.opportunity_id,
                        venue=order.venue.value,
                        status=order.status.value,
                        created_at=order.created_at,
                        payload=_dump(order),
                    )
                )
                for fill in order.fills:
                    session.add(
                        PaperFillRow(
                            id=fill.id, order_id=order.id, filled_at=fill.filled_at, payload=_dump(fill)
                        )
                    )
            await session.merge(PaperStateRow(key=LEDGER_KEY, updated_at=now, payload=ledger_to_dict(ledger)))

    async def save_ledger(self, ledger: PaperLedger, now: datetime) -> None:
        async with self._db.sessions.begin() as session:
            await session.merge(PaperStateRow(key=LEDGER_KEY, updated_at=now, payload=ledger_to_dict(ledger)))

    async def get_execution_by_key(self, key: str) -> PaperExecution | None:
        async with self._db.sessions() as session:
            row = (
                await session.execute(
                    select(PaperExecutionRow).where(PaperExecutionRow.idempotency_key == key)
                )
            ).scalar_one_or_none()
            return PaperExecution.model_validate(row.payload) if row else None

    async def list_executions(self) -> list[PaperExecution]:
        async with self._db.sessions() as session:
            rows = (
                await session.execute(select(PaperExecutionRow).order_by(desc(PaperExecutionRow.started_at)))
            ).scalars()
            return [PaperExecution.model_validate(r.payload) for r in rows]

    async def list_orders(self) -> list[PaperOrder]:
        async with self._db.sessions() as session:
            rows = (
                await session.execute(
                    select(PaperOrderRow).order_by(desc(PaperOrderRow.created_at), PaperOrderRow.id)
                )
            ).scalars()
            return [PaperOrder.model_validate(r.payload) for r in rows]

    async def list_fills(self) -> list[PaperFill]:
        async with self._db.sessions() as session:
            rows = (await session.execute(select(PaperFillRow).order_by(PaperFillRow.filled_at))).scalars()
            return [PaperFill.model_validate(r.payload) for r in rows]

    # ---- audit ----------------------------------------------------------------------------------

    async def add_audit(
        self,
        *,
        ts: datetime,
        category: str,
        message: str,
        data_source: str,
        correlation_id: str | None = None,
        pair_id: str | None = None,
        opportunity_id: str | None = None,
        execution_id: str | None = None,
        paper_order_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        async with self._db.sessions.begin() as session:
            session.add(
                AuditEventRow(
                    ts=ts,
                    category=category,
                    message=message,
                    data_source=data_source,
                    correlation_id=correlation_id,
                    pair_id=pair_id,
                    opportunity_id=opportunity_id,
                    execution_id=execution_id,
                    paper_order_id=paper_order_id,
                    payload=payload or {},
                )
            )

    async def list_audit(self, *, limit: int, category: str | None = None) -> list[AuditEventRow]:
        async with self._db.sessions() as session:
            query = select(AuditEventRow).order_by(desc(AuditEventRow.id)).limit(limit)
            if category:
                query = query.where(AuditEventRow.category == category)
            return list((await session.execute(query)).scalars())

    # ---- idempotency ----------------------------------------------------------------------------

    async def get_idempotency(self, key: str) -> IdempotencyRow | None:
        async with self._db.sessions() as session:
            return await session.get(IdempotencyRow, key)

    async def save_idempotency(
        self,
        key: str,
        *,
        scope: str,
        request_hash: str,
        status_code: int,
        response: dict[str, Any],
        now: datetime,
    ) -> None:
        async with self._db.sessions.begin() as session:
            await session.merge(
                IdempotencyRow(
                    key=key,
                    scope=scope,
                    request_hash=request_hash,
                    status_code=status_code,
                    response=response,
                    created_at=now,
                )
            )

    # ---- venue health --------------------------------------------------------------------------

    async def save_health(self, health: Sequence[VenueHealth]) -> None:
        async with self._db.sessions.begin() as session:
            for item in health:
                await session.merge(
                    VenueHealthRow(venue=item.venue.value, checked_at=item.checked_at, payload=_dump(item))
                )

    async def list_health(self) -> list[VenueHealth]:
        async with self._db.sessions() as session:
            rows = (await session.execute(select(VenueHealthRow).order_by(VenueHealthRow.venue))).scalars()
            return [VenueHealth.model_validate(r.payload) for r in rows]


__all__ = ["Repository", "book_key", "ledger_from_dict", "ledger_to_dict"]
