"""API request/response schemas not already covered by domain models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import ExecutionScenario
from app.domain.interfaces import VenueHealth
from app.domain.models import ArbitrageOpportunity, MarketPair, NormalizedMarket, NormalizedOrderBook
from app.domain.paper import PaperExecution

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:\-]{8,128}$"
MAX_REQUEST_QUANTITY = Decimal("1000000")


class HealthResponse(BaseModel):
    status: str = Field(description="ok, degraded (a venue unreachable) or error")
    paper_trading_only: bool = True
    data_mode: str
    simulated_clock: bool
    now: datetime
    database: str
    last_scan: dict[str, Any] | None
    venues: list[VenueHealth]


class VenuesResponse(BaseModel):
    data_mode: str
    venues: list[VenueHealth]


class MarketView(BaseModel):
    market: NormalizedMarket
    eligible: bool
    eligibility_reasons: list[str]


class OpportunityDetail(BaseModel):
    opportunity: ArbitrageOpportunity
    run_id: str
    fresh: bool
    executable: bool
    pair: MarketPair | None
    books: list[NormalizedOrderBook]


class ExecuteRequest(BaseModel):
    """Paper execution parameters. Quantity defaults to the maximum executable quantity."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"quantity": "100", "scenario": "normal"}]},
    )

    quantity: Decimal | None = Field(default=None, description="contracts per leg (decimal string)")
    scenario: ExecutionScenario = Field(
        default=ExecutionScenario.NORMAL,
        description="deterministic fault injection for the simulator (normal = both legs fill if possible)",
    )

    @field_validator("quantity")
    @classmethod
    def _positive(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite() or value <= 0 or value > MAX_REQUEST_QUANTITY:
            raise ValueError(f"quantity must be a finite decimal in (0, {MAX_REQUEST_QUANTITY}]")
        return value


class ExecuteResponse(BaseModel):
    paper_trading_only: bool = True
    replayed: bool
    execution: PaperExecution


class RefreshResponse(BaseModel):
    paper_trading_only: bool = True
    data_mode: str
    summary: dict[str, Any]


class AuditEvent(BaseModel):
    id: int
    ts: datetime
    category: str
    message: str
    data_source: str
    correlation_id: str | None
    pair_id: str | None
    opportunity_id: str | None
    execution_id: str | None
    paper_order_id: str | None
    payload: dict[str, Any]


class RelationshipGraph(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ErrorResponse(BaseModel):
    detail: str
