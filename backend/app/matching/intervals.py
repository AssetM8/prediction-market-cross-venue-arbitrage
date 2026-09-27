"""Exact set algebra over the real line, used to classify logical relations.

A proposition about a numeric quantity ("CPI above 3.0%") or a time ("occurs by
2026-12-31 ET") is represented as the set of values of that quantity for which the
proposition resolves YES. Comparing two such sets classifies the pair exactly:

=====================  ===========================================
set relation           logical relation between A and B
=====================  ===========================================
A == B                 EQUIVALENT
A ∩ B = ∅, A ∪ B = R   COMPLEMENTARY
A ⊂ B                  A_IMPLIES_B
B ⊂ A                  B_IMPLIES_A
A ∩ B = ∅              MUTUALLY_EXCLUSIVE
otherwise              PARTIALLY_OVERLAPPING
=====================  ===========================================

Endpoints are Decimals (times are converted to epoch seconds as Decimal) so inclusivity is
exact: ``>= 3.0`` and ``> 3.0`` are different sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.enums import Relation

NEG_INF = Decimal("-Infinity")
POS_INF = Decimal("Infinity")


@dataclass(frozen=True, order=True)
class Interval:
    lower: Decimal
    lower_closed: bool
    upper: Decimal
    upper_closed: bool

    def __post_init__(self) -> None:
        if self.lower.is_infinite() and self.lower_closed:
            object.__setattr__(self, "lower_closed", False)
        if self.upper.is_infinite() and self.upper_closed:
            object.__setattr__(self, "upper_closed", False)

    @property
    def empty(self) -> bool:
        if self.lower > self.upper:
            return True
        if self.lower == self.upper:
            return not (self.lower_closed and self.upper_closed)
        return False

    def describe(self) -> str:
        left = "[" if self.lower_closed else "("
        right = "]" if self.upper_closed else ")"
        lo = "-inf" if self.lower == NEG_INF else format(self.lower, "f")
        hi = "+inf" if self.upper == POS_INF else format(self.upper, "f")
        return f"{left}{lo}, {hi}{right}"


def _intersect(a: Interval, b: Interval) -> Interval:
    if a.lower > b.lower:
        lower, lower_closed = a.lower, a.lower_closed
    elif b.lower > a.lower:
        lower, lower_closed = b.lower, b.lower_closed
    else:
        lower, lower_closed = a.lower, a.lower_closed and b.lower_closed
    if a.upper < b.upper:
        upper, upper_closed = a.upper, a.upper_closed
    elif b.upper < a.upper:
        upper, upper_closed = b.upper, b.upper_closed
    else:
        upper, upper_closed = a.upper, a.upper_closed and b.upper_closed
    return Interval(lower, lower_closed, upper, upper_closed)


@dataclass(frozen=True)
class IntervalSet:
    """A finite union of disjoint intervals, kept in canonical (merged, sorted) form."""

    intervals: tuple[Interval, ...]

    @staticmethod
    def of(*intervals: Interval) -> IntervalSet:
        return IntervalSet(_normalize(list(intervals)))

    @staticmethod
    def full() -> IntervalSet:
        return IntervalSet.of(Interval(NEG_INF, False, POS_INF, False))

    @staticmethod
    def empty_set() -> IntervalSet:
        return IntervalSet(())

    @staticmethod
    def greater_than(value: Decimal, *, inclusive: bool) -> IntervalSet:
        return IntervalSet.of(Interval(value, inclusive, POS_INF, False))

    @staticmethod
    def less_than(value: Decimal, *, inclusive: bool) -> IntervalSet:
        return IntervalSet.of(Interval(NEG_INF, False, value, inclusive))

    @staticmethod
    def between(low: Decimal, high: Decimal, *, low_inclusive: bool, high_inclusive: bool) -> IntervalSet:
        return IntervalSet.of(Interval(low, low_inclusive, high, high_inclusive))

    @staticmethod
    def point(value: Decimal) -> IntervalSet:
        return IntervalSet.of(Interval(value, True, value, True))

    @property
    def is_empty(self) -> bool:
        return not self.intervals

    @property
    def is_full(self) -> bool:
        return self == IntervalSet.full()

    def intersect(self, other: IntervalSet) -> IntervalSet:
        parts = [_intersect(a, b) for a in self.intervals for b in other.intervals]
        return IntervalSet.of(*[p for p in parts if not p.empty])

    def union(self, other: IntervalSet) -> IntervalSet:
        return IntervalSet.of(*self.intervals, *other.intervals)

    def complement(self) -> IntervalSet:
        result: list[Interval] = []
        cursor, cursor_closed = NEG_INF, False  # cursor_closed: include cursor in gap
        for interval in self.intervals:
            gap = Interval(cursor, cursor_closed, interval.lower, not interval.lower_closed)
            if not gap.empty:
                result.append(gap)
            cursor, cursor_closed = interval.upper, not interval.upper_closed
        tail = Interval(cursor, cursor_closed, POS_INF, False)
        if not tail.empty and cursor != POS_INF:
            result.append(tail)
        return IntervalSet.of(*result)

    def subset_of(self, other: IntervalSet) -> bool:
        return self.intersect(other.complement()).is_empty

    def describe(self) -> str:
        if not self.intervals:
            return "∅"
        return " ∪ ".join(interval.describe() for interval in self.intervals)


def _normalize(intervals: list[Interval]) -> tuple[Interval, ...]:
    items = sorted((i for i in intervals if not i.empty), key=lambda i: (i.lower, not i.lower_closed))
    merged: list[Interval] = []
    for current in items:
        if not merged:
            merged.append(current)
            continue
        last = merged[-1]
        touches = current.lower < last.upper or (
            current.lower == last.upper and (current.lower_closed or last.upper_closed)
        )
        if touches:
            if current.upper > last.upper:
                upper, upper_closed = current.upper, current.upper_closed
            elif current.upper < last.upper:
                upper, upper_closed = last.upper, last.upper_closed
            else:
                upper, upper_closed = last.upper, last.upper_closed or current.upper_closed
            merged[-1] = Interval(last.lower, last.lower_closed, upper, upper_closed)
        else:
            merged.append(current)
    return tuple(merged)


def classify_sets(a: IntervalSet, b: IntervalSet) -> Relation:
    """Exact logical relation between "value in A" and "value in B"."""
    if a == b:
        return Relation.EQUIVALENT
    intersection = a.intersect(b)
    if intersection.is_empty and a.union(b).is_full:
        return Relation.COMPLEMENTARY
    if a.subset_of(b):
        return Relation.A_IMPLIES_B
    if b.subset_of(a):
        return Relation.B_IMPLIES_A
    if intersection.is_empty:
        return Relation.MUTUALLY_EXCLUSIVE
    return Relation.PARTIALLY_OVERLAPPING
