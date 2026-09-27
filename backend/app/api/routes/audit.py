"""Audit log endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ContainerDep
from app.api.schemas import AuditEvent

router = APIRouter(prefix="/api", tags=["audit"])


@router.get(
    "/audit",
    response_model=list[AuditEvent],
    summary="Audit log (newest first)",
    description="Pair decisions, opportunities, scans, paper executions (with the full trail: why the pair was "
    "approved, books used, timestamps, requested quantity, fills, fees, residual exposure, result) and "
    "kill-switch events.",
)
async def audit(
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=5000)] = 200,
    category: Annotated[str | None, Query(pattern="^[a-z_]{1,40}$")] = None,
) -> list[AuditEvent]:
    rows = await container.repo.list_audit(limit=limit, category=category)
    return [
        AuditEvent(
            id=row.id,
            ts=row.ts,
            category=row.category,
            message=row.message,
            data_source=row.data_source,
            correlation_id=row.correlation_id,
            pair_id=row.pair_id,
            opportunity_id=row.opportunity_id,
            execution_id=row.execution_id,
            paper_order_id=row.paper_order_id,
            payload=row.payload,
        )
        for row in rows
    ]
