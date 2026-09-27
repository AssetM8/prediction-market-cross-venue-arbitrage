"""The staged matching pipeline (Stages 1-6).

Stage 1 eligibility -> Stage 2 canonicalization and proposition extraction -> Stage 3
candidate generation -> (optional venue enrichment of candidate markets) -> Stage 4
deterministic checks -> Stage 5 relation classification (+ optional LLM veto) ->
Stage 6 conservative contract-level approval. Quote-level approval (both books fresh and
well formed) happens at scan time in :mod:`app.arbitrage.gating`.

Every evaluated pair - approved or rejected - is returned with its full check list, so the
decision is auditable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.ids import stable_id
from app.core.logging import get_logger, log_context, log_event
from app.core.metrics import METRICS
from app.domain.enums import AdjudicationSource, DataSource, Relation, Venue
from app.domain.models import MarketPair, NormalizedMarket, SemanticJudgement
from app.matching.adjudicator import RelationAdjudicator
from app.matching.candidates import generate_candidates
from app.matching.eligibility import Eligibility, check_eligibility
from app.matching.embeddings import EmbeddingProvider
from app.matching.extract import Proposition, extract_proposition
from app.matching.rules import evaluate_pair

logger = get_logger(__name__)

Enricher = Callable[[Sequence[NormalizedMarket]], Awaitable[list[NormalizedMarket]]]


@dataclass(frozen=True)
class MatchingConfig:
    min_confidence: Decimal
    min_similarity: Decimal
    max_candidates_per_market: int
    candidate_time_window: timedelta
    max_resolution_gap: timedelta


@dataclass
class MatchingReport:
    evaluated_at: datetime
    pairs: list[MarketPair]
    eligibility: list[Eligibility]
    stats: dict[str, Any] = field(default_factory=dict)
    enriched_markets: dict[str, NormalizedMarket] = field(default_factory=dict)

    @property
    def approved(self) -> list[MarketPair]:
        return [p for p in self.pairs if p.approved_for_arbitrage_calculation]


class MatchingPipeline:
    def __init__(
        self,
        config: MatchingConfig,
        *,
        embedder: EmbeddingProvider | None,
        adjudicator: RelationAdjudicator | None = None,
    ) -> None:
        self._config = config
        self._embedder = embedder
        self._adjudicator = adjudicator

    async def run(
        self,
        kalshi_markets: Sequence[NormalizedMarket],
        polymarket_markets: Sequence[NormalizedMarket],
        *,
        now: datetime,
        data_source: DataSource,
        enrich_kalshi: Enricher | None = None,
        enrich_polymarket: Enricher | None = None,
    ) -> MatchingReport:
        # Stage 1
        eligibility = [check_eligibility(m, now) for m in [*kalshi_markets, *polymarket_markets]]
        eligible_keys = {e.market_key for e in eligibility if e.eligible}
        kalshi_ok = [m for m in kalshi_markets if m.key in eligible_keys]
        poly_ok = [m for m in polymarket_markets if m.key in eligible_keys]

        # Stage 2
        kalshi_props = [(m, extract_proposition(m)) for m in kalshi_ok]
        poly_props = [(m, extract_proposition(m)) for m in poly_ok]

        # Stage 3
        report = generate_candidates(
            kalshi_props,
            poly_props,
            embedder=self._embedder,
            min_similarity=self._config.min_similarity,
            max_per_market=self._config.max_candidates_per_market,
            time_window=self._config.candidate_time_window,
        )
        candidates = list(report.candidates)

        # Venue enrichment (fees, settlement sources) only for candidate markets.
        replacements: dict[str, NormalizedMarket] = {}
        if enrich_kalshi is not None:
            unique = {c.kalshi.key: c.kalshi for c in candidates}
            for market in await enrich_kalshi(list(unique.values())):
                replacements[market.key] = market
        if enrich_polymarket is not None:
            unique = {c.polymarket.key: c.polymarket for c in candidates}
            for market in await enrich_polymarket(list(unique.values())):
                replacements[market.key] = market
        prop_cache: dict[str, Proposition] = {}

        def resolved(market: NormalizedMarket, prop: Proposition) -> tuple[NormalizedMarket, Proposition]:
            updated = replacements.get(market.key)
            if updated is None or updated.raw_metadata_hash == market.raw_metadata_hash:
                return market, prop
            if updated.key not in prop_cache:
                prop_cache[updated.key] = extract_proposition(updated)
            return updated, prop_cache[updated.key]

        # Stages 4-6
        pairs: list[MarketPair] = []
        for candidate in candidates:
            kalshi, a = resolved(candidate.kalshi, candidate.kalshi_prop)
            poly, b = resolved(candidate.polymarket, candidate.polymarket_prop)
            pair_id = stable_id("pair", kalshi.venue_market_id, poly.venue_market_id)
            with log_context(pair_id=pair_id):
                pairs.append(
                    await self._evaluate(pair_id, kalshi, poly, a, b, candidate.similarity, now, data_source)
                )

        relation_counts: dict[str, int] = {}
        for pair in pairs:
            relation_counts[pair.relation.value] = relation_counts.get(pair.relation.value, 0) + 1
        approved = sum(1 for p in pairs if p.approved_for_arbitrage_calculation)
        METRICS.inc("pairs_evaluated_total", len(pairs))
        METRICS.inc("pairs_approved_total", approved)
        METRICS.inc("pairs_rejected_total", len(pairs) - approved)
        stats = {
            "kalshi_markets": len(kalshi_markets),
            "polymarket_markets": len(polymarket_markets),
            "eligible_kalshi": len(kalshi_ok),
            "eligible_polymarket": len(poly_ok),
            "ineligible": sum(1 for e in eligibility if not e.eligible),
            "possible_pairs": report.possible_pairs,
            "compared_after_blocking": report.compared_pairs,
            "filtered_by_category": report.filtered_by_category,
            "filtered_by_time": report.filtered_by_time,
            "filtered_by_similarity": report.filtered_by_similarity,
            "candidates": len(candidates),
            "approved_pairs": approved,
            "relations": relation_counts,
            "embedding_provider": getattr(self._embedder, "name", "none"),
            "llm_adjudicator": getattr(self._adjudicator, "name", "none"),
        }
        log_event(
            logger, logging.INFO, "matching complete", **{k: v for k, v in stats.items() if k != "relations"}
        )
        return MatchingReport(
            evaluated_at=now,
            pairs=pairs,
            eligibility=eligibility,
            stats=stats,
            enriched_markets=replacements,
        )

    async def _evaluate(
        self,
        pair_id: str,
        kalshi: NormalizedMarket,
        poly: NormalizedMarket,
        a: Proposition,
        b: Proposition,
        similarity: Decimal,
        now: datetime,
        data_source: DataSource,
    ) -> MarketPair:
        evaluation = evaluate_pair(
            kalshi,
            poly,
            a,
            b,
            similarity=similarity,
            min_similarity=self._config.min_similarity,
            min_confidence=self._config.min_confidence,
            max_resolution_gap=self._config.max_resolution_gap,
        )
        relation = evaluation.relation
        judgement = evaluation.judgement
        approved = evaluation.contract_approved
        blocking = list(evaluation.blocking_mismatches)
        reasons = list(evaluation.decision_reasons)
        source = AdjudicationSource.DETERMINISTIC
        if approved and self._adjudicator is not None:
            source = AdjudicationSource.DETERMINISTIC_WITH_LLM_VETO
            llm = await self._adjudicator.adjudicate(kalshi, poly)
            veto = _llm_veto(llm, relation)
            if veto:
                approved = False
                relation = Relation.AMBIGUOUS
                blocking.append(f"llm_veto: {veto}")
                reasons.append(f"LLM adjudicator veto: {veto}")
                judgement = judgement.model_copy(
                    update={
                        "relation": Relation.AMBIGUOUS,
                        "safe_for_cross_venue_arbitrage": False,
                        "reason": f"{judgement.reason}; LLM veto: {veto}",
                    }
                )
            else:
                reasons.append("LLM adjudicator concurred")
        return MarketPair(
            id=pair_id,
            kalshi_market_id=kalshi.venue_market_id,
            polymarket_market_id=poly.venue_market_id,
            kalshi_title=kalshi.title,
            polymarket_title=poly.title,
            similarity_score=similarity,
            relation=relation,
            confidence=evaluation.confidence,
            deterministic_checks=evaluation.checks,
            semantic_explanation=judgement,
            blocking_mismatches=tuple(blocking),
            adjudication_source=source,
            approved_for_arbitrage_calculation=approved,
            decision_reasons=tuple(reasons),
            leg_mappings=evaluation.leg_mappings if approved else (),
            propositions={
                Venue.KALSHI.value: a.summary(),
                Venue.POLYMARKET.value: b.summary(),
                "region_relation": evaluation.region_relation.value if evaluation.region_relation else None,
                "check_groups": evaluation.groups,
                "kalshi_rules": kalshi.rules,
                "polymarket_rules": poly.rules,
                "kalshi_resolution_source": kalshi.resolution_source,
                "polymarket_resolution_source": poly.resolution_source,
            },
            data_source=data_source,
            evaluated_at=now,
        )


def _llm_veto(llm: SemanticJudgement | None, relation: Relation) -> str | None:
    if llm is None:
        return "no valid judgement returned (unavailable or malformed output)"
    if llm.relation is not relation:
        return f"model classified the pair as {llm.relation.value}"
    if not llm.safe_for_cross_venue_arbitrage or not llm.same_resolution_criteria:
        return "model flagged differing resolution criteria: " + "; ".join(llm.differences[:3])
    return None


__all__ = ["MatchingConfig", "MatchingPipeline", "MatchingReport", "SemanticJudgement"]
