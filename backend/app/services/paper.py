"""Paper execution service: validation, idempotency, simulation, persistence and audit.

This service mutates only local simulated state (the paper ledger, paper orders/fills and
audit events). It has no dependency on any venue transport.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.decimal_utils import ZERO
from app.core.ids import stable_id
from app.core.logging import get_logger, log_context, log_event
from app.domain.enums import CheckStatus, ExecutionScenario, Venue
from app.domain.models import ArbitrageOpportunity, FeeMetadata, NormalizedOrderBook
from app.domain.paper import PaperExecution, PaperPortfolio
from app.execution.simulator import PaperExecutionEngine
from app.persistence.repository import book_key
from app.services.container import AppContainer

logger = get_logger(__name__)


class PaperExecutionError(Exception):
    """Base class for request-level errors (mapped to HTTP 4xx)."""


class OpportunityNotFoundError(PaperExecutionError):
    pass


class IdempotencyConflictError(PaperExecutionError):
    pass


@dataclass(frozen=True)
class ExecutionRequest:
    opportunity_id: str
    idempotency_key: str
    quantity: Decimal | None
    scenario: ExecutionScenario


@dataclass(frozen=True)
class ExecutionResponse:
    execution: PaperExecution
    replayed: bool


def _book_summary(book: NormalizedOrderBook) -> dict[str, Any]:
    return {
        "venue": book.venue.value,
        "market_id": book.market_id,
        "outcome": book.outcome_side.value,
        "hash": book.checksum_or_source_hash,
        "observed_at": book.observed_at.isoformat(),
        "best_bid": str(book.best_bid.price) if book.best_bid else None,
        "best_ask": str(book.best_ask.price) if book.best_ask else None,
        "ask_levels": [[str(level.price), str(level.quantity)] for level in book.asks[:10]],
        "derived_asks": book.derived_asks,
        "data_source": book.data_source.value,
    }


class PaperService:
    def __init__(self, container: AppContainer) -> None:
        self._c = container

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        repo = self._c.repo
        async with self._c.execution_lock:
            existing = await repo.get_execution_by_key(request.idempotency_key)
            if existing is not None:
                if existing.opportunity_id != request.opportunity_id:
                    raise IdempotencyConflictError(
                        "Idempotency-Key was already used for a different opportunity"
                    )
                return ExecutionResponse(execution=existing, replayed=True)
            found = await repo.get_opportunity(request.opportunity_id)
            if found is None:
                raise OpportunityNotFoundError(request.opportunity_id)
            opportunity, _ = found
            now = self._c.clock.now()
            execution_id = stable_id("exec", opportunity.id, request.idempotency_key)
            with log_context(
                opportunity_id=opportunity.id, pair_id=opportunity.pair_id, correlation_id=execution_id
            ):
                books, fees = await self._inputs(opportunity)
                ledger = await repo.load_ledger(self._c.starting_cash())
                engine = PaperExecutionEngine(self._c.execution, ledger, self._c.clock)
                quantity = (
                    request.quantity if request.quantity is not None else opportunity.max_executable_quantity
                )
                superseded = [
                    leg
                    for leg in opportunity.legs
                    if leg.outcome_id in books
                    and books[leg.outcome_id].checksum_or_source_hash != leg.book_hash
                ]
                if superseded:
                    # Books changed since detection: execute nothing and record why.
                    books = {}
                execution = engine.simulate_hedged_execution(
                    opportunity,
                    books,
                    fees,
                    quantity=quantity if quantity > ZERO else opportunity.max_executable_quantity,
                    scenario=request.scenario,
                    idempotency_key=request.idempotency_key,
                    execution_id=execution_id,
                )
                if superseded:
                    execution = execution.model_copy(
                        update={"reason": f"opportunity superseded by newer order books ({execution.reason})"}
                    )
                await repo.save_execution(execution, engine.ledger, now)
                await self._audit(opportunity, execution, books, now)
                log_event(
                    logger,
                    logging.INFO,
                    "paper execution finished",
                    execution_id=execution.id,
                    outcome=execution.outcome.value,
                    hedged=str(execution.hedged_quantity),
                    residual=str(execution.residual_quantity),
                )
            return ExecutionResponse(execution=execution, replayed=False)

    async def _inputs(
        self, opportunity: ArbitrageOpportunity
    ) -> tuple[dict[str, NormalizedOrderBook], dict[Venue, FeeMetadata]]:
        books: dict[str, NormalizedOrderBook] = {}
        fees: dict[Venue, FeeMetadata] = {}
        for leg in opportunity.legs:
            market = await self._c.repo.get_market(f"{leg.venue.value}:{leg.market_id}")
            if market is not None:
                fees[leg.venue] = market.fee_metadata
            for book in await self._c.repo.books_for_market(leg.venue.value, leg.market_id):
                books[book.outcome_id] = book
        return books, fees

    async def _audit(
        self,
        opportunity: ArbitrageOpportunity,
        execution: PaperExecution,
        books: dict[str, NormalizedOrderBook],
        now: datetime,
    ) -> None:
        pair = await self._c.repo.get_pair(opportunity.pair_id) if opportunity.pair_id else None
        pair_info: dict[str, Any] | None = None
        if pair is not None:
            pair_info = {
                "id": pair.id,
                "relation": pair.relation.value,
                "confidence": str(pair.confidence),
                "approved": pair.approved_for_arbitrage_calculation,
                "why_equivalent": list(pair.decision_reasons),
                "passed_checks": [
                    f"{c.name}: {c.detail}" for c in pair.deterministic_checks if c.status is CheckStatus.PASS
                ],
                "blocking_mismatches": list(pair.blocking_mismatches),
                "adjudication_source": pair.adjudication_source.value,
            }
        used = [books[leg.outcome_id] for leg in opportunity.legs if leg.outcome_id in books]
        payload = {
            "paper_trading_only": True,
            "pair": pair_info,
            "opportunity": {
                "id": opportunity.id,
                "direction": opportunity.direction,
                "status": opportunity.status.value,
                "expected_net_profit": str(opportunity.expected_net_profit),
                "max_executable_quantity": str(opportunity.max_executable_quantity),
                "detected_at": opportunity.detected_at.isoformat(),
                "stale_after": opportunity.stale_after.isoformat(),
                "assumptions": list(opportunity.assumptions),
            },
            "books_used": [_book_summary(book) for book in used],
            "book_timestamps": {k: v.isoformat() for k, v in opportunity.book_timestamps.items()},
            "requested_quantity": str(execution.requested_quantity),
            "orders": [
                {
                    "id": order.id,
                    "purpose": order.purpose,
                    "venue": order.venue.value,
                    "side": order.outcome_side.value,
                    "action": order.action.value,
                    "requested": str(order.requested_quantity),
                    "filled": str(order.filled_quantity),
                    "average_price": str(order.average_price) if order.average_price is not None else None,
                    "fees": str(order.fees),
                    "status": order.status.value,
                    "reason": order.reject_reason,
                    "fills": [[str(f.price), str(f.quantity), str(f.fee)] for f in order.fills],
                }
                for order in execution.orders
            ],
            "total_fees": str(execution.total_fees),
            "hedged_quantity": str(execution.hedged_quantity),
            "residual_quantity": str(execution.residual_quantity),
            "residual_notional": str(execution.residual_notional),
            "realized_pnl": str(execution.realized_pnl),
            "locked_in_pnl": str(execution.expected_locked_in_pnl),
            "outcome": execution.outcome.value,
            "reason": execution.reason,
            "steps": [step.model_dump(mode="json") for step in execution.steps],
        }
        await self._c.repo.add_audit(
            ts=now,
            category="paper_execution",
            message=f"paper execution {execution.outcome.value}: {execution.reason}",
            data_source=opportunity.data_source.value,
            correlation_id=execution.id,
            pair_id=opportunity.pair_id,
            opportunity_id=opportunity.id,
            execution_id=execution.id,
            paper_order_id=execution.orders[0].id if execution.orders else None,
            payload=payload,
        )

    async def portfolio(self) -> PaperPortfolio:
        ledger = await self._c.repo.load_ledger(self._c.starting_cash())
        marks: dict[str, Decimal | None] = {}
        for book in await self._c.repo.all_books():
            marks[book_key(book)] = book.best_bid.price if book.best_bid else None
        return ledger.portfolio(self._c.clock.now(), marks)

    async def reset_kill_switch(self) -> PaperPortfolio:
        ledger = await self._c.repo.load_ledger(self._c.starting_cash())
        ledger.reset_kill_switch()
        now = self._c.clock.now()
        await self._c.repo.save_ledger(ledger, now)
        await self._c.repo.add_audit(
            ts=now,
            category="kill_switch",
            message="kill switch reset by operator",
            data_source=self._c.data_source.value,
            payload={},
        )
        return await self.portfolio()
