from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.domain.enums import Relation
from app.matching.intervals import NEG_INF, POS_INF, Interval, IntervalSet, classify_sets

D = Decimal


def gt(v: str, inclusive: bool = False) -> IntervalSet:
    return IntervalSet.greater_than(D(v), inclusive=inclusive)


def lt(v: str, inclusive: bool = False) -> IntervalSet:
    return IntervalSet.less_than(D(v), inclusive=inclusive)


def test_classification_table() -> None:
    assert classify_sets(gt("3.0"), gt("3.0")) is Relation.EQUIVALENT
    assert classify_sets(gt("3.0"), lt("3.0", inclusive=True)) is Relation.COMPLEMENTARY
    assert classify_sets(gt("3.0", inclusive=True), gt("3.0")) is Relation.B_IMPLIES_A  # >= vs >
    assert classify_sets(gt("7000"), gt("6800")) is Relation.A_IMPLIES_B
    assert classify_sets(IntervalSet.point(D(-25)), IntervalSet.point(D(0))) is Relation.MUTUALLY_EXCLUSIVE
    assert classify_sets(gt("3.0"), lt("4.0")) is Relation.PARTIALLY_OVERLAPPING
    # >= 3.0 and < 3.0 partition the line: complementary
    assert classify_sets(gt("3.0", inclusive=True), lt("3.0")) is Relation.COMPLEMENTARY
    # > 3.0 and < 3.0 miss the point 3.0: mutually exclusive but not complementary
    assert classify_sets(gt("3.0"), lt("3.0")) is Relation.MUTUALLY_EXCLUSIVE


def test_complement_of_point_and_union() -> None:
    point = IntervalSet.point(D(5))
    complement = point.complement()
    assert complement == IntervalSet.of(
        Interval(NEG_INF, False, D(5), False), Interval(D(5), False, POS_INF, False)
    )
    assert point.union(complement).is_full
    assert point.intersect(complement).is_empty
    assert IntervalSet.full().complement().is_empty


values = st.decimals(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False, places=2)


@given(values, st.booleans(), values, st.booleans())
def test_complement_partitions_line(a: Decimal, a_inc: bool, b: Decimal, b_inc: bool) -> None:
    left, right = min(a, b), max(a, b)
    region = IntervalSet.between(left, right, low_inclusive=a_inc, high_inclusive=b_inc)
    complement = region.complement()
    assert region.intersect(complement).is_empty
    assert region.union(complement).is_full
    assert complement.complement() == region


@given(values, values)
def test_threshold_relations_are_consistent(a: Decimal, b: Decimal) -> None:
    relation = classify_sets(
        IntervalSet.greater_than(a, inclusive=False), IntervalSet.greater_than(b, inclusive=False)
    )
    if a == b:
        assert relation is Relation.EQUIVALENT
    elif a > b:
        assert relation is Relation.A_IMPLIES_B
    else:
        assert relation is Relation.B_IMPLIES_A
