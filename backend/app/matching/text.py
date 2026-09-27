"""Stage 2 - canonicalization of contract text.

Canonical text is used for comparison only; the original text is always retained on the
market and shown in the dashboard's rule-difference viewer.

Normalization steps: Unicode NFKC, typographic punctuation to ASCII, case-sensitive
expansion of ambiguous abbreviations (``US``/``U.S.``, ``ET``, ``SA``) *before* casefolding,
casefolding, case-insensitive abbreviation and synonym expansion, number and percentage
formatting, whitespace collapse.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Final

_PUNCT_MAP: Final = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        " ": " ",
        "…": "...",
    }
)

# Case-sensitive replacements applied to the original text (ambiguous as lowercase).
_CASE_SENSITIVE: Final = (
    (re.compile(r"\bU\.S\.A?\.?(?=\W|$)"), "United States"),
    (re.compile(r"\bUS\b"), "United States"),
    (re.compile(r"\bUSA\b"), "United States"),
    (re.compile(r"\bET\b"), "Eastern Time"),
    (re.compile(r"\bEST\b|\bEDT\b"), "Eastern Time"),
    (re.compile(r"\bPT\b|\bPST\b|\bPDT\b"), "Pacific Time"),
    (re.compile(r"\bCT\b|\bCST\b|\bCDT\b"), "Central Time"),
    (re.compile(r"\bUTC\b|\bGMT\b"), "UTC"),
    (re.compile(r"\(SA\)|\bSA\b"), "seasonally adjusted"),
    (re.compile(r"\(NSA\)|\bNSA\b"), "not seasonally adjusted"),
)

# Case-insensitive abbreviation / synonym expansion on casefolded text. Order matters.
_EXPANSIONS: Final = (
    (r"≥\s?|>=\s?", "at least "),
    (r"≤\s?|<=\s?", "at most "),
    (r">\s?", "above "),
    (r"<\s?", "below "),
    (r"\bfomc\b", "federal open market committee"),
    (r"\bthe fed\b", "the federal reserve"),
    (r"\bfed\b", "federal reserve"),
    (r"\bfederal reserve board\b", "federal reserve"),
    (r"\becb\b", "european central bank"),
    (r"\bboe\b", "bank of england"),
    (r"\bboj\b", "bank of japan"),
    (r"\bbls\b", "bureau of labor statistics"),
    (r"\bbea\b", "bureau of economic analysis"),
    (r"\bu\.s\. bureau\b", "bureau"),
    (r"(?<=\d)\s?bps?\b", " basis points"),
    (r"\bbps\b|\bbp\b|\bbasis-points?\b", "basis points"),
    (r"\bbasis point\b", "basis points"),
    (r"\by/y\b|\byoy\b|\byear-over-year\b|\byear on year\b|\b12-month\b", "year over year"),
    (r"\bm/m\b|\bmom\b|\bmonth-over-month\b", "month over month"),
    (r"\bq1\b", "first quarter"),
    (r"\bq2\b", "second quarter"),
    (r"\bq3\b", "third quarter"),
    (r"\bq4\b", "fourth quarter"),
    (r"\bgop\b", "republican"),
    (r"\bdems\b|\bdemocrats\b", "democratic"),
    (r"\brepublicans\b", "republican"),
    (r"\bbtc\b", "bitcoin"),
    (r"\beth\b", "ethereum"),
    (r"\bs&p ?500\b|\bspx\b|\bs and p 500\b", "s&p 500"),
    (r"\bu-3\b", "u3"),
    (r"\bpct\b|\bper cent\b", "percent"),
    (r"%", " percent"),
    (r"\bgubernatorial\b", "governor"),
    (r"\blunar\b", "moon"),
    (r"\bmayoral\b", "mayor"),
    (r"\bpresidency\b", "president"),
    (r"\bsenatorial\b", "senate"),
    (r"\bwhite house\b", "president"),
    (r"\bassociated press\b", "ap"),
    (r"\b(?:the )?ap\b", "associated press"),
    (r"\bcf benchmarks?\b", "cf benchmarks"),
    (r"\bbitcoin real ?time index\b|\bbrti\b", "cf benchmarks bitcoin real time index"),
)
_COMPILED_EXPANSIONS: Final = tuple((re.compile(p), r) for p, r in _EXPANSIONS)

_NUMBER_WITH_COMMAS: Final = re.compile(r"(?<=\d),(?=\d{3}\b)")
_MONEY_K: Final = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?k\b")
_WHITESPACE: Final = re.compile(r"\s+")
_WORD: Final = re.compile(r"[a-z0-9&$.]+(?:'[a-z]+)?")

STOPWORDS: Final = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "by",
        "be",
        "is",
        "are",
        "was",
        "were",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "with",
        "from",
        "or",
        "and",
        "if",
        "then",
        "than",
        "into",
        "over",
        "under",
        "per",
        "which",
        "who",
        "whom",
        "what",
        "when",
        "where",
        "whether",
        "any",
        "all",
        "each",
        "other",
        "such",
        "market",
        "resolve",
        "resolves",
        "resolved",
        "resolution",
        "yes",
        "no",
        "otherwise",
        "contract",
        "question",
        "also",
        "only",
        "not",
    ]
)


@dataclass(frozen=True)
class CanonicalText:
    original: str
    canonical: str
    tokens: tuple[str, ...]

    @property
    def token_set(self) -> frozenset[str]:
        return frozenset(self.tokens)


def canonicalize(text: str) -> CanonicalText:
    """Return the canonical form of ``text`` (original preserved)."""
    working = unicodedata.normalize("NFKC", text).translate(_PUNCT_MAP)
    for pattern, replacement in _CASE_SENSITIVE:
        working = pattern.sub(replacement, working)
    working = working.casefold()
    working = _NUMBER_WITH_COMMAS.sub("", working)
    working = _MONEY_K.sub(lambda m: f"${_thousands(m.group(1))}", working)
    for pattern, replacement in _COMPILED_EXPANSIONS:
        working = pattern.sub(replacement, working)
    working = _WHITESPACE.sub(" ", working).strip()
    tokens = tuple(
        token.strip(".")
        for token in _WORD.findall(working)
        if token.strip(".") and token.strip(".") not in STOPWORDS
    )
    return CanonicalText(original=text, canonical=working, tokens=tokens)


def _thousands(value: str) -> str:
    whole, _, fraction = value.partition(".")
    padded = (fraction + "000")[:3]
    return str(int(whole) * 1000 + int(padded))


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard similarity (display/ranking metric only - never used for money)."""
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def char_ngrams(text: str, n: int = 3) -> Counter[str]:
    padded = f"  {text}  "
    return Counter(padded[i : i + n] for i in range(len(padded) - n + 1))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    """Cosine similarity of two sparse count vectors (ranking metric only)."""
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    norm = math.sqrt(sum(v * v for v in left.values())) * math.sqrt(sum(v * v for v in right.values()))
    return dot / norm if norm else 0.0
