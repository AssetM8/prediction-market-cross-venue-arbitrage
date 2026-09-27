"""Payoff verification by joint-state enumeration.

Following the paper's Definition 2, a dependency between two markets is described by the
set of joint resolution vectors that can actually occur. For two binary propositions A
(Kalshi YES) and B (Polymarket YES) each relation admits these joint states:

====================== ==================================
relation               possible (A, B) states
====================== ==================================
EQUIVALENT             (T, T), (F, F)
COMPLEMENTARY          (T, F), (F, T)
A_IMPLIES_B            (T, T), (F, T), (F, F)
B_IMPLIES_A            (T, T), (T, F), (F, F)
MUTUALLY_EXCLUSIVE     (T, F), (F, T), (F, F)
other                  all four states
====================== ==================================

A portfolio's *guaranteed* payout is its minimum payout over the possible states. This is
how every opportunity's ``guaranteed_payout`` is derived - it is computed, not assumed.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import product

from app.core.decimal_utils import ONE, ZERO
from app.domain.enums import OutcomeSide, Relation
from app.domain.models import PayoffState

JointState = tuple[bool, bool]

_ALL: frozenset[JointState] = frozenset((a, b) for a, b in product((True, False), repeat=2))

ALLOWED_STATES: dict[Relation, frozenset[JointState]] = {
    Relation.EQUIVALENT: frozenset({(True, True), (False, False)}),
    Relation.COMPLEMENTARY: frozenset({(True, False), (False, True)}),
    Relation.A_IMPLIES_B: frozenset({(True, True), (False, True), (False, False)}),
    Relation.B_IMPLIES_A: frozenset({(True, True), (True, False), (False, False)}),
    Relation.MUTUALLY_EXCLUSIVE: frozenset({(True, False), (False, True), (False, False)}),
}


def allowed_states(relation: Relation) -> frozenset[JointState]:
    return ALLOWED_STATES.get(relation, _ALL)


def _leg_pays(side: OutcomeSide, proposition_true: bool) -> Decimal:
    wins = proposition_true if side is OutcomeSide.YES else not proposition_true
    return ONE if wins else ZERO


def payoff_states(
    relation: Relation, kalshi_side: OutcomeSide, polymarket_side: OutcomeSide
) -> tuple[PayoffState, ...]:
    """Per-unit payout of (buy ``kalshi_side`` + buy ``polymarket_side``) in each possible state."""
    return tuple(
        PayoffState(
            kalshi_yes=a,
            polymarket_yes=b,
            payout_per_unit=_leg_pays(kalshi_side, a) + _leg_pays(polymarket_side, b),
        )
        for a, b in sorted(allowed_states(relation), reverse=True)
    )


def guaranteed_payout_per_unit(
    relation: Relation, kalshi_side: OutcomeSide, polymarket_side: OutcomeSide
) -> Decimal:
    """Minimum payout per unit over all joint states the relation allows."""
    return min(state.payout_per_unit for state in payoff_states(relation, kalshi_side, polymarket_side))


def verified_constructions(relation: Relation) -> list[tuple[OutcomeSide, OutcomeSide, Decimal]]:
    """All two-leg buy portfolios whose guaranteed payout is at least 1 per unit."""
    results = []
    for k_side, p_side in product((OutcomeSide.YES, OutcomeSide.NO), repeat=2):
        payout = guaranteed_payout_per_unit(relation, k_side, p_side)
        if payout >= ONE:
            results.append((k_side, p_side, payout))
    return results
