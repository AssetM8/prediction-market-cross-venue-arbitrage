"""Optional: the same pipeline against PostgreSQL. Runs only when TEST_POSTGRES_URL is set,
e.g. TEST_POSTGRES_URL=postgresql+asyncpg://arb:arb@127.0.0.1:5432/arb."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import DataMode
from app.domain.enums import ExecutionOutcome, ExecutionScenario, OpportunityStatus
from app.services.container import AppContainer
from app.services.paper import ExecutionRequest, PaperService
from app.services.scan import run_scan
from tests.conftest import make_settings

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL not set")


async def test_scan_and_paper_execution_on_postgres(tmp_path: Path) -> None:
    container = AppContainer(make_settings(tmp_path, database_url=POSTGRES_URL), data_mode=DataMode.FIXTURE)
    await container.db.reset()
    venues = container.venues()
    try:
        report = await run_scan(container, venues, kind="postgres-test")
        validated = [o for o in report.opportunities if o.status is OpportunityStatus.VALIDATED]
        assert len(validated) == 2
        response = await PaperService(container).execute(
            ExecutionRequest(
                opportunity_id=validated[0].id,
                idempotency_key="pg-key-0001",
                quantity=None,
                scenario=ExecutionScenario.NORMAL,
            )
        )
        assert response.execution.outcome is ExecutionOutcome.HEDGED
        stored = await container.repo.get_execution_by_key("pg-key-0001")
        assert stored is not None and stored.id == response.execution.id
        run = await container.repo.latest_run()
        assert run is not None and run.id == report.run_id
        assert run.started_at.tzinfo is not None
    finally:
        await venues.aclose()
        await container.db.reset()
        await container.aclose()
