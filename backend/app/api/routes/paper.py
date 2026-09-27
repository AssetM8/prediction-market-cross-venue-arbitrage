"""Paper trading endpoints. These mutate only local simulated state."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.api.deps import ContainerDep, PaperServiceDep
from app.api.schemas import IDEMPOTENCY_KEY_PATTERN, ExecuteRequest, ExecuteResponse
from app.domain.paper import PaperExecution, PaperOrder, PaperPortfolio, PaperPosition
from app.services.paper import ExecutionRequest, IdempotencyConflictError, OpportunityNotFoundError

router = APIRouter(prefix="/api/paper", tags=["paper trading"])


@router.post(
    "/execute/{opportunity_id}",
    response_model=ExecuteResponse,
    summary="Simulate a two-leg paper execution",
    description="Runs the deterministic paper simulator for a validated cross-venue opportunity. Requires an "
    "Idempotency-Key header: repeating a request with the same key returns the original result "
    "(Idempotent-Replayed: true) instead of executing twice. No venue is contacted and no real order exists.",
    responses={
        400: {"description": "missing or malformed Idempotency-Key"},
        404: {"description": "opportunity not found"},
        409: {"description": "Idempotency-Key already used for another opportunity"},
    },
)
async def execute(
    opportunity_id: str,
    service: PaperServiceDep,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="8-128 chars of [A-Za-z0-9_.:-]"),
    ] = None,
    body: ExecuteRequest | None = None,
) -> ExecuteResponse:
    if idempotency_key is None or not re.fullmatch(IDEMPOTENCY_KEY_PATTERN, idempotency_key):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a valid Idempotency-Key header is required")
    request = body or ExecuteRequest()
    try:
        result = await service.execute(
            ExecutionRequest(
                opportunity_id=opportunity_id,
                idempotency_key=idempotency_key,
                quantity=request.quantity,
                scenario=request.scenario,
            )
        )
    except OpportunityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return ExecuteResponse(replayed=result.replayed, execution=result.execution)


@router.get("/executions", response_model=list[PaperExecution], summary="Paper executions (newest first)")
async def executions(container: ContainerDep) -> list[PaperExecution]:
    return await container.repo.list_executions()


@router.get("/orders", response_model=list[PaperOrder], summary="Simulated orders and their fills")
async def orders(container: ContainerDep) -> list[PaperOrder]:
    return await container.repo.list_orders()


@router.get("/positions", response_model=list[PaperPosition], summary="Paper positions marked at best bid")
async def positions(service: PaperServiceDep) -> list[PaperPosition]:
    portfolio = await service.portfolio()
    return list(portfolio.positions)


@router.get(
    "/portfolio",
    response_model=PaperPortfolio,
    summary="Paper portfolio",
    description="Per-venue cash (not nettable), positions, hedged bundles, realized/unrealized/locked-in P&L, "
    "residual exposure and kill-switch state.",
)
async def portfolio(service: PaperServiceDep) -> PaperPortfolio:
    return await service.portfolio()


@router.post("/kill-switch/reset", response_model=PaperPortfolio, summary="Reset the paper kill switch")
async def reset_kill_switch(service: PaperServiceDep) -> PaperPortfolio:
    return await service.reset_kill_switch()
