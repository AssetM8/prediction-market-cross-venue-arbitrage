"""Market, pair, relationship and order-book endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.api.deps import ContainerDep
from app.api.schemas import IDEMPOTENCY_KEY_PATTERN, MarketView, RefreshResponse, RelationshipGraph
from app.core.ids import content_hash
from app.domain.enums import CheckStatus, Relation, Venue
from app.domain.models import MarketPair, NormalizedMarket, NormalizedOrderBook
from app.services.scan import run_scan

router = APIRouter(prefix="/api", tags=["markets"])


@router.get(
    "/markets",
    response_model=list[MarketView],
    summary="Normalized markets",
    description="Markets from the latest sync with Stage-1 eligibility verdicts. Each market carries "
    "data_source (live or fixture).",
)
async def markets(
    container: ContainerDep,
    venue: Annotated[Venue | None, Query()] = None,
    eligible: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[MarketView]:
    rows = await container.repo.list_markets(venue.value if venue else None)
    views = [
        MarketView(
            market=NormalizedMarket.model_validate(row.payload),
            eligible=row.eligible,
            eligibility_reasons=list(row.eligibility_reasons),
        )
        for row in rows
        if eligible is None or row.eligible == eligible
    ]
    return views[:limit]


def _both_active(pair: MarketPair) -> bool:
    return any(c.name == "market_status" and c.status is CheckStatus.PASS for c in pair.deterministic_checks)


@router.get(
    "/market-pairs",
    response_model=list[MarketPair],
    summary="Candidate market pairs",
    description="Every evaluated Kalshi/Polymarket pair with its relation, confidence, deterministic checks, "
    "semantic judgement and blocking mismatches. Filters: relation, approved, min_confidence, venue_status.",
)
async def market_pairs(
    container: ContainerDep,
    relation: Annotated[list[Relation] | None, Query()] = None,
    approved: Annotated[bool | None, Query()] = None,
    min_confidence: Annotated[Decimal | None, Query(ge=0, le=1)] = None,
    venue_status: Annotated[str | None, Query(pattern="^(active|inactive)$")] = None,
) -> list[MarketPair]:
    pairs = await container.repo.list_pairs()
    result = []
    for pair in pairs:
        if relation and pair.relation not in relation:
            continue
        if approved is not None and pair.approved_for_arbitrage_calculation != approved:
            continue
        if min_confidence is not None and pair.confidence < min_confidence:
            continue
        if venue_status == "active" and not _both_active(pair):
            continue
        if venue_status == "inactive" and _both_active(pair):
            continue
        result.append(pair)
    result.sort(key=lambda p: (not p.approved_for_arbitrage_calculation, -p.confidence, p.id))
    return result


@router.get("/market-pairs/{pair_id}", response_model=MarketPair, summary="One market pair")
async def market_pair(pair_id: str, container: ContainerDep) -> MarketPair:
    pair = await container.repo.get_pair(pair_id)
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "pair not found")
    return pair


@router.post(
    "/market-pairs/refresh",
    response_model=RefreshResponse,
    summary="Re-run discovery, matching and opportunity detection",
    description="Runs a read-only scan using the configured DATA_MODE (fixture or live). Mutates only local "
    "state. An optional Idempotency-Key makes retries return the first result; concurrent refreshes get 409.",
    responses={409: {"description": "a refresh is already running"}},
)
async def refresh(
    container: ContainerDep,
    response: Response,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", pattern=IDEMPOTENCY_KEY_PATTERN)
    ] = None,
) -> RefreshResponse:
    scope = "refresh"
    if idempotency_key:
        stored = await container.repo.get_idempotency(f"{scope}:{idempotency_key}")
        if stored is not None:
            response.headers["Idempotent-Replayed"] = "true"
            return RefreshResponse.model_validate(stored.response)
    if container.refresh_lock.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "a refresh is already running")
    async with container.refresh_lock:
        venues = container.venues()
        try:
            report = await run_scan(container, venues, kind="refresh")
        finally:
            await venues.aclose()
    body = RefreshResponse(data_mode=container.data_mode.value, summary=report.summary())
    if idempotency_key:
        await container.repo.save_idempotency(
            f"{scope}:{idempotency_key}",
            scope=scope,
            request_hash=content_hash({}),
            status_code=200,
            response=body.model_dump(mode="json"),
            now=container.clock.now(),
        )
    return body


@router.get(
    "/relationships",
    response_model=RelationshipGraph,
    summary="Logical relationship graph",
    description="Nodes are markets, edges are determined relations (equivalent, complementary, implications, "
    "mutually exclusive, partial overlap) with the two-leg constructions whose minimum payout is verified by "
    "joint-state enumeration. Only approved equivalence/complement edges are executable in the MVP.",
)
async def relationships(container: ContainerDep) -> RelationshipGraph:
    edges = await container.repo.list_edges()
    nodes: dict[str, dict[str, str]] = {}
    for edge in edges:
        nodes[edge["source"]] = {
            "id": edge["source"],
            "venue": "kalshi",
            "title": edge.get("source_title", ""),
        }
        nodes[edge["target"]] = {
            "id": edge["target"],
            "venue": "polymarket",
            "title": edge.get("target_title", ""),
        }
    return RelationshipGraph(nodes=sorted(nodes.values(), key=lambda n: n["id"]), edges=edges)


@router.get(
    "/books/{venue}/{market_id}",
    response_model=list[NormalizedOrderBook],
    summary="Stored order books for a market",
    description="Latest normalized YES and NO books (bids best-first, asks best-first). Kalshi asks are "
    "derived from the opposite side's bids and flagged derived_asks=true.",
)
async def books(venue: Venue, market_id: str, container: ContainerDep) -> list[NormalizedOrderBook]:
    result = await container.repo.books_for_market(venue.value, market_id)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no stored books for this market")
    return sorted(result, key=lambda b: b.outcome_side.value, reverse=True)
