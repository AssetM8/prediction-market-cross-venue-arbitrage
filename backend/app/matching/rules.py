"""Stage 4 and Stage 5 - deterministic rule validation and relation classification.

Proposition A is the Kalshi market's YES outcome, proposition B the Polymarket market's
YES outcome. Every check returns PASS / FAIL / UNKNOWN / NOT_APPLICABLE with the values
seen on each side, so each accept/reject decision is machine-readable and auditable.

Classification is exact where possible: when both propositions describe the same event and
quantity, their YES regions (sets of values or times) are compared with interval algebra
(:mod:`app.matching.intervals`). Resolution-criteria mismatches (sources, first release vs
final, revisions, cancellation/suspension/recount handling, conditional clauses, rounding,
seasonal adjustment) turn an otherwise exact relation into ``AMBIGUOUS``. For the two
relations eligible for execution (EQUIVALENT, COMPLEMENTARY) any *unknown* critical field
also forces ``AMBIGUOUS``: equivalence has to be shown, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final

from app.domain.enums import (
    EXECUTABLE_RELATIONS,
    CheckStatus,
    MarketStatus,
    OutcomeSide,
    Relation,
)
from app.domain.models import CheckResult, LegMapping, NormalizedMarket, SemanticJudgement
from app.matching.extract import Proposition, _topic_tokens
from app.matching.intervals import classify_sets
from app.matching.text import jaccard

IDENTITY_GROUP: Final = "identity"
REGION_GROUP: Final = "region"
CRITERIA_GROUP: Final = "resolution_criteria"
STATUS_GROUP: Final = "status"
TIMING_GROUP: Final = "timing"
OPTIONAL_GROUP: Final = "optional"

CATEGORY_ALIASES: Final = {
    "economics": "economics",
    "economy": "economics",
    "financials": "economics",
    "finance": "economics",
    "fed": "economics",
    "inflation": "economics",
    "politics": "politics",
    "elections": "politics",
    "election": "politics",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
    "science and technology": "science",
    "science": "science",
    "tech": "science",
    "technology": "science",
    "space": "science",
    "sports": "sports",
    "culture": "culture",
    "entertainment": "culture",
    "world": "world",
    "geopolitics": "world",
    "climate and weather": "weather",
    "weather": "weather",
    "companies": "economics",
}

OCCURRENCE_TOPIC_PASS: Final = 0.6
OCCURRENCE_TOPIC_UNKNOWN: Final = 0.3


@dataclass(frozen=True)
class GroupedCheck:
    group: str
    result: CheckResult


@dataclass(frozen=True)
class RelationEvaluation:
    checks: tuple[CheckResult, ...]
    groups: dict[str, str]
    relation: Relation
    region_relation: Relation | None
    confidence: Decimal
    blocking_mismatches: tuple[str, ...]
    judgement: SemanticJudgement
    leg_mappings: tuple[LegMapping, ...]
    contract_approved: bool
    decision_reasons: tuple[str, ...]


def _fmt(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, frozenset | set):
        return ", ".join(sorted(str(v) for v in value)) or None
    return str(value)


def _compare(
    name: str,
    group: str,
    left: object,
    right: object,
    *,
    critical: bool = True,
    both_missing: CheckStatus = CheckStatus.NOT_APPLICABLE,
    detail: str = "",
) -> GroupedCheck:
    """Equality check: both missing -> ``both_missing``; one missing -> UNKNOWN."""
    left_empty = left is None or left == frozenset()
    right_empty = right is None or right == frozenset()
    if left_empty and right_empty:
        status = both_missing
        note = detail or "not stated on either venue"
    elif left_empty or right_empty:
        status = CheckStatus.UNKNOWN
        note = detail or "stated on one venue only"
    elif left == right:
        status = CheckStatus.PASS
        note = detail or "identical after canonicalization"
    else:
        status = CheckStatus.FAIL
        note = detail or "differs"
    return GroupedCheck(
        group,
        CheckResult(
            name=name,
            status=status,
            critical=critical,
            kalshi_value=_fmt(left),
            polymarket_value=_fmt(right),
            detail=note,
        ),
    )


UNSPECIFIED_TZ: Final = "unspecified-tz"


def _compare_observation(left: str | None, right: str | None) -> GroupedCheck:
    """Observation instants ("2026-12-31T17:00@America/New_York"); a missing zone is UNKNOWN."""
    check = _compare("observation_time", IDENTITY_GROUP, left, right)
    if check.result.status is CheckStatus.FAIL and left and right:
        l_when, _, l_zone = left.partition("@")
        r_when, _, r_zone = right.partition("@")
        if l_when == r_when and UNSPECIFIED_TZ in (l_zone, r_zone):
            return GroupedCheck(
                IDENTITY_GROUP,
                check.result.model_copy(
                    update={"status": CheckStatus.UNKNOWN, "detail": "time zone stated on one venue only"}
                ),
            )
    return check


def _category(market: NormalizedMarket) -> str | None:
    raw = market.category.strip().lower()
    return CATEGORY_ALIASES.get(raw, raw or None)


def _effective_revision(prop: Proposition) -> str | None:
    if prop.revision is None and prop.finality == "first_release":
        return "ignored (implied by first-release wording)"
    if prop.revision == "ignored":
        return "ignored (implied by first-release wording)" if prop.finality == "first_release" else "ignored"
    return prop.revision


def evaluate_pair(
    kalshi: NormalizedMarket,
    polymarket: NormalizedMarket,
    a: Proposition,
    b: Proposition,
    *,
    similarity: Decimal,
    min_similarity: Decimal,
    min_confidence: Decimal,
    max_resolution_gap: timedelta,
) -> RelationEvaluation:
    """Run all deterministic checks and classify the relation between A and B."""
    checks: list[GroupedCheck] = []

    # ---- identity ---------------------------------------------------------------------
    checks.append(_compare("contract_structure", IDENTITY_GROUP, kalshi.market_type, polymarket.market_type))
    kind_check = _compare(
        "event_kind",
        IDENTITY_GROUP,
        a.kind if a.kind != "generic" else None,
        b.kind if b.kind != "generic" else None,
        both_missing=CheckStatus.UNKNOWN,
        detail="template recognised from wording" if a.kind == b.kind != "generic" else "",
    )
    checks.append(kind_check)
    same_kind = a.kind == b.kind and a.kind != "generic"

    if same_kind and a.kind == "occurrence":
        topic = jaccard(_topic_tokens(a.title), _topic_tokens(b.title))
        status = (
            CheckStatus.PASS
            if topic >= OCCURRENCE_TOPIC_PASS
            else CheckStatus.UNKNOWN
            if topic >= OCCURRENCE_TOPIC_UNKNOWN
            else CheckStatus.FAIL
        )
        checks.append(
            GroupedCheck(
                IDENTITY_GROUP,
                CheckResult(
                    name="subject",
                    status=status,
                    critical=True,
                    kalshi_value=" ".join(sorted(_topic_tokens(a.title))),
                    polymarket_value=" ".join(sorted(_topic_tokens(b.title))),
                    detail=f"event-topic token overlap {topic:.2f}",
                ),
            )
        )
    else:
        checks.append(
            _compare("subject", IDENTITY_GROUP, a.subject, b.subject, both_missing=CheckStatus.UNKNOWN)
        )
    checks.append(_compare("predicate", IDENTITY_GROUP, a.predicate, b.predicate))
    checks.append(_compare("measurement_definition", IDENTITY_GROUP, a.metric, b.metric))
    checks.append(_compare("office", IDENTITY_GROUP, a.office, b.office))
    checks.append(
        _compare(
            "geographic_scope",
            IDENTITY_GROUP,
            a.geography or None,
            b.geography or None,
            both_missing=CheckStatus.NOT_APPLICABLE,
        )
    )
    if a.kind != "occurrence" or b.kind != "occurrence":
        checks.append(
            _compare("reference_period", IDENTITY_GROUP, a.period, b.period, both_missing=CheckStatus.UNKNOWN)
        )
    checks.append(_compare_observation(a.observation, b.observation))

    # ---- region (threshold / deadline / polarity) -------------------------------------
    region_relation: Relation | None = None
    region_axis_ok = a.region_axis is not None and a.region_axis == b.region_axis
    if a.region is not None and b.region is not None and region_axis_ok:
        region_relation = classify_sets(a.region, b.region)
    region_name = (
        "event_deadline" if a.kind == "occurrence" or b.kind == "occurrence" else "threshold_and_interval"
    )
    if a.region is None and b.region is None:
        region_status = CheckStatus.NOT_APPLICABLE
        region_detail = "no numeric or time threshold on either venue"
    elif a.region_ambiguous or b.region_ambiguous:
        region_status = CheckStatus.UNKNOWN
        region_detail = "threshold wording could not be read unambiguously"
    elif region_relation is None:
        region_status = CheckStatus.UNKNOWN
        region_detail = "threshold/deadline stated on one venue only or on different axes"
    elif region_relation in EXECUTABLE_RELATIONS:
        region_status = CheckStatus.PASS
        region_detail = f"YES regions are {region_relation.value.lower()}"
    else:
        region_status = CheckStatus.FAIL
        region_detail = (
            f"YES regions differ ({region_relation.value}); check threshold inclusivity, "
            "direction, interval bounds and by/before deadline semantics"
        )
    checks.append(
        GroupedCheck(
            REGION_GROUP,
            CheckResult(
                name=region_name,
                status=region_status,
                critical=True,
                kalshi_value=a.region_text,
                polymarket_value=b.region_text,
                detail=region_detail,
            ),
        )
    )
    uses_time = a.deadline is not None or b.deadline is not None or a.observation or b.observation
    checks.append(
        _compare(
            "time_zone",
            REGION_GROUP,
            a.timezone if uses_time else None,
            b.timezone if uses_time else None,
            detail="" if uses_time else "no time-specific deadline",
        )
    )
    polarity_status = CheckStatus.PASS
    polarity_detail = "same polarity"
    if region_relation is Relation.COMPLEMENTARY:
        polarity_detail = "opposite polarity: YES on one venue is NO on the other"
    elif a.negated != b.negated and region_relation is None:
        polarity_status = CheckStatus.UNKNOWN
        polarity_detail = "negation on one venue without a comparable region"
    checks.append(
        GroupedCheck(
            REGION_GROUP,
            CheckResult(
                name="outcome_polarity",
                status=polarity_status,
                critical=True,
                kalshi_value="negated" if a.negated else "affirmative",
                polymarket_value="negated" if b.negated else "affirmative",
                detail=polarity_detail,
            ),
        )
    )

    # ---- resolution criteria ------------------------------------------------------------
    econ = a.kind == "economic_data" or b.kind == "economic_data"
    checks.append(
        _compare(
            "resolution_source",
            CRITERIA_GROUP,
            a.sources or None,
            b.sources or None,
            both_missing=CheckStatus.UNKNOWN,
        )
    )
    checks.append(
        _compare(
            "preliminary_vs_final",
            CRITERIA_GROUP,
            a.finality,
            b.finality,
            both_missing=CheckStatus.UNKNOWN if econ else CheckStatus.NOT_APPLICABLE,
        )
    )
    checks.append(
        _compare(
            "revision_treatment",
            CRITERIA_GROUP,
            _effective_revision(a),
            _effective_revision(b),
            both_missing=CheckStatus.UNKNOWN if econ else CheckStatus.NOT_APPLICABLE,
        )
    )
    checks.append(_compare("cancellation_treatment", CRITERIA_GROUP, a.cancellation, b.cancellation))
    checks.append(_compare("suspension_treatment", CRITERIA_GROUP, a.suspension, b.suspension))
    checks.append(_compare("recount_treatment", CRITERIA_GROUP, a.recount, b.recount))
    conditional_status = CheckStatus.PASS if not a.conditional and not b.conditional else CheckStatus.FAIL
    checks.append(
        GroupedCheck(
            CRITERIA_GROUP,
            CheckResult(
                name="conditional_clauses",
                status=conditional_status,
                critical=True,
                kalshi_value=a.conditional,
                polymarket_value=b.conditional,
                detail="no conditional clauses"
                if conditional_status is CheckStatus.PASS
                else "conditional clause present; payoff equivalence cannot be verified",
            ),
        )
    )
    checks.append(
        _compare(
            "rounding_rules",
            CRITERIA_GROUP,
            a.rounding,
            b.rounding,
            both_missing=CheckStatus.UNKNOWN if econ else CheckStatus.NOT_APPLICABLE,
        )
    )
    checks.append(
        _compare(
            "seasonal_adjustment",
            CRITERIA_GROUP,
            a.seasonal,
            b.seasonal,
            both_missing=CheckStatus.UNKNOWN if econ else CheckStatus.NOT_APPLICABLE,
        )
    )
    checks.append(_compare("winner_structure", CRITERIA_GROUP, a.winner_structure, b.winner_structure))
    gap_status = CheckStatus.UNKNOWN
    gap_detail = "resolution time missing on a venue"
    if kalshi.resolution_time and polymarket.resolution_time:
        gap = abs(kalshi.resolution_time - polymarket.resolution_time)
        gap_status = CheckStatus.PASS if gap <= max_resolution_gap else CheckStatus.FAIL
        gap_detail = f"expected resolution times differ by {gap}"
    checks.append(
        GroupedCheck(
            TIMING_GROUP,
            CheckResult(
                name="resolution_deadline",
                status=gap_status,
                critical=True,
                kalshi_value=kalshi.resolution_time.isoformat() if kalshi.resolution_time else None,
                polymarket_value=polymarket.resolution_time.isoformat()
                if polymarket.resolution_time
                else None,
                detail=gap_detail,
            ),
        )
    )

    # ---- venue status (Stage 6, contract level) ------------------------------------------
    both_active = kalshi.status is MarketStatus.ACTIVE and polymarket.status is MarketStatus.ACTIVE
    checks.append(
        GroupedCheck(
            STATUS_GROUP,
            CheckResult(
                name="market_status",
                status=CheckStatus.PASS if both_active else CheckStatus.FAIL,
                critical=True,
                kalshi_value=kalshi.status.value,
                polymarket_value=polymarket.status.value,
                detail="both markets active" if both_active else "a market is not active",
            ),
        )
    )

    # ---- optional signals (affect confidence only) --------------------------------------
    checks.append(
        _compare(
            "category",
            OPTIONAL_GROUP,
            _category(kalshi),
            _category(polymarket),
            critical=False,
            both_missing=CheckStatus.UNKNOWN,
        )
    )
    checks.append(
        GroupedCheck(
            OPTIONAL_GROUP,
            CheckResult(
                name="text_similarity",
                status=CheckStatus.PASS if similarity >= min_similarity else CheckStatus.FAIL,
                critical=False,
                kalshi_value=None,
                polymarket_value=None,
                detail=f"candidate similarity {similarity} (threshold {min_similarity})",
            ),
        )
    )
    close_status = CheckStatus.UNKNOWN
    if kalshi.close_time and polymarket.close_time:
        close_status = (
            CheckStatus.PASS
            if abs(kalshi.close_time - polymarket.close_time) <= max_resolution_gap
            else CheckStatus.FAIL
        )
    checks.append(
        GroupedCheck(
            OPTIONAL_GROUP,
            CheckResult(
                name="close_time_proximity",
                status=close_status,
                critical=False,
                kalshi_value=kalshi.close_time.isoformat() if kalshi.close_time else None,
                polymarket_value=polymarket.close_time.isoformat() if polymarket.close_time else None,
                detail="trading close times",
            ),
        )
    )

    return _classify(checks, a, b, region_relation, min_confidence=min_confidence)


def _classify(
    checks: list[GroupedCheck],
    a: Proposition,
    b: Proposition,
    region_relation: Relation | None,
    *,
    min_confidence: Decimal,
) -> RelationEvaluation:
    def failing(group: str, status: CheckStatus) -> list[CheckResult]:
        return [
            c.result for c in checks if c.group == group and c.result.critical and c.result.status is status
        ]

    identity_fail = failing(IDENTITY_GROUP, CheckStatus.FAIL)
    identity_unknown = failing(IDENTITY_GROUP, CheckStatus.UNKNOWN)
    region_names = ("threshold_and_interval", "event_deadline")
    region_fail = [c for c in failing(REGION_GROUP, CheckStatus.FAIL) if c.name in region_names]
    criteria_fail = failing(CRITERIA_GROUP, CheckStatus.FAIL) + [
        c for c in failing(REGION_GROUP, CheckStatus.FAIL) if c.name not in region_names
    ]
    critical_unknown = [
        c.result for c in checks if c.result.critical and c.result.status is CheckStatus.UNKNOWN
    ]
    timing_fail = failing(TIMING_GROUP, CheckStatus.FAIL)
    status_fail = failing(STATUS_GROUP, CheckStatus.FAIL)
    fail_names = {c.name for c in identity_fail}
    reasons: list[str] = []

    same_single_winner_race = (
        a.kind == b.kind == "election"
        and fail_names == {"subject"}
        and a.winner_structure == b.winner_structure == "single"
        and a.predicate == b.predicate
    )
    if same_single_winner_race:
        relation = Relation.MUTUALLY_EXCLUSIVE
        reasons.append("same single-winner race, different candidates: at most one can resolve YES")
    elif fail_names == {"predicate"} and a.kind == b.kind == "election":
        relation = Relation.PARTIALLY_OVERLAPPING
        reasons.append(f"same race and subject but different predicate ({a.predicate} vs {b.predicate})")
    elif identity_fail:
        relation = Relation.UNRELATED
        reasons.append("event identity differs: " + ", ".join(sorted(fail_names)))
    elif identity_unknown:
        relation = Relation.AMBIGUOUS
        reasons.append(
            "event identity could not be established: " + ", ".join(c.name for c in identity_unknown)
        )
    elif a.region is None and b.region is None:
        relation = Relation.EQUIVALENT
        reasons.append("same event, subject and predicate; no threshold involved")
    elif region_relation is None:
        relation = Relation.AMBIGUOUS
        reasons.append("YES regions not comparable")
    else:
        relation = region_relation
        reasons.append(f"YES-region algebra gives {region_relation.value}")

    tz_unknown = any(c.result.name == "time_zone" and c.result.status is CheckStatus.UNKNOWN for c in checks)
    if relation not in (Relation.UNRELATED, Relation.AMBIGUOUS) and a.region_axis == "time" and tz_unknown:
        reasons.append("time zone stated on one venue only: deadline comparison is unreliable")
        relation = Relation.AMBIGUOUS
    if relation not in (Relation.UNRELATED, Relation.AMBIGUOUS) and criteria_fail:
        reasons.append("resolution criteria differ: " + ", ".join(sorted(c.name for c in criteria_fail)))
        relation = Relation.AMBIGUOUS
    if relation in EXECUTABLE_RELATIONS and timing_fail:
        reasons.append("resolution deadlines differ: " + ", ".join(c.detail for c in timing_fail))
        relation = Relation.AMBIGUOUS
    if relation in EXECUTABLE_RELATIONS and critical_unknown:
        reasons.append("unverified critical fields: " + ", ".join(sorted(c.name for c in critical_unknown)))
        relation = Relation.AMBIGUOUS

    optional = [c.result for c in checks if c.group == OPTIONAL_GROUP]
    optional_pass = sum(1 for c in optional if c.status is CheckStatus.PASS)
    optional_fraction = Decimal(optional_pass) / Decimal(len(optional)) if optional else Decimal(0)
    if relation is Relation.AMBIGUOUS:
        confidence = Decimal("0.50")
    elif (
        relation in (Relation.UNRELATED, Relation.PARTIALLY_OVERLAPPING, Relation.MUTUALLY_EXCLUSIVE)
        and identity_fail
    ):
        confidence = Decimal("0.90")
    else:
        penalty = Decimal("0.05") * len(critical_unknown)
        confidence = max(Decimal("0.50"), Decimal("0.80") + Decimal("0.20") * optional_fraction - penalty)
    confidence = confidence.quantize(Decimal("0.01"))

    blocking = sorted(
        {
            f"{c.name}: {c.detail}"
            for c in identity_fail + criteria_fail + status_fail + region_fail + timing_fail
        }
    )
    if relation in EXECUTABLE_RELATIONS or relation is Relation.AMBIGUOUS:
        # unverified critical fields block equivalence and explain ambiguity
        blocking = sorted(set(blocking) | {f"{c.name}: {c.detail}" for c in critical_unknown})
    contract_approved = (
        relation in EXECUTABLE_RELATIONS
        and not blocking
        and not critical_unknown
        and confidence >= min_confidence
    )
    if relation in EXECUTABLE_RELATIONS and confidence < min_confidence:
        reasons.append(f"confidence {confidence} below minimum {min_confidence}")
    if status_fail:
        reasons.append("market status not compatible for trading")
    reasons.append("approved for arbitrage calculation" if contract_approved else "not approved")

    differences = tuple(
        f"{c.result.name}: {c.result.kalshi_value!s} vs {c.result.polymarket_value!s} ({c.result.detail})"
        for c in checks
        if c.result.status in (CheckStatus.FAIL, CheckStatus.UNKNOWN) and c.result.critical
    )
    evidence = tuple(
        f"{c.result.name}: {c.result.detail}" for c in checks if c.result.status is CheckStatus.PASS
    )
    criteria_ok = not criteria_fail and not [
        c for c in critical_unknown if c.name in {x.result.name for x in checks if x.group == CRITERIA_GROUP}
    ]
    judgement = SemanticJudgement(
        relation=relation,
        confidence=confidence,
        shared_event=not identity_fail and not identity_unknown,
        same_resolution_criteria=criteria_ok,
        differences=differences,
        evidence=evidence,
        safe_for_cross_venue_arbitrage=contract_approved,
        reason="; ".join(reasons),
    )
    return RelationEvaluation(
        checks=tuple(c.result for c in checks),
        groups={c.result.name: c.group for c in checks},
        relation=relation,
        region_relation=region_relation,
        confidence=confidence,
        blocking_mismatches=tuple(blocking),
        judgement=judgement,
        leg_mappings=leg_mappings_for(relation),
        contract_approved=contract_approved,
        decision_reasons=tuple(reasons),
    )


def leg_mappings_for(relation: Relation) -> tuple[LegMapping, ...]:
    """Two-leg portfolios whose payout is 1 in every joint state allowed by ``relation``."""
    if relation is Relation.EQUIVALENT:
        return (
            LegMapping(
                kalshi_side=OutcomeSide.YES,
                polymarket_side=OutcomeSide.NO,
                label="A: buy YES on Kalshi + buy NO on Polymarket",
            ),
            LegMapping(
                kalshi_side=OutcomeSide.NO,
                polymarket_side=OutcomeSide.YES,
                label="B: buy NO on Kalshi + buy YES on Polymarket",
            ),
        )
    if relation is Relation.COMPLEMENTARY:
        return (
            LegMapping(
                kalshi_side=OutcomeSide.YES,
                polymarket_side=OutcomeSide.YES,
                label="C: buy YES on Kalshi + buy YES on Polymarket",
            ),
            LegMapping(
                kalshi_side=OutcomeSide.NO,
                polymarket_side=OutcomeSide.NO,
                label="D: buy NO on Kalshi + buy NO on Polymarket",
            ),
        )
    return ()
