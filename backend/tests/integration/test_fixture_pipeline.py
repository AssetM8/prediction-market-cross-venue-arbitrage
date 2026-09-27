"""Fixture ingestion -> matching -> books -> opportunities -> persistence -> paper execution audit."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.enums import (
    DataSource,
    ExecutionOutcome,
    ExecutionScenario,
    OpportunityStatus,
    StrategyType,
    Venue,
)
from app.persistence.repository import ledger_from_dict, ledger_to_dict
from app.services.container import AppContainer
from app.services.paper import (
    ExecutionRequest,
    IdempotencyConflictError,
    OpportunityNotFoundError,
    PaperService,
)
from app.services.scan import ScanReport, run_scan

D = Decimal


@pytest.fixture
async def scanned(container: AppContainer) -> ScanReport:
    venues = container.venues()
    try:
        return await run_scan(container, venues, kind="test")
    finally:
        await venues.aclose()


def _cross(report: ScanReport) -> dict[str, list]:  # type: ignore[type-arg]
    result: dict[str, list] = {}  # type: ignore[type-arg]
    for opp in report.opportunities:
        if opp.strategy_type is StrategyType.CROSS_VENUE_BINARY:
            result.setdefault(opp.pair_id or "", []).append(opp)
    return result


async def test_scan_detects_expected_opportunities(scanned: ScanReport) -> None:
    assert scanned.status == "completed"
    assert scanned.data_source is DataSource.FIXTURE
    validated = [o for o in scanned.opportunities if o.status is OpportunityStatus.VALIDATED]
    assert {o.direction[:2] for o in validated} == {"A:", "C:"}
    assert all(o.strategy_type is StrategyType.CROSS_VENUE_BINARY for o in validated)
    fed = next(o for o in validated if o.direction.startswith("A:"))
    assert fed.max_executable_quantity == D(150)
    assert fed.legs[0].venue is Venue.KALSHI and fed.legs[0].vwap == D("0.40")
    assert fed.legs[1].vwap == D("0.524")  # (120 @ 0.52 + 30 @ 0.54) / 150
    assert fed.expected_net_profit > D(4) and fed.slippage == D("0.6")
    statuses = {o.status for o in scanned.opportunities}
    assert {
        OpportunityStatus.NOT_PROFITABLE,
        OpportunityStatus.SUPPRESSED,
        OpportunityStatus.CANDIDATE,
    } <= statuses
    stale = [o for o in scanned.opportunities if o.status is OpportunityStatus.SUPPRESSED]
    assert all("stale_book" in (o.rejection_reason or "") for o in stale)
    kinds = {o.strategy_type for o in scanned.opportunities}
    assert {
        StrategyType.MARKET_REBALANCING_LONG,
        StrategyType.MARKET_REBALANCING_SHORT,
        StrategyType.COMBINATORIAL,
    } <= kinds
    rebalancing = [
        o for o in scanned.opportunities if o.strategy_type is StrategyType.MARKET_REBALANCING_LONG
    ]
    assert all(o.status is not OpportunityStatus.VALIDATED for o in rebalancing)


async def test_results_are_persisted(container: AppContainer, scanned: ScanReport) -> None:
    repo = container.repo
    assert len(await repo.list_pairs()) == len(scanned.pairs)
    run = await repo.latest_run()
    assert run is not None and run.id == scanned.run_id
    stored = await repo.list_opportunities(run.id)
    assert {o.id for o in stored} == {o.id for o in scanned.opportunities}
    edges = await repo.list_edges()
    assert {e["relation"] for e in edges} >= {
        "EQUIVALENT",
        "COMPLEMENTARY",
        "A_IMPLIES_B",
        "MUTUALLY_EXCLUSIVE",
    }
    books = await repo.books_for_market("kalshi", "KXFEDDECISION-26DEC-C25")
    assert len(books) == 2
    market = await repo.get_market("kalshi:KXFEDDECISION-26DEC-C25")
    assert (
        market is not None and market.fee_metadata.source == "documented_metadata"
    )  # enriched version stored
    markets = await repo.list_markets()
    assert sum(1 for m in markets if not m.eligible) == 5
    audit = await repo.list_audit(limit=500)
    assert {row.category for row in audit} >= {"pair_decision", "opportunity", "scan"}


async def test_paper_execution_audit_trail_and_idempotency(
    container: AppContainer, scanned: ScanReport
) -> None:
    service = PaperService(container)
    fed = next(
        o
        for o in scanned.opportunities
        if o.status is OpportunityStatus.VALIDATED and o.direction.startswith("A:")
    )
    request = ExecutionRequest(
        opportunity_id=fed.id,
        idempotency_key="test-key-0001",
        quantity=None,
        scenario=ExecutionScenario.NORMAL,
    )
    first = await service.execute(request)
    assert first.execution.outcome is ExecutionOutcome.HEDGED and not first.replayed
    replay = await service.execute(request)
    assert replay.replayed and replay.execution.id == first.execution.id
    assert len(await container.repo.list_executions()) == 1
    with pytest.raises(IdempotencyConflictError):
        other = next(o for o in scanned.opportunities if o.id != fed.id)
        await service.execute(
            ExecutionRequest(
                opportunity_id=other.id,
                idempotency_key="test-key-0001",
                quantity=None,
                scenario=ExecutionScenario.NORMAL,
            )
        )
    with pytest.raises(OpportunityNotFoundError):
        await service.execute(
            ExecutionRequest(
                opportunity_id="opp_missing",
                idempotency_key="test-key-0002",
                quantity=None,
                scenario=ExecutionScenario.NORMAL,
            )
        )
    audit = list(await container.repo.list_audit(limit=50, category="paper_execution"))
    assert len(audit) == 1
    payload = audit[0].payload
    assert payload["paper_trading_only"] is True
    assert payload["pair"]["relation"] == "EQUIVALENT" and payload["pair"]["why_equivalent"]
    assert payload["pair"]["passed_checks"]
    assert len(payload["books_used"]) == 2 and all(b["hash"] for b in payload["books_used"])
    assert D(payload["requested_quantity"]) == 150
    assert [o["status"] for o in payload["orders"]] == ["filled", "filled"]
    assert all(o["fills"] for o in payload["orders"])
    assert D(payload["total_fees"]) > 0 and D(payload["residual_quantity"]) == 0
    assert payload["outcome"] == "hedged"
    assert payload["steps"][0]["message"] == "execution requested"
    orders = await container.repo.list_orders()
    assert len(orders) == 2 and all(order.simulated for order in orders)
    assert len(await container.repo.list_fills()) >= 2
    portfolio = await service.portfolio()
    assert portfolio.locked_in_pnl > 0 and len(portfolio.hedged_bundles) == 1


async def test_partial_fill_leaves_audited_residual(container: AppContainer, scanned: ScanReport) -> None:
    service = PaperService(container)
    cpi = next(
        o
        for o in scanned.opportunities
        if o.status is OpportunityStatus.VALIDATED and o.direction.startswith("C:")
    )
    response = await service.execute(
        ExecutionRequest(
            opportunity_id=cpi.id,
            idempotency_key="partial-0001",
            quantity=D(300),
            scenario=ExecutionScenario.SECOND_LEG_PARTIAL,
        )
    )
    execution = response.execution
    assert execution.hedged_quantity == D(120)
    assert execution.outcome in (
        ExecutionOutcome.PARTIALLY_HEDGED_UNWOUND,
        ExecutionOutcome.UNHEDGED_RESIDUAL,
    )
    assert any(order.purpose == "unwind" for order in execution.orders)
    audit = await container.repo.list_audit(limit=5, category="paper_execution")
    assert D(audit[0].payload["residual_quantity"]) == execution.residual_quantity


async def test_suppressed_opportunity_cannot_execute(container: AppContainer, scanned: ScanReport) -> None:
    stale = next(o for o in scanned.opportunities if o.status is OpportunityStatus.SUPPRESSED)
    response = await PaperService(container).execute(
        ExecutionRequest(
            opportunity_id=stale.id,
            idempotency_key="stale-0001",
            quantity=D(1),
            scenario=ExecutionScenario.NORMAL,
        )
    )
    assert response.execution.outcome is ExecutionOutcome.REJECTED
    assert response.execution.orders == ()


async def test_kill_switch_state_persists(container: AppContainer) -> None:
    service = PaperService(container)
    ledger = await container.repo.load_ledger(container.starting_cash())
    ledger.kill_switch_engaged = True
    ledger.consecutive_failures = 3
    restored = ledger_from_dict(ledger_to_dict(ledger))
    assert restored.kill_switch_engaged and restored.consecutive_failures == 3
    await container.repo.save_ledger(ledger, container.clock.now())
    assert (await service.portfolio()).kill_switch_engaged
    assert not (await service.reset_kill_switch()).kill_switch_engaged
