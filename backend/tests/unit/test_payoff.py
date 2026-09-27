from __future__ import annotations

from decimal import Decimal

import pytest

from app.arbitrage.payoff import allowed_states, guaranteed_payout_per_unit, verified_constructions
from app.domain.enums import OutcomeSide, Relation

YES, NO = OutcomeSide.YES, OutcomeSide.NO


@pytest.mark.parametrize(
    ("relation", "k_side", "p_side", "expected"),
    [
        (Relation.EQUIVALENT, YES, NO, 1),
        (Relation.EQUIVALENT, NO, YES, 1),
        (Relation.EQUIVALENT, YES, YES, 0),
        (Relation.COMPLEMENTARY, YES, YES, 1),
        (Relation.COMPLEMENTARY, NO, NO, 1),
        (Relation.COMPLEMENTARY, YES, NO, 0),
        (Relation.A_IMPLIES_B, NO, YES, 1),
        (Relation.B_IMPLIES_A, YES, NO, 1),
        (Relation.MUTUALLY_EXCLUSIVE, NO, NO, 1),
        (Relation.PARTIALLY_OVERLAPPING, YES, NO, 0),
        (Relation.AMBIGUOUS, YES, NO, 0),
        (Relation.UNRELATED, NO, YES, 0),
    ],
)
def test_guaranteed_payout(
    relation: Relation, k_side: OutcomeSide, p_side: OutcomeSide, expected: int
) -> None:
    assert guaranteed_payout_per_unit(relation, k_side, p_side) == Decimal(expected)


def test_verified_constructions_only_for_dependent_relations() -> None:
    assert {(k, p) for k, p, _ in verified_constructions(Relation.EQUIVALENT)} == {(YES, NO), (NO, YES)}
    assert {(k, p) for k, p, _ in verified_constructions(Relation.COMPLEMENTARY)} == {(YES, YES), (NO, NO)}
    assert verified_constructions(Relation.PARTIALLY_OVERLAPPING) == []
    assert verified_constructions(Relation.AMBIGUOUS) == []
    assert len(allowed_states(Relation.UNRELATED)) == 4
