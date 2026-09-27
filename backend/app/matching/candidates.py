"""Stage 3 - candidate generation without an unrestricted all-pairs comparison.

Cheap deterministic filters narrow the Kalshi x Polymarket space before any detailed
check runs (the paper reduces its O(2^(n+m)) dependency search the same way, with
timeliness and topical heuristics):

1. **Blocking** - an inverted index maps discriminative keys (content tokens, extracted
   entities, template keys such as ``metric:unemployment_rate``) to Polymarket markets.
   Only markets sharing at least one key with a Kalshi market are compared. Keys that
   occur in more than ``max_key_share`` of markets are ignored for blocking.
2. **Topic/category compatibility** - mapped categories must not conflict.
3. **Temporal compatibility** - resolution times must lie within ``time_window``.
4. **Similarity ranking** - title-token Jaccard, structured-key Jaccard and embedding
   cosine; the top ``max_per_market`` candidates above ``min_similarity`` survive.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.domain.models import NormalizedMarket
from app.matching.embeddings import EmbeddingProvider, cosine_similarity
from app.matching.extract import Proposition
from app.matching.rules import CATEGORY_ALIASES
from app.matching.text import jaccard

TITLE_WEIGHT = 0.5
KEY_WEIGHT = 0.2
EMBEDDING_WEIGHT = 0.3


@dataclass(frozen=True)
class Candidate:
    kalshi: NormalizedMarket
    polymarket: NormalizedMarket
    kalshi_prop: Proposition
    polymarket_prop: Proposition
    similarity: Decimal
    components: dict[str, float]


@dataclass(frozen=True)
class CandidateReport:
    candidates: tuple[Candidate, ...]
    possible_pairs: int
    compared_pairs: int
    filtered_by_category: int
    filtered_by_time: int
    filtered_by_similarity: int


def structured_keys(prop: Proposition) -> frozenset[str]:
    keys = {f"entity:{entity}" for entity in prop.entities}
    for label, value in (
        ("kind", prop.kind if prop.kind != "generic" else None),
        ("metric", prop.metric),
        ("subject", prop.subject),
        ("office", prop.office),
        ("period", prop.period),
    ):
        if value:
            keys.add(f"{label}:{value}")
    keys |= {f"geo:{place}" for place in prop.geography}
    return frozenset(keys)


def _blocking_keys(prop: Proposition) -> frozenset[str]:
    keys = set(prop.title.token_set)
    keys |= {k for k in structured_keys(prop) if not k.startswith(("kind:", "period:"))}
    return frozenset(keys)


def _category(market: NormalizedMarket) -> str | None:
    raw = market.category.strip().lower()
    if not raw:
        return None
    return CATEGORY_ALIASES.get(raw, raw)


def generate_candidates(
    kalshi: list[tuple[NormalizedMarket, Proposition]],
    polymarket: list[tuple[NormalizedMarket, Proposition]],
    *,
    embedder: EmbeddingProvider | None,
    min_similarity: Decimal,
    max_per_market: int,
    time_window: timedelta,
    max_key_share: float = 0.3,
) -> CandidateReport:
    index: dict[str, list[int]] = defaultdict(list)
    for position, (_, prop) in enumerate(polymarket):
        for key in _blocking_keys(prop):
            index[key].append(position)
    cutoff = max(2, int(len(polymarket) * max_key_share))
    usable = {key: positions for key, positions in index.items() if len(positions) <= cutoff}

    embeddings: dict[str, list[float]] = {}

    def embed(market: NormalizedMarket, prop: Proposition) -> list[float] | None:
        if embedder is None:
            return None
        if market.key not in embeddings:
            embeddings[market.key] = embedder.embed(prop.title.canonical)
        return embeddings[market.key]

    candidates: list[Candidate] = []
    compared = filtered_category = filtered_time = filtered_similarity = 0
    for k_market, k_prop in kalshi:
        positions: set[int] = set()
        for key in _blocking_keys(k_prop):
            positions.update(usable.get(key, ()))
        ranked: list[Candidate] = []
        for position in sorted(positions):
            p_market, p_prop = polymarket[position]
            compared += 1
            k_cat, p_cat = _category(k_market), _category(p_market)
            if k_cat and p_cat and k_cat != p_cat:
                filtered_category += 1
                continue
            if (
                k_market.resolution_time
                and p_market.resolution_time
                and (abs(k_market.resolution_time - p_market.resolution_time) > time_window)
            ):
                filtered_time += 1
                continue
            title_sim = jaccard(k_prop.title.token_set, p_prop.title.token_set)
            key_sim = jaccard(structured_keys(k_prop), structured_keys(p_prop))
            k_vec, p_vec = embed(k_market, k_prop), embed(p_market, p_prop)
            embed_sim = cosine_similarity(k_vec, p_vec) if k_vec and p_vec else 0.0
            weight_embed = EMBEDDING_WEIGHT if embedder else 0.0
            total_weight = TITLE_WEIGHT + KEY_WEIGHT + weight_embed
            score = (
                TITLE_WEIGHT * title_sim + KEY_WEIGHT * key_sim + weight_embed * max(embed_sim, 0.0)
            ) / total_weight
            similarity = Decimal(str(round(score, 4)))
            if similarity < min_similarity:
                filtered_similarity += 1
                continue
            ranked.append(
                Candidate(
                    kalshi=k_market,
                    polymarket=p_market,
                    kalshi_prop=k_prop,
                    polymarket_prop=p_prop,
                    similarity=similarity,
                    components={
                        "title_token_jaccard": round(title_sim, 4),
                        "structured_key_jaccard": round(key_sim, 4),
                        "embedding_cosine": round(embed_sim, 4),
                    },
                )
            )
        ranked.sort(key=lambda c: (-c.similarity, c.polymarket.venue_market_id))
        candidates.extend(ranked[:max_per_market])
    return CandidateReport(
        candidates=tuple(candidates),
        possible_pairs=len(kalshi) * len(polymarket),
        compared_pairs=compared,
        filtered_by_category=filtered_category,
        filtered_by_time=filtered_time,
        filtered_by_similarity=filtered_similarity,
    )
