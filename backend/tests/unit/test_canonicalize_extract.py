from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.enums import Relation, Venue
from app.matching.extract import Proposition, extract_proposition
from app.matching.intervals import IntervalSet, classify_sets
from app.matching.text import canonicalize
from tests.conftest import make_market

D = Decimal


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("Will the U.S. unemployment rate rise?", "united states unemployment rate"),
        ("Close at 5pm ET", "eastern time"),
        ("Fed cuts 25bps", "federal reserve cuts 25 basis points"),
        ("CPI YoY above 3.0%", "cpi year over year above 3.0 percent"),
        ("BTC ≥ $150,000", "bitcoin at least $150000"),
        ("Unemployment (SA)", "unemployment seasonally adjusted"),
        ("CPI (NSA)", "cpi not seasonally adjusted"),
        ("GDP in Q3 2026", "gdp in third quarter 2026"),
        ("“Smart” quotes — dash", '"smart" quotes - dash'),
        ("Ohio gubernatorial race", "ohio governor race"),
        ("Price over $150k", "price over $150000"),
    ],
)
def test_canonicalization(raw: str, fragment: str) -> None:
    result = canonicalize(raw)
    assert fragment in result.canonical
    assert result.original == raw  # original text is never discarded


def test_us_pronoun_is_not_expanded() -> None:
    assert "united states" not in canonicalize("tell us about it").canonical


def _prop(title: str, rules: str = "", source: str | None = None, venue: Venue = Venue.KALSHI) -> Proposition:
    return extract_proposition(
        make_market("X", venue, title=title, rules=rules or title, resolution_source=source)
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("GDP growth at least 3.0%?", "[3.0, +inf)"),
        ("GDP growth greater than 3.0%?", "(3.0, +inf)"),
        ("CPI YoY at or below 3.0%?", "(-inf, 3.0]"),
        ("CPI YoY below 3.0%?", "(-inf, 3.0)"),
        ("Unemployment rate 4.5% or higher?", "[4.5, +inf)"),
        ("Unemployment rate 4.5% or lower?", "(-inf, 4.5]"),
        ("Bitcoin above $150,000 on Dec 31, 2026?", "(150000, +inf)"),
    ],
)
def test_threshold_parsing(title: str, expected: str) -> None:
    assert _prop(title).region_text == expected


def test_multiple_thresholds_are_ambiguous() -> None:
    prop = _prop("Unemployment rate this month", rules="Yes if above 4.5% and No if below 4.0%.")
    assert prop.region is None and prop.region_ambiguous


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("Fed decision? — Cut 25bps", "cut of exactly 25 basis points"),
        ("Fed decision? — Cut >25bps", "cut of more than 25 basis points"),
        ("Fed decision? — Hold", "hold (no change)"),
        ("Fed decision? — Hike", "hike (any size)"),
        ("Will the Fed cut rates by at least 50 bps in December 2026?", "cut of at least 50 basis points"),
    ],
)
def test_policy_regions(title: str, text: str) -> None:
    prop = _prop(title)
    assert prop.kind == "policy_decision"
    assert prop.region_text == text


def test_by_versus_before_deadlines_with_timezone() -> None:
    by = _prop("Will it launch by December 31, 2026?", rules="Launch by December 31, 2026 (Eastern Time).")
    before = _prop(
        "Will it launch before January 1, 2027?", rules="Launch before January 1, 2027 Eastern Time."
    )
    assert by.kind == before.kind == "occurrence"
    assert by.timezone == before.timezone == "America/New_York"
    assert by.deadline is not None and before.deadline is not None
    assert by.deadline.instant == datetime(2027, 1, 1, 5, 0, tzinfo=UTC)  # midnight ET in UTC
    assert by.region is not None and before.region is not None
    assert classify_sets(by.region, before.region) is Relation.EQUIVALENT
    earlier = _prop(
        "Will it launch before December 31, 2026?", rules="Before December 31, 2026 (Eastern Time)."
    )
    assert earlier.region is not None
    assert classify_sets(earlier.region, by.region) is Relation.A_IMPLIES_B


def test_period_and_entities() -> None:
    prop = _prop("Will Jordan Avery win the 2026 Ohio Senate election?")
    assert prop.kind == "election"
    assert prop.subject == "jordan avery"
    assert prop.office == "senate"
    assert prop.geography == frozenset({"ohio"})
    assert prop.period == "2026"
    assert prop.predicate == "win_election"
    popular = _prop(
        "Will the Democratic candidate win the popular vote in the 2028 U.S. presidential election?"
    )
    assert popular.predicate == "win_popular_vote"
    assert popular.subject == "party:democratic"
    assert popular.office == "president"


def test_resolution_details_are_extracted() -> None:
    prop = _prop(
        "October 2026 unemployment rate above 4.5%?",
        rules=(
            "Yes if the seasonally adjusted rate, as first reported by the Bureau of Labor Statistics, is above 4.5%. "
            "The rate is reported to one decimal place. Subsequent revisions will not be considered. "
            "If the release is delayed, the market will remain open."
        ),
    )
    assert prop.sources == frozenset({"bls"})
    assert prop.finality == "first_release"
    assert prop.revision == "ignored"
    assert prop.rounding == "0.1"
    assert prop.seasonal == "sa"
    assert prop.cancellation == frozenset({"extended"})
    assert prop.period == "2026-10"


def test_negation_complements_region() -> None:
    prop = _prop("Will the unemployment rate not be above 4.5% for October 2026?")
    assert prop.negated
    assert prop.region == IntervalSet.less_than(D("4.5"), inclusive=True)


def test_conditional_clause_detected() -> None:
    prop = _prop(
        "Fed decision? — Cut 25bps", rules="Conditional on the meeting being held in person, Yes if cut."
    )
    assert prop.conditional is not None
