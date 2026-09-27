"""Relationship classification and mismatch rejection on the fixture set and handcrafted pairs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core.clock import FrozenClock
from app.domain.enums import CheckStatus, DataSource, MarketStatus, OutcomeSide, Relation, Venue
from app.matching.embeddings import HashingEmbeddingProvider
from app.matching.extract import extract_proposition
from app.matching.pipeline import MatchingConfig, MatchingPipeline, MatchingReport
from app.matching.rules import evaluate_pair
from app.services.container import build_fixture_venues
from app.venues.fixture import FixtureSet
from tests.conftest import NOW, make_market, make_settings

D = Decimal
CONFIG = MatchingConfig(
    min_confidence=D("0.90"),
    min_similarity=D("0.30"),
    max_candidates_per_market=25,
    candidate_time_window=timedelta(days=400),
    max_resolution_gap=timedelta(hours=72),
)


@pytest.fixture
async def report(fixture_set: FixtureSet, tmp_path: Any) -> MatchingReport:
    clock = FrozenClock(fixture_set.as_of)
    venues = build_fixture_venues(make_settings(tmp_path), fixture_set, clock)
    kalshi = await venues.kalshi.list_active_markets()
    poly = await venues.polymarket.list_active_markets()
    pipeline = MatchingPipeline(CONFIG, embedder=HashingEmbeddingProvider())
    return await pipeline.run(
        kalshi,
        poly,
        now=clock.now(),
        data_source=DataSource.FIXTURE,
        enrich_kalshi=venues.kalshi.enrich_markets,
    )


def _pairs(report: MatchingReport) -> dict[str, Any]:
    return {f"{p.kalshi_market_id}|{p.polymarket_market_id}": p for p in report.pairs}


async def test_fixture_expectations(report: MatchingReport, fixture_set: FixtureSet) -> None:
    pairs = _pairs(report)
    expected = fixture_set.manifest["expected"]
    for key, relation in expected["approved_pairs"].items():
        assert pairs[key].relation.value == relation, key
        assert pairs[key].approved_for_arbitrage_calculation, key
        assert not pairs[key].blocking_mismatches, key
    for key, relation in expected["rejected_pairs"].items():
        assert pairs[key].relation.value == relation, key
        assert not pairs[key].approved_for_arbitrage_calculation, key
        assert pairs[key].blocking_mismatches, key
    assert {p.polymarket_market_id for p in report.approved} == {"610001", "610002", "610003", "610010"}


async def test_every_pair_has_machine_readable_explanation(report: MatchingReport) -> None:
    for pair in report.pairs:
        assert pair.deterministic_checks
        assert pair.decision_reasons
        judgement = pair.semantic_explanation.model_dump(mode="json")
        assert set(judgement) == {
            "relation",
            "confidence",
            "shared_event",
            "same_resolution_criteria",
            "differences",
            "evidence",
            "safe_for_cross_venue_arbitrage",
            "reason",
        }
        assert judgement["safe_for_cross_venue_arbitrage"] == pair.approved_for_arbitrage_calculation


async def test_specific_rejection_reasons(report: MatchingReport) -> None:
    pairs = _pairs(report)
    office = pairs["KXSENATEOH-26-JAVE|610008"]
    assert any(c.name == "office" and c.status is CheckStatus.FAIL for c in office.deterministic_checks)
    sources = pairs["KXBTCD-26DEC3117-T150000|610006"]
    assert any(m.startswith("resolution_source") for m in sources.blocking_mismatches)
    inclusivity = pairs["KXGDP-26Q3-T3.0|610004"]
    check = next(c for c in inclusivity.deterministic_checks if c.name == "threshold_and_interval")
    assert check.kalshi_value == "[3.0, +inf)" and check.polymarket_value == "(3.0, +inf)"
    deadline = pairs["KXARTEMIS3-27JUL|610007"]
    assert any(
        c.name == "event_deadline" and c.status is CheckStatus.FAIL for c in deadline.deterministic_checks
    )
    popular = pairs["KXPRESPARTY-28-DEM|610009"]
    assert any(c.name == "predicate" and c.status is CheckStatus.FAIL for c in popular.deterministic_checks)


async def test_complementary_pair_leg_mapping(report: MatchingReport) -> None:
    pair = _pairs(report)["KXCPIYOY-26NOV-T3.0|610003"]
    assert [(m.kalshi_side, m.polymarket_side) for m in pair.leg_mappings] == [
        (OutcomeSide.YES, OutcomeSide.YES),
        (OutcomeSide.NO, OutcomeSide.NO),
    ]
    equivalent = _pairs(report)["KXFEDDECISION-26DEC-C25|610001"]
    assert [(m.kalshi_side, m.polymarket_side) for m in equivalent.leg_mappings] == [
        (OutcomeSide.YES, OutcomeSide.NO),
        (OutcomeSide.NO, OutcomeSide.YES),
    ]


async def test_candidate_space_is_reduced(report: MatchingReport) -> None:
    stats = report.stats
    assert stats["compared_after_blocking"] < stats["possible_pairs"] / 2
    assert stats["candidates"] < stats["compared_after_blocking"]
    assert stats["ineligible"] == 5


def _evaluate(kalshi_title: str, kalshi_rules: str, poly_title: str, poly_rules: str, **kwargs: Any) -> Any:
    k = make_market("K1", Venue.KALSHI, title=kalshi_title, rules=kalshi_rules, **kwargs.get("k", {}))
    p = make_market("P1", Venue.POLYMARKET, title=poly_title, rules=poly_rules, **kwargs.get("p", {}))
    return evaluate_pair(
        k,
        p,
        extract_proposition(k),
        extract_proposition(p),
        similarity=D("0.8"),
        min_similarity=D("0.3"),
        min_confidence=D("0.9"),
        max_resolution_gap=timedelta(hours=72),
    )


BASE_RULES = (
    "Yes if the U.S. unemployment rate (seasonally adjusted) for October 2026, as first reported by the Bureau of "
    "Labor Statistics, is above 4.5%. The rate is reported to one decimal place. If the release is delayed, the "
    "market will remain open."
)


def test_identical_contracts_are_equivalent_and_approved() -> None:
    result = _evaluate(
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
    )
    assert result.relation is Relation.EQUIVALENT
    assert result.contract_approved


def test_rounding_difference_blocks_approval() -> None:
    other = BASE_RULES.replace("one decimal place", "two decimal places")
    result = _evaluate(
        "October 2026 unemployment above 4.5%?", BASE_RULES, "October 2026 unemployment above 4.5%?", other
    )
    assert result.relation is Relation.AMBIGUOUS
    assert not result.contract_approved


def test_unstated_field_on_one_venue_blocks_equivalence() -> None:
    other = BASE_RULES.replace(" (seasonally adjusted)", "")
    result = _evaluate(
        "October 2026 unemployment above 4.5%?", BASE_RULES, "October 2026 unemployment above 4.5%?", other
    )
    assert result.relation is Relation.AMBIGUOUS
    assert any("seasonal_adjustment" in m for m in result.blocking_mismatches)


def test_final_versus_preliminary_is_rejected() -> None:
    final = BASE_RULES.replace("as first reported", "as shown in the final estimate")
    result = _evaluate(
        "October 2026 unemployment above 4.5%?", BASE_RULES, "October 2026 unemployment above 4.5%?", final
    )
    assert not result.contract_approved
    assert any(m.startswith("preliminary_vs_final") for m in result.blocking_mismatches)


def test_conditional_clause_is_rejected() -> None:
    conditional = BASE_RULES + " This market is conditional on the report being published in October."
    result = _evaluate(
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        "October 2026 unemployment above 4.5%?",
        conditional,
    )
    assert not result.contract_approved
    assert any(m.startswith("conditional_clauses") for m in result.blocking_mismatches)


def test_cancellation_treatment_difference_is_rejected() -> None:
    other = BASE_RULES.replace("the market will remain open", "the market resolves 50-50")
    result = _evaluate(
        "October 2026 unemployment above 4.5%?", BASE_RULES, "October 2026 unemployment above 4.5%?", other
    )
    assert any(m.startswith("cancellation_treatment") for m in result.blocking_mismatches)


def test_inactive_market_blocks_approval() -> None:
    result = _evaluate(
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        p={"status": MarketStatus.PAUSED},
    )
    assert not result.contract_approved
    assert any(m.startswith("market_status") for m in result.blocking_mismatches)


def test_resolution_deadline_gap_blocks_equivalence() -> None:
    result = _evaluate(
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        "October 2026 unemployment above 4.5%?",
        BASE_RULES,
        p={"resolution_time": NOW + timedelta(days=90)},
    )
    assert result.relation is Relation.AMBIGUOUS
    assert any(m.startswith("resolution_deadline") for m in result.blocking_mismatches)


def test_multi_winner_versus_single_winner() -> None:
    rules = "Resolves Yes per the Associated Press call of the 2026 Ohio Senate primary election."
    result = _evaluate(
        "Will Jordan Avery win the 2026 Ohio Senate primary?",
        rules,
        "Will Jordan Avery finish in the top two of the 2026 Ohio Senate primary?",
        rules,
    )
    assert not result.contract_approved
    assert any(c.name == "winner_structure" and c.status is CheckStatus.FAIL for c in result.checks)


def test_different_candidates_same_race_are_mutually_exclusive() -> None:
    rules = "Resolves per the Associated Press call of the 2026 Ohio Senate general election."
    result = _evaluate(
        "Will Jordan Avery win the 2026 Ohio Senate election?",
        rules,
        "Will Morgan Blake win the 2026 Ohio Senate election?",
        rules,
    )
    assert result.relation is Relation.MUTUALLY_EXCLUSIVE


def test_missing_timezone_blocks_time_based_equivalence() -> None:
    k_rules = "Yes if the launch happens by December 31, 2026 (Eastern Time), as confirmed by NASA."
    p_rules = "Yes if the launch happens by December 31, 2026, as confirmed by NASA."
    result = _evaluate("Launch by December 31, 2026?", k_rules, "Launch by December 31, 2026?", p_rules)
    assert result.relation is Relation.AMBIGUOUS
    assert any(m.startswith("time_zone") for m in result.blocking_mismatches)
