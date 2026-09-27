"""FastAPI application factory.

``uvicorn app.main:app`` serves the API. Paper trading only: the application has no route,
setting or dependency capable of placing a real order.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit, markets, opportunities, paper, system
from app.core.config import DataMode, Settings, get_settings
from app.core.logging import configure_logging, get_logger, log_context, log_event, new_correlation_id
from app.core.metrics import METRICS
from app.services.container import AppContainer
from app.services.scan import run_scan

logger = get_logger(__name__)

DESCRIPTION = """
Read-only cross-venue prediction-market arbitrage research demo (Kalshi x Polymarket),
derived from *Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets*
(arXiv:2508.03474).

**Paper trading only.** Market data is read from public endpoints (or checked-in fixtures);
execution is simulated locally. No endpoint can place, sign or cancel a real order.
Contract equivalence cannot be inferred from titles alone; venue settlement rules may
still differ (basis risk). Not financial advice.
"""


async def _auto_scan(container: AppContainer) -> None:
    if container.data_mode is not DataMode.FIXTURE or not container.settings.auto_scan_on_startup:
        return
    if await container.repo.latest_run() is not None:
        return
    venues = container.venues()
    try:
        await run_scan(container, venues, kind="startup")
    finally:
        await venues.aclose()


def create_app(settings: Settings | None = None, container: AppContainer | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        active = container or AppContainer(settings)
        await active.start()
        await _auto_scan(active)
        app.state.container = active
        log_event(
            logger, logging.INFO, "api started", data_mode=active.data_mode.value, paper_trading_only=True
        )
        try:
            yield
        finally:
            if owned:
                await active.aclose()

    app = FastAPI(
        title="prediction-market-cross-venue-arbitrage",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "Idempotent-Replayed"],
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        incoming = request.headers.get("X-Correlation-ID", "")
        correlation_id = incoming if incoming.isalnum() and len(incoming) <= 64 else new_correlation_id()
        started = time.perf_counter()
        with log_context(correlation_id=correlation_id):
            response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            METRICS.inc(
                "api_requests_total",
                path=request.url.path.split("/")[1] or "root",
                status=str(response.status_code),
            )
            log_event(
                logger,
                logging.INFO,
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                elapsed_ms=elapsed_ms,
            )
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Paper-Trading-Only"] = "true"
        return response

    for module in (system, markets, opportunities, paper, audit):
        app.include_router(module.router)
    return app


app = create_app()
