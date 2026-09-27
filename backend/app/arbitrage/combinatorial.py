"""Combinatorial arbitrage over dependent condition subsets (paper Definitions 2 and 4).

Markets M1 = {C_1..C_n} and M2 = {C'_1..C'_m} are MECE sets of conditions. The joint
resolution vectors V ⊆ V1 x V2 that can occur describe their dependency: if
``|V| < n * m`` the markets are dependent, and subsets S ⊆ M1, S' ⊆ M2 are *dependent
subsets* when ``sum_{c in S} c == sum_{c' in S'} c'`` for every v in V.

Definition 4: if ``sum_S price < sum_S' price`` hold YES on S and YES on the complement of
S' (payout 1 in every state); if ``>`` hold YES on the complement of S and YES on S'.

The functions here are exact and brute-force (the paper reduces markets to at most 4+1
conditions, so 2^(n+m) subsets is small). Guaranteed payouts are verified by enumerating V.
In this MVP the cross-venue engine only *executes* equivalence/complement pairs; this
module powers analytics and tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from app.core.decimal_utils import ZERO

Vector = tuple[int, ...]
JointVector = tuple[Vector, Vector]

MAX_CONDITIONS = 8  # guard against accidental exponential blow-up


@dataclass(frozen=True)
class DependentSubsets:
    s: frozenset[int]
    s_prime: frozenset[int]


@dataclass(frozen=True)
class CombinatorialResult:
    subsets: DependentSubsets
    buy_m1: frozenset[int]
    buy_m2: frozenset[int]
    cost: Decimal
    min_payout: Decimal
    profit: Decimal


def validate_vectors(n: int, m: int, vectors: Iterable[JointVector]) -> list[JointVector]:
    """Check each joint vector has exactly one true condition per market (MECE)."""
    checked = []
    for v1, v2 in vectors:
        if len(v1) != n or len(v2) != m:
            raise ValueError("vector length does not match market size")
        if sum(v1) != 1 or sum(v2) != 1 or any(x not in (0, 1) for x in (*v1, *v2)):
            raise ValueError("each market must have exactly one true condition per vector")
        checked.append((tuple(v1), tuple(v2)))
    return checked


def is_dependent(n: int, m: int, vectors: Sequence[JointVector]) -> bool:
    return len(set(vectors)) < n * m


def _subsets(size: int) -> list[frozenset[int]]:
    return [frozenset(c) for k in range(1, size + 1) for c in combinations(range(size), k)]


def dependent_subsets(n: int, m: int, vectors: Sequence[JointVector]) -> list[DependentSubsets]:
    """All non-trivial (S, S') with equal truth sums in every joint vector."""
    if n > MAX_CONDITIONS or m > MAX_CONDITIONS:
        raise ValueError(f"reduce markets to at most {MAX_CONDITIONS} conditions first")
    vecs = validate_vectors(n, m, vectors)
    results = []
    for s in _subsets(n):
        for s_prime in _subsets(m):
            if len(s) == n and len(s_prime) == m:
                continue  # trivially equal (both sum to 1)
            if all(sum(v1[i] for i in s) == sum(v2[j] for j in s_prime) for v1, v2 in vecs):
                results.append(DependentSubsets(s=s, s_prime=s_prime))
    return results


def min_payout(vectors: Sequence[JointVector], buy_m1: frozenset[int], buy_m2: frozenset[int]) -> Decimal:
    """Minimum payout per unit of holding YES on ``buy_m1`` and ``buy_m2`` across V."""
    return min(Decimal(sum(v1[i] for i in buy_m1) + sum(v2[j] for j in buy_m2)) for v1, v2 in vectors)


def evaluate(
    subsets: DependentSubsets,
    prices_m1: Sequence[Decimal],
    prices_m2: Sequence[Decimal],
    vectors: Sequence[JointVector],
) -> CombinatorialResult | None:
    """Apply Definition 4 to executable YES prices; returns None when sums are equal."""
    sum_s = sum((prices_m1[i] for i in subsets.s), ZERO)
    sum_sp = sum((prices_m2[j] for j in subsets.s_prime), ZERO)
    all_m1 = frozenset(range(len(prices_m1)))
    all_m2 = frozenset(range(len(prices_m2)))
    if sum_s < sum_sp:
        buy_m1, buy_m2 = subsets.s, all_m2 - subsets.s_prime
    elif sum_s > sum_sp:
        buy_m1, buy_m2 = all_m1 - subsets.s, subsets.s_prime
    else:
        return None
    cost = sum((prices_m1[i] for i in buy_m1), ZERO) + sum((prices_m2[j] for j in buy_m2), ZERO)
    payout = min_payout(vectors, buy_m1, buy_m2)
    return CombinatorialResult(
        subsets=subsets, buy_m1=buy_m1, buy_m2=buy_m2, cost=cost, min_payout=payout, profit=payout - cost
    )
