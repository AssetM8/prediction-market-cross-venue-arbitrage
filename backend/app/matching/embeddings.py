"""Optional embedding support for candidate ranking.

Embeddings only *rank* candidates in Stage 3; they never approve a pair. The default
:class:`HashingEmbeddingProvider` is a deterministic, dependency-free hashed bag of
character trigrams (a local "embedding" that needs no model download and no network).
To use a neural model, implement :class:`EmbeddingProvider` (e.g. wrapping
sentence-transformers) and pass it to :class:`app.matching.pipeline.MatchingPipeline`.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.matching.text import char_ngrams


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, text: str) -> list[float]: ...


class HashingEmbeddingProvider:
    """Signed feature hashing of character trigrams into ``dimensions`` buckets."""

    name = "hashing-char3"

    def __init__(self, dimensions: int = 512) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for gram, count in char_ngrams(text.lower()).items():
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * count
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine of two dense vectors (ranking only; never used for money)."""
    if len(left) != len(right):
        raise ValueError("vector dimensions differ")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0
