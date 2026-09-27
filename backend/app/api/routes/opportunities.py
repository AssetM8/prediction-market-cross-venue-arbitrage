"""Opportunity endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ContainerDep
from app.api.schemas import OpportunityDetail
from app.domain.enums import OpportunityStatus, StrategyType
from app.domain.models import ArbitrageOpportunity

router = APIRouter(prefix="/api", tags=["opportunities"])


@router.get(
    "/opportunities",
    response_model=list[ArbitrageOpportunity],
    summary="Opportunities (validated, candidate, not profitable, suppressed)",
    description="Depth-walked, fee- and buffer-adjusted opportunities from the latest scan (run=all for "
    "history). Filters: status, strategy, min_profit (expected net profit), freshness (fresh/stale against "
    "the service clock), pair_id.",
)
async def opportunities(
    container: ContainerDep,
    status_filter: Annotated[list[OpportunityStatus] | None, Query(alias="status")] = None,
    strategy: Annotated[list[StrategyType] | None, Query()] = None,
    min_profit: Annotated[Decimal | None, Query()] = None,
    freshness: Annotated[str | None, Query(pattern="^(fresh|stale)$")] = None,
    pair_id: Annotated[str | None, Query()] = None,
    run: Annotated[str, Query(pattern="^(latest|all)$")] = "latest",
) -> list[ArbitrageOpportunity]:
    run_id = None
    if run == "latest":
        latest = await container.repo.latest_run()
        if latest is None:
            return []
        run_id = latest.id
    now = container.clock.now()
    result = []
    for opp in await container.repo.list_opportunities(run_id):
        if status_filter and opp.status not in status_filter:
            continue
        if strategy and opp.strategy_type not in strategy:
            continue
        if min_profit is not None and opp.expected_net_profit < min_profit:
            continue
        fresh = now <= opp.stale_after
        if freshness == "fresh" and not fresh:
            continue
        if freshness == "stale" and fresh:
            continue
        if pair_id and opp.pair_id != pair_id:
            continue
        result.append(opp)
    order = {
        OpportunityStatus.VALIDATED: 0,
        OpportunityStatus.CANDIDATE: 1,
        OpportunityStatus.SUPPRESSED: 2,
        OpportunityStatus.NOT_PROFITABLE: 3,
    }
    result.sort(key=lambda o: (order[o.status], -o.expected_net_profit, o.id))
    return result


@router.get(
    "/opportunities/{opportunity_id}",
    response_model=OpportunityDetail,
    summary="Opportunity detail",
    description="The opportunity with its pair, the stored order books for both legs, freshness against the "
    "service clock and whether it can be paper-executed now.",
    responses={404: {"description": "not found"}},
)
async def opportunity(opportunity_id: str, container: ContainerDep) -> OpportunityDetail:
    found = await container.repo.get_opportunity(opportunity_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    opp, run_id = found
    pair = await container.repo.get_pair(opp.pair_id) if opp.pair_id else None
    books = []
    for leg in opp.legs:
        for book in await container.repo.books_for_market(leg.venue.value, leg.market_id):
            if book.outcome_id == leg.outcome_id:
                books.append(book)
    fresh = container.clock.now() <= opp.stale_after
    return OpportunityDetail(
        opportunity=opp,
        run_id=run_id,
        fresh=fresh,
        executable=fresh
        and opp.status is OpportunityStatus.VALIDATED
        and opp.strategy_type is StrategyType.CROSS_VENUE_BINARY,
        pair=pair,
        books=books,
    )
