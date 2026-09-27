"""Live-mode scan behaviour when venues are unreachable, the CLI, and the optional LLM veto."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.core.config import DataMode, get_settings
from app.domain.enums import DataSource, Relation, Venue
from app.domain.models import NormalizedMarket, SemanticJudgement
from app.matching.adjudicator import AnthropicAdjudicator
from app.matching.embeddings import HashingEmbeddingProvider
from app.matching.pipeline import MatchingConfig, MatchingPipeline
from app.services.container import AppContainer
from app.services.scan import run_scan
from tests.conftest import NOW, make_market, make_settings

D = Decimal
KALSHI = "https://kalshi.invalid/trade-api/v2"
GAMMA = "https://gamma.invalid"
CLOB = "https://clob.invalid"


@respx.mock
async def test_live_scan_reports_unreachable_venues_truthfully(tmp_path: Path) -> None:
    respx.route(host__in=["kalshi.invalid", "gamma.invalid", "clob.invalid"]).mock(
        return_value=httpx.Response(403)
    )
    settings = make_settings(
        tmp_path,
        data_mode=DataMode.LIVE,
        kalshi_base_url=KALSHI,
        polymarket_gamma_url=GAMMA,
        polymarket_clob_url=CLOB,
        http_max_attempts=1,
    )
    container = AppContainer(settings)
    await container.db.reset()
    venues = container.venues()
    try:
        report = await run_scan(container, venues, kind="scan")
    finally:
        await venues.aclose()
        await container.aclose()
    assert report.data_source is DataSource.LIVE
    assert report.pairs == [] and report.opportunities == []
    assert any("kalshi" in error and "403" in error for error in report.errors)
    assert any("polymarket" in error for error in report.errors)
    assert report.status == "completed_with_errors"
    assert not any(h.reachable for h in report.health)


def _cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("LOG_JSON", "true")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_cli_demo_scan_execute_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_env(tmp_path, monkeypatch)
    runner = CliRunner()
    demo = runner.invoke(cli_app, ["demo"])
    assert demo.exit_code == 0, demo.output
    assert "PAPER TRADING ONLY" in demo.output
    assert "[FAIL]" not in demo.output and "[PASS] two_leg_fill_simulated" in demo.output

    scan = runner.invoke(cli_app, ["scan", "--fixture"])
    assert scan.exit_code == 0, scan.output
    assert "validated paper opportunit" in scan.output
    opp_id = next(line.split("id=")[1].split()[0] for line in scan.output.splitlines() if "id=opp_" in line)

    executed = runner.invoke(cli_app, ["paper-execute", "--opportunity-id", opp_id, "--quantity", "5"])
    assert executed.exit_code == 0, executed.output
    assert '"simulated": true' in executed.output

    missing = runner.invoke(cli_app, ["paper-execute", "--opportunity-id", "opp_nope"])
    assert missing.exit_code == 1

    output = tmp_path / "audit.jsonl"
    exported = runner.invoke(cli_app, ["export-audit", "--output", str(output)])
    assert exported.exit_code == 0
    lines = [json.loads(line) for line in output.read_text().splitlines()]
    assert any(line["category"] == "paper_execution" for line in lines)

    matched = runner.invoke(cli_app, ["match-markets", "--fixture"])
    assert matched.exit_code == 0 and "EQUIVALENT" in matched.output
    synced = runner.invoke(cli_app, ["sync-markets", "--fixture"])
    assert "stored 15 Kalshi and 17 Polymarket markets (fixture data)" in synced.output
    health = runner.invoke(cli_app, ["health", "--fixture"])
    assert health.exit_code == 0 and '"paper_trading_only": true' in health.output
    assert runner.invoke(cli_app, ["kill-switch-reset"]).exit_code == 0
    get_settings.cache_clear()


def test_cli_live_scan_exit_code_when_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_env(
        tmp_path,
        monkeypatch,
        KALSHI_BASE_URL=KALSHI,
        POLYMARKET_GAMMA_URL=GAMMA,
        POLYMARKET_CLOB_URL=CLOB,
        HTTP_MAX_ATTEMPTS="1",
    )
    with respx.mock:
        respx.route().mock(side_effect=httpx.ConnectError("blocked"))
        result = CliRunner().invoke(cli_app, ["scan", "--live"])
    assert result.exit_code == 3
    assert "UNREACHABLE" in result.output
    assert "could not evaluate - venue data unavailable" in result.output
    get_settings.cache_clear()


def test_paper_trading_cannot_be_disabled(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="PAPER_TRADING_ONLY=false is not supported"):
        make_settings(tmp_path, paper_trading_only=False)


# ---- optional LLM adjudicator: veto only -------------------------------------------------------

RULES = (
    "Yes if the U.S. unemployment rate (seasonally adjusted) for October 2026, as first reported by the Bureau of "
    "Labor Statistics, is above 4.5%. The rate is reported to one decimal place. If the release is delayed, the "
    "market will remain open."
)


def _markets() -> tuple[NormalizedMarket, NormalizedMarket]:
    return (
        make_market(
            "K1",
            Venue.KALSHI,
            title="October 2026 unemployment above 4.5%?",
            rules=RULES,
            category="Economics",
        ),
        make_market(
            "P1",
            Venue.POLYMARKET,
            title="October 2026 unemployment above 4.5%?",
            rules=RULES,
            category="Economics",
        ),
    )


class FakeAdjudicator:
    name = "fake"

    def __init__(self, judgement: SemanticJudgement | None) -> None:
        self.judgement = judgement
        self.calls = 0

    async def adjudicate(
        self, kalshi: NormalizedMarket, polymarket: NormalizedMarket
    ) -> SemanticJudgement | None:
        self.calls += 1
        return self.judgement


CONFIG = MatchingConfig(
    min_confidence=D("0.9"),
    min_similarity=D("0.3"),
    max_candidates_per_market=5,
    candidate_time_window=timedelta(days=400),
    max_resolution_gap=timedelta(hours=72),
)


def judgement(relation: Relation, safe: bool = True) -> SemanticJudgement:
    return SemanticJudgement(
        relation=relation,
        confidence=D("0.9"),
        shared_event=True,
        same_resolution_criteria=safe,
        safe_for_cross_venue_arbitrage=safe,
        reason="llm",
    )


@pytest.mark.parametrize(
    ("llm", "approved"),
    [
        (judgement(Relation.EQUIVALENT), True),
        (judgement(Relation.A_IMPLIES_B), False),
        (judgement(Relation.EQUIVALENT, safe=False), False),
        (None, False),
    ],
)
async def test_llm_can_only_veto(llm: SemanticJudgement | None, approved: bool) -> None:
    fake = FakeAdjudicator(llm)
    pipeline = MatchingPipeline(CONFIG, embedder=HashingEmbeddingProvider(), adjudicator=fake)
    k, p = _markets()
    report = await pipeline.run([k], [p], now=NOW, data_source=DataSource.FIXTURE)
    pair = report.pairs[0]
    assert fake.calls == 1
    assert pair.approved_for_arbitrage_calculation is approved
    assert pair.adjudication_source.value == "deterministic_with_llm_veto"
    if not approved:
        assert pair.relation is Relation.AMBIGUOUS
        assert any(m.startswith("llm_veto") for m in pair.blocking_mismatches)


async def test_llm_is_not_consulted_for_rejected_pairs() -> None:
    fake = FakeAdjudicator(judgement(Relation.EQUIVALENT))
    pipeline = MatchingPipeline(CONFIG, embedder=HashingEmbeddingProvider(), adjudicator=fake)
    k, p = _markets()
    p = p.model_copy(update={"rules": RULES.replace("one decimal place", "two decimal places")})
    report = await pipeline.run([k], [p], now=NOW, data_source=DataSource.FIXTURE)
    assert fake.calls == 0 and not report.pairs[0].approved_for_arbitrage_calculation


@respx.mock
async def test_anthropic_adjudicator_request_and_parsing() -> None:
    body: dict[str, Any] = {}

    def reply(request: httpx.Request) -> httpx.Response:
        body.update(json.loads(request.content))
        assert request.headers["x-api-key"] == "test-key"
        text = json.dumps(judgement(Relation.EQUIVALENT).model_dump(mode="json"))
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})

    respx.post("https://llm.invalid/v1/messages").mock(side_effect=reply)
    adjudicator = AnthropicAdjudicator(
        api_key="test-key", model="model-x", base_url="https://llm.invalid", timeout_seconds=5
    )
    k, p = _markets()
    result = await adjudicator.adjudicate(k, p)
    await adjudicator.aclose()
    assert result is not None and result.relation is Relation.EQUIVALENT
    assert body["model"] == "model-x" and body["temperature"] == 0
    assert "untrusted data" in body["system"]


@respx.mock
async def test_anthropic_adjudicator_failure_returns_none() -> None:
    respx.post("https://llm.invalid/v1/messages").mock(return_value=httpx.Response(500))
    adjudicator = AnthropicAdjudicator(
        api_key="k", model="m", base_url="https://llm.invalid", timeout_seconds=5
    )
    k, p = _markets()
    assert await adjudicator.adjudicate(k, p) is None
    await adjudicator.aclose()
    with pytest.raises(ValueError, match="required"):
        AnthropicAdjudicator(api_key="", model="m", base_url="https://llm.invalid", timeout_seconds=5)
