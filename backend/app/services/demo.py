"""Deterministic fixture demo (``make demo`` / ``python -m app.cli demo``).

Steps: reset the database, load fixtures through the real venue adapters, run the matching
pipeline (rejecting the intentionally mismatched pairs), build normalized books, detect
opportunities, paper-execute the validated ones (one normal fill, one second-leg partial
fill that exercises residual exposure and unwind), attempt a suppressed one (rejected),
and summarise the result. No network access and no credentials are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.config import DataMode, Settings
from app.domain.enums import ExecutionScenario, OpportunityStatus, StrategyType
from app.domain.models import ArbitrageOpportunity
from app.domain.paper import PaperExecution, PaperPortfolio
from app.services.container import AppContainer
from app.services.paper import ExecutionRequest, PaperService
from app.services.scan import ScanReport, run_scan


@dataclass
class DemoResult:
    scan: ScanReport
    executions: list[PaperExecution] = field(default_factory=list)
    portfolio: PaperPortfolio | None = None
    expected: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)


async def run_demo(settings: Settings) -> DemoResult:
    fixture_settings = settings.model_copy(update={"data_mode": DataMode.FIXTURE})
    container = AppContainer(fixture_settings, data_mode=DataMode.FIXTURE)
    try:
        await container.db.reset()
        venues = container.venues()
        try:
            scan = await run_scan(container, venues, kind="demo")
        finally:
            await venues.aclose()
        result = DemoResult(scan=scan)
        fixtures = container.fixtures
        result.expected = dict(fixtures.manifest.get("expected", {})) if fixtures else {}

        validated = sorted(
            (o for o in scan.opportunities if o.status is OpportunityStatus.VALIDATED),
            key=lambda o: (-o.expected_net_profit, o.id),
        )
        service = PaperService(container)
        scenarios = [ExecutionScenario.NORMAL, ExecutionScenario.SECOND_LEG_PARTIAL]
        for index, opportunity in enumerate(validated[: len(scenarios)]):
            response = await service.execute(
                ExecutionRequest(
                    opportunity_id=opportunity.id,
                    idempotency_key=f"demo-{index}-{opportunity.id}",
                    quantity=None,
                    scenario=scenarios[index],
                )
            )
            result.executions.append(response.execution)
        suppressed = _first(scan.opportunities, OpportunityStatus.SUPPRESSED)
        if suppressed is not None:
            response = await service.execute(
                ExecutionRequest(
                    opportunity_id=suppressed.id,
                    idempotency_key=f"demo-suppressed-{suppressed.id}",
                    quantity=Decimal(1),
                    scenario=ExecutionScenario.NORMAL,
                )
            )
            result.executions.append(response.execution)
        result.portfolio = await service.portfolio()
        result.checks = _acceptance(result)
        return result
    finally:
        await container.aclose()


def _first(
    opportunities: list[ArbitrageOpportunity], status: OpportunityStatus
) -> ArbitrageOpportunity | None:
    for opportunity in opportunities:
        if opportunity.status is status and opportunity.strategy_type is StrategyType.CROSS_VENUE_BINARY:
            return opportunity
    return None


def _acceptance(result: DemoResult) -> dict[str, bool]:
    pairs = {f"{p.kalshi_market_id}|{p.polymarket_market_id}": p for p in result.scan.pairs}
    expected_approved = result.expected.get("approved_pairs", {})
    expected_rejected = result.expected.get("rejected_pairs", {})
    statuses = [
        o.status for o in result.scan.opportunities if o.strategy_type is StrategyType.CROSS_VENUE_BINARY
    ]
    return {
        "valid_pair_approved": any(p.approved_for_arbitrage_calculation for p in result.scan.pairs),
        "expected_approvals_match": all(
            key in pairs
            and pairs[key].approved_for_arbitrage_calculation
            and pairs[key].relation.value == rel
            for key, rel in expected_approved.items()
        ),
        "mismatched_pairs_rejected": all(
            key in pairs
            and not pairs[key].approved_for_arbitrage_calculation
            and pairs[key].relation.value == rel
            for key, rel in expected_rejected.items()
        ),
        "profitable_opportunity_found": OpportunityStatus.VALIDATED in statuses,
        "non_profitable_case_found": OpportunityStatus.NOT_PROFITABLE in statuses,
        "suppressed_case_found": OpportunityStatus.SUPPRESSED in statuses,
        "two_leg_fill_simulated": any(e.outcome.value == "hedged" for e in result.executions),
        "leg_risk_simulated": any(
            e.residual_quantity > 0 or e.outcome.value.startswith("partially") for e in result.executions
        ),
    }
