"""System endpoints: health, venues, public configuration, metrics."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import ContainerDep
from app.api.schemas import HealthResponse, VenuesResponse
from app.core.metrics import METRICS

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Database status, data mode, simulated-clock flag, last scan summary and last known venue "
    "health. Always reports paper_trading_only=true.",
)
async def health(container: ContainerDep) -> HealthResponse:
    try:
        db_ok = await container.db.ping()
    except SQLAlchemyError:
        db_ok = False
    run = await container.repo.latest_run() if db_ok else None
    venues = await container.repo.list_health() if db_ok else []
    status = "ok"
    if not db_ok:
        status = "error"
    elif any(not v.reachable for v in venues):
        status = "degraded"
    return HealthResponse(
        status=status,
        data_mode=container.data_mode.value,
        simulated_clock=container.clock.simulated,
        now=container.clock.now(),
        database="ok" if db_ok else "error",
        last_scan=run.summary if run else None,
        venues=venues,
    )


@router.get(
    "/api/venues",
    response_model=VenuesResponse,
    summary="Venue connection state",
    description="Last recorded health per venue. Pass check=true to run a fresh read-only health check.",
)
async def venues(
    container: ContainerDep,
    check: Annotated[bool, Query(description="run a fresh health check now")] = False,
) -> VenuesResponse:
    if check:
        bundle = container.venues()
        try:
            results = [await bundle.kalshi.healthcheck(), await bundle.polymarket.healthcheck()]
        finally:
            await bundle.aclose()
        await container.repo.save_health(results)
    return VenuesResponse(data_mode=container.data_mode.value, venues=await container.repo.list_health())


@router.get(
    "/api/config/public",
    summary="Public configuration",
    description="Allow-listed, non-secret settings. Secrets (e.g. OPTIONAL_LLM_API_KEY) are never returned; "
    "only whether one is configured.",
)
async def public_config(container: ContainerDep) -> dict[str, Any]:
    return {
        **container.settings.public_view(),
        "data_mode": container.data_mode.value,
        "simulated_clock": container.clock.simulated,
    }


@router.get(
    "/metrics",
    summary="In-process metrics",
    description="Counters, gauges and timestamps (requests, retries, stale books, opportunity counts, paper "
    "execution outcomes). format=prometheus returns Prometheus text exposition.",
    response_model=None,
)
async def metrics(
    format: Annotated[str, Query(pattern="^(json|prometheus)$")] = "json",
) -> Response | dict[str, Any]:
    if format == "prometheus":
        return PlainTextResponse(METRICS.prometheus_text())
    return METRICS.snapshot()
