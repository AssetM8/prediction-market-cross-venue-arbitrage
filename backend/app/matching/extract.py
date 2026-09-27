"""Stage 2/4 support - deterministic proposition extraction from contract text.

Each binary market's YES outcome is turned into a :class:`Proposition`: a structured
description of *what* is measured (kind, subject, metric, office, geography, reference
period), *which values resolve YES* (an :class:`IntervalSet` on a value or time axis) and
*how resolution works* (sources, first-release vs final, revisions, cancellation,
suspension, recounts, conditional clauses, rounding, seasonal adjustment, winner
structure).

Extraction is intentionally conservative: when a field cannot be read unambiguously it is
left as ``None`` and the deterministic checks report ``UNKNOWN``, which blocks approval of
an equivalence. Nothing here is probabilistic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

from app.domain.models import NormalizedMarket
from app.matching.intervals import IntervalSet
from app.matching.text import CanonicalText, canonicalize

MONTHS: Final = {
    name: index
    for index, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_RE: Final = "|".join(sorted(MONTHS, key=len, reverse=True))

TIMEZONES: Final = {
    "eastern time": "America/New_York",
    "pacific time": "America/Los_Angeles",
    "central time": "America/Chicago",
    "utc": "UTC",
}

US_STATES: Final = frozenset(
    [
        "alabama",
        "alaska",
        "arizona",
        "arkansas",
        "california",
        "colorado",
        "connecticut",
        "delaware",
        "florida",
        "georgia",
        "hawaii",
        "idaho",
        "illinois",
        "indiana",
        "iowa",
        "kansas",
        "kentucky",
        "louisiana",
        "maine",
        "maryland",
        "massachusetts",
        "michigan",
        "minnesota",
        "mississippi",
        "missouri",
        "montana",
        "nebraska",
        "nevada",
        "ohio",
        "oklahoma",
        "oregon",
        "pennsylvania",
        "tennessee",
        "texas",
        "utah",
        "vermont",
        "virginia",
        "washington",
        "wisconsin",
        "wyoming",
    ]
) | frozenset(
    {
        "new hampshire",
        "new jersey",
        "new mexico",
        "new york",
        "north carolina",
        "north dakota",
        "rhode island",
        "south carolina",
        "south dakota",
        "west virginia",
    }
)
COUNTRIES: Final = frozenset(
    {
        "united states",
        "united kingdom",
        "canada",
        "mexico",
        "germany",
        "france",
        "japan",
        "china",
        "india",
        "brazil",
        "euro area",
        "eurozone",
    }
)

OFFICES: Final = (
    ("vice president", "vice_president"),
    ("vice presidential", "vice_president"),
    ("president", "president"),
    ("presidential", "president"),
    ("senate", "senate"),
    ("senator", "senate"),
    ("house of representatives", "house"),
    ("congressional district", "house"),
    ("governor", "governor"),
    ("mayor", "mayor"),
    ("attorney general", "attorney_general"),
    ("prime minister", "prime_minister"),
)

INSTITUTIONS: Final = (
    ("federal reserve", "federal_reserve"),
    ("european central bank", "ecb"),
    ("bank of england", "boe"),
    ("bank of japan", "boj"),
)

SOURCES: Final = (
    ("bureau of labor statistics", "bls"),
    ("bls.gov", "bls"),
    ("bureau of economic analysis", "bea"),
    ("bea.gov", "bea"),
    ("federalreserve.gov", "federal_reserve"),
    ("federal open market committee statement", "federal_reserve"),
    ("federal reserve", "federal_reserve"),
    ("ecb.europa.eu", "ecb"),
    ("european central bank", "ecb"),
    ("associated press", "associated_press"),
    ("fox news", "fox_news"),
    ("nbc", "nbc"),
    ("cnn", "cnn"),
    ("cf benchmarks", "cf_benchmarks"),
    ("binance", "binance"),
    ("coinbase", "coinbase"),
    ("s&p dow jones indices", "sp_dow_jones_indices"),
    ("nasa", "nasa"),
    ("certified results", "certified_results"),
    ("secretary of state", "secretary_of_state"),
)

ASSETS: Final = (
    ("bitcoin", "bitcoin"),
    ("ethereum", "ethereum"),
    ("s&p 500", "sp500"),
    ("nasdaq", "nasdaq"),
    ("gold", "gold"),
    ("crude oil", "crude_oil"),
)

NAME_STOPWORDS: Final = frozenset(
    {
        "will",
        "the",
        "yes",
        "no",
        "if",
        "this",
        "market",
        "senate",
        "house",
        "governor",
        "president",
        "election",
        "general",
        "primary",
        "united",
        "states",
        "federal",
        "reserve",
        "democratic",
        "republican",
        "party",
        "associated",
        "press",
        "eastern",
        "time",
        "bureau",
        "labor",
        "statistics",
        "consumer",
        "price",
        "index",
        "october",
        "november",
        "december",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "fomc",
        "cpi",
        "gdp",
        "us",
        "u.s.",
        "fox",
        "news",
        "nbc",
        "cnn",
        "ap",
        "mayor",
        "mayoral",
        "gubernatorial",
        "vice",
        "who",
        "what",
        "when",
        "nominee",
        "candidate",
        "popular",
        "vote",
        "state",
        "secretary",
        "certified",
        "official",
    }
)

_NUMBER: Final = r"\$?(-?\d+(?:\.\d+)?)"
_UNIT: Final = r"(?:\s*(percent|basis points|dollars|usd))?"

# (pattern, lower_or_upper, inclusive). Longer phrases first so "at least" wins over "least".
_COMPARATORS: Final = (
    (rf"(?:greater than or equal to|at least|no less than|not less than)\s+{_NUMBER}{_UNIT}", "lower", True),
    (rf"{_NUMBER}{_UNIT}\s+or (?:more|higher|above|greater)", "lower", True),
    (
        rf"(?:less than or equal to|at most|no more than|not more than|at or below)\s+{_NUMBER}{_UNIT}",
        "upper",
        True,
    ),
    (rf"{_NUMBER}{_UNIT}\s+or (?:less|lower|below|fewer)", "upper", True),
    (
        rf"(?:greater than|more than|higher than|above|exceeds?|exceeding|over|strictly greater than)\s+{_NUMBER}{_UNIT}",
        "lower",
        False,
    ),
    (rf"(?:less than|lower than|below|under|fewer than)\s+{_NUMBER}{_UNIT}", "upper", False),
)
_COMPILED_COMPARATORS: Final = tuple((re.compile(p), side, inc) for p, side, inc in _COMPARATORS)

_DATE_MDY: Final = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b")
_DATE_ISO: Final = re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b")
_MONTH_YEAR: Final = re.compile(rf"\b({_MONTH_RE})\.?\s+(20\d\d)\b")
_QUARTER: Final = re.compile(r"\b(first|second|third|fourth) quarter(?: of)?\s+(20\d\d)\b")
_QUARTER_ALT: Final = re.compile(r"\b(20\d\d)\s+(first|second|third|fourth) quarter\b")
_YEAR: Final = re.compile(r"\b(20\d\d)\b")
_CLOCK: Final = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)")
_CLOCK_24: Final = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*(?:am|pm|a\.m|p\.m))")
_QUARTERS: Final = {"first": 1, "second": 2, "third": 3, "fourth": 4}


@dataclass(frozen=True)
class Deadline:
    instant: datetime
    semantics: str  # "by" | "before"
    has_time: bool
    timezone: str | None


@dataclass(frozen=True)
class Proposition:
    """Structured reading of a market's YES proposition (see module docstring)."""

    venue_market_id: str
    kind: str
    title: CanonicalText
    body: CanonicalText
    subject: str | None = None
    entities: frozenset[str] = frozenset()
    predicate: str | None = None
    metric: str | None = None
    office: str | None = None
    geography: frozenset[str] = frozenset()
    period: str | None = None
    region: IntervalSet | None = None
    region_axis: str | None = None
    region_text: str | None = None
    region_ambiguous: bool = False
    deadline: Deadline | None = None
    observation: str | None = None
    timezone: str | None = None
    sources: frozenset[str] = frozenset()
    finality: str | None = None
    revision: str | None = None
    cancellation: frozenset[str] = frozenset()
    suspension: frozenset[str] = frozenset()
    recount: frozenset[str] = frozenset()
    conditional: str | None = None
    rounding: str | None = None
    seasonal: str | None = None
    winner_structure: str | None = None
    negated: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, object]:
        """JSON-friendly view for audit trails and the rule-difference viewer."""
        return {
            "kind": self.kind,
            "subject": self.subject,
            "entities": sorted(self.entities),
            "predicate": self.predicate,
            "metric": self.metric,
            "office": self.office,
            "geography": sorted(self.geography),
            "period": self.period,
            "yes_region": self.region_text,
            "region_axis": self.region_axis,
            "deadline": self.deadline.instant.isoformat() if self.deadline else None,
            "deadline_semantics": self.deadline.semantics if self.deadline else None,
            "observation": self.observation,
            "timezone": self.timezone,
            "sources": sorted(self.sources),
            "finality": self.finality,
            "revision": self.revision,
            "cancellation": sorted(self.cancellation),
            "suspension": sorted(self.suspension),
            "recount": sorted(self.recount),
            "conditional": self.conditional,
            "rounding": self.rounding,
            "seasonal_adjustment": self.seasonal,
            "winner_structure": self.winner_structure,
            "negated": self.negated,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _find_first(text: str, table: tuple[tuple[str, str], ...]) -> str | None:
    best: tuple[int, str] | None = None
    for phrase, label in table:
        match = re.search(rf"\b{re.escape(phrase)}\b", text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), label)
    return best[1] if best else None


def _find_all(text: str, table: tuple[tuple[str, str], ...]) -> frozenset[str]:
    found = set()
    for phrase, label in table:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text):
            found.add(label)
    return frozenset(found)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+|\n+", text) if s.strip()]


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _timezone(text: str) -> str | None:
    found = {iana for phrase, iana in TIMEZONES.items() if re.search(rf"\b{phrase}\b", text)}
    return found.pop() if len(found) == 1 else ("MULTIPLE" if found else None)


def _parse_dates(text: str) -> list[tuple[int, date]]:
    dates: list[tuple[int, date]] = []
    for match in _DATE_MDY.finditer(text):
        try:
            dates.append(
                (match.start(), date(int(match.group(3)), MONTHS[match.group(1)], int(match.group(2))))
            )
        except ValueError:
            continue
    for match in _DATE_ISO.finditer(text):
        try:
            dates.append((match.start(), date(int(match.group(1)), int(match.group(2)), int(match.group(3)))))
        except ValueError:
            continue
    return sorted(dates)


def _clock_after(text: str, start: int) -> time | None:
    window = text[max(0, start - 40) : start + 60]
    match = _CLOCK.search(window)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(3).startswith("p"):
            hour += 12
        return time(hour, int(match.group(2) or 0))
    match24 = _CLOCK_24.search(window)
    if match24:
        return time(int(match24.group(1)), int(match24.group(2)))
    return None


def _to_epoch(moment: datetime) -> Decimal:
    delta = moment.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / Decimal(1_000_000)


# --------------------------------------------------------------------------------------
# field extractors
# --------------------------------------------------------------------------------------


def _extract_threshold(title: str, body: str) -> tuple[IntervalSet | None, str | None, str | None, bool]:
    """Return (yes_region, unit, description, ambiguous) for value comparisons."""
    for source_text in (title, body):
        found: list[tuple[Decimal, str, bool, str | None]] = []
        for pattern, side, inclusive in _COMPILED_COMPARATORS:
            for match in pattern.finditer(source_text):
                value = _decimal(match.group(1))
                if value is None:
                    continue
                unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
                entry = (value, side, inclusive, unit)
                # an "or more"-style match can overlap a plain one; keep distinct readings only
                if entry not in found:
                    found.append(entry)
        # drop readings subsumed by an inclusive phrase at the same value/side
        distinct = {(v, s, i) for v, s, i, _ in found}
        if not distinct:
            continue
        if len(distinct) > 1:
            inclusive_keys = {(v, s) for v, s, i in distinct if i}
            distinct = {(v, s, i) for v, s, i in distinct if i or (v, s) not in inclusive_keys}
        if len(distinct) != 1:
            return None, None, f"multiple thresholds: {sorted(distinct)}", True
        value, side, inclusive = distinct.pop()
        units = {u for v, s, i, u in found if (v, s, i) == (value, side, inclusive) and u}
        unit = units.pop() if len(units) == 1 else None
        region = (
            IntervalSet.greater_than(value, inclusive=inclusive)
            if side == "lower"
            else IntervalSet.less_than(value, inclusive=inclusive)
        )
        return region, unit, region.describe(), False
    return None, None, None, False


def _extract_policy_region(text: str) -> tuple[IntervalSet | None, str | None]:
    """YES region on the axis "policy-rate change in basis points" (cuts are negative)."""
    cut = re.search(r"\b(cut|cuts|lower|lowers|lowered|decrease|decreases|reduce|reduces|reduction)\b", text)
    hike = re.search(r"\b(hike|hikes|raise|raises|increase|increases|increased)\b", text)
    hold = re.search(r"\b(no change|unchanged|hold|holds|maintain|maintains|keep|keeps)\b", text)
    if hold and not cut and not hike:
        return IntervalSet.point(Decimal(0)), "hold (no change)"
    if bool(cut) == bool(hike):
        return None, None
    sign = Decimal(-1) if cut else Decimal(1)
    verb = "cut" if cut else "hike"
    strictly_more = re.search(
        r"(?:more than|greater than|over|above|exceeding|larger than)\s+(\d+(?:\.\d+)?)\s*basis points", text
    )
    at_least = re.search(
        r"(?:at least\s+(\d+(?:\.\d+)?)\s*basis points)|(?:(\d+(?:\.\d+)?)\s*basis points or more)", text
    )
    exact = re.search(r"(\d+(?:\.\d+)?)\s*basis points", text)
    if strictly_more:
        magnitude = Decimal(strictly_more.group(1)) * sign
        region = (
            IntervalSet.less_than(magnitude, inclusive=False)
            if sign < 0
            else IntervalSet.greater_than(magnitude, inclusive=False)
        )
        return region, f"{verb} of more than {abs(magnitude)} basis points"
    if at_least:
        magnitude = Decimal(at_least.group(1) or at_least.group(2)) * sign
        region = (
            IntervalSet.less_than(magnitude, inclusive=True)
            if sign < 0
            else IntervalSet.greater_than(magnitude, inclusive=True)
        )
        return region, f"{verb} of at least {abs(magnitude)} basis points"
    if exact:
        magnitude = Decimal(exact.group(1)) * sign
        return IntervalSet.point(magnitude), f"{verb} of exactly {abs(magnitude)} basis points"
    region = (
        IntervalSet.less_than(Decimal(0), inclusive=False)
        if sign < 0
        else IntervalSet.greater_than(Decimal(0), inclusive=False)
    )
    return region, f"{verb} (any size)"


def _extract_period(title: str, body: str) -> str | None:
    for text in (title, body):
        quarter = _QUARTER.search(text)
        if quarter:
            return f"{quarter.group(2)}-q{_QUARTERS[quarter.group(1)]}"
        quarter_alt = _QUARTER_ALT.search(text)
        if quarter_alt:
            return f"{quarter_alt.group(1)}-q{_QUARTERS[quarter_alt.group(2)]}"
        month_year = _MONTH_YEAR.search(text)
        if month_year:
            return f"{month_year.group(2)}-{MONTHS[month_year.group(1)]:02d}"
        mdy = _DATE_MDY.search(text)
        if mdy:
            return f"{mdy.group(3)}-{MONTHS[mdy.group(1)]:02d}"
        year = _YEAR.search(text)
        if year:
            return year.group(1)
    return None


def _extract_deadline(text: str, tz_name: str | None) -> Deadline | None:
    patterns = (
        (r"\b(?:on or before|no later than|by the end of|by end of|by)\s+", "by"),
        (r"\b(?:before|prior to)\s+", "before"),
    )
    candidates: list[tuple[int, str, str]] = []
    for prefix, semantics in patterns:
        for match in re.finditer(
            prefix + rf"((?:{_MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d\d|20\d\d-\d\d-\d\d|20\d\d)",
            text,
        ):
            candidates.append((match.start(), semantics, match.group(1)))
    if not candidates:
        return None
    # the earliest-mentioned deadline phrase is the proposition's deadline
    start, semantics, raw = sorted(candidates)[0]
    zone = ZoneInfo(tz_name) if tz_name and tz_name != "MULTIPLE" else UTC
    dates = _parse_dates(raw)
    if dates:
        day = dates[0][1]
    elif re.fullmatch(r"20\d\d", raw):
        day = date(int(raw), 12, 31) if semantics == "by" else date(int(raw), 1, 1)
    else:
        return None
    clock = _clock_after(text, start)
    if clock is not None:
        instant = datetime.combine(day, clock, tzinfo=zone)
        return Deadline(instant=instant.astimezone(UTC), semantics=semantics, has_time=True, timezone=tz_name)
    if semantics == "by":
        # "by D" includes all of D: YES region is t < start of the following day.
        instant = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)
        return Deadline(instant=instant.astimezone(UTC), semantics="before", has_time=False, timezone=tz_name)
    instant = datetime.combine(day, time(0, 0), tzinfo=zone)
    return Deadline(instant=instant.astimezone(UTC), semantics="before", has_time=False, timezone=tz_name)


def _deadline_region(deadline: Deadline) -> IntervalSet:
    upper = _to_epoch(deadline.instant)
    inclusive = deadline.semantics == "by"
    return IntervalSet.less_than(upper, inclusive=inclusive)


def _extract_observation(text: str, tz_name: str | None) -> str | None:
    dates = _parse_dates(text)
    if not dates:
        return None
    start, day = dates[0]
    clock = _clock_after(text, start)
    zone = tz_name or "unspecified-tz"
    if clock is None:
        return f"{day.isoformat()}@{zone}"
    return f"{day.isoformat()}T{clock.strftime('%H:%M')}@{zone}"


def _sentence_outcomes(text: str, trigger: str) -> frozenset[str]:
    outcomes: set[str] = set()
    for sentence in _sentences(text):
        if not re.search(trigger, sentence):
            continue
        if re.search(r"50-50|50/50|fifty|\$0?\.50 per|0\.5 each|split", sentence):
            outcomes.add("split")
        elif re.search(r"reschedul|next scheduled|new date|following meeting|will be used", sentence):
            outcomes.add("follows_rescheduled")
        elif re.search(r"remain open|extended|extend", sentence):
            outcomes.add("extended")
        elif re.search(r"\b(resolve[sd]?|settle[sd]?)\b[^.]*\bno\b|\bno\b[^.]*\bresolve", sentence):
            outcomes.add("resolves_no")
        elif re.search(r"\b(resolve[sd]?|settle[sd]?)\b[^.]*\byes\b", sentence):
            outcomes.add("resolves_yes")
        else:
            outcomes.add("mentioned_without_outcome")
    return frozenset(outcomes)


def _extract_finality(text: str) -> tuple[str | None, str | None]:
    finality = None
    if re.search(
        r"first (?:reported|released|published|release)|initial(?:ly)? (?:reported|released|published|release)|"
        r"advance estimate|preliminary|first print|as first",
        text,
    ):
        finality = "first_release"
    elif re.search(r"\bfinal (?:estimate|reading|release|results?)\b|after all revisions|certified", text):
        finality = "final"
    revision = None
    if re.search(
        r"revisions?[^.]*(?:not be|will not|won't|are not|shall not)[^.]*(?:consider|count|taken|affect|used)|"
        r"(?:subsequent|later|future) revisions?[^.]*(?:ignored|disregarded|no effect|not affect|will not)|"
        r"regardless of (?:any )?(?:subsequent |later )?revisions?|without regard to (?:any )?revisions?",
        text,
    ):
        revision = "ignored"
    elif re.search(
        r"revised (?:figure|number|value|estimate)s? (?:will be|is) used|including revisions", text
    ):
        revision = "revised_used"
    return finality, revision


def _extract_rounding(text: str) -> str | None:
    match = re.search(
        r"(?:rounded|reported|round(?:ing)?)\s+(?:to\s+)?(?:the nearest\s+)?"
        r"(one decimal(?: place)?|a single decimal|tenth|0\.1|two decimal(?: places)?|hundredth|0\.01|whole (?:number|percent))",
        text,
    )
    if not match:
        return None
    token = match.group(1)
    if token.startswith(("one", "a single", "tenth", "0.1")):
        return "0.1"
    if token.startswith(("two", "hundredth", "0.01")):
        return "0.01"
    return "1"


def _extract_person_names(original_title: str) -> frozenset[str]:
    """Heuristic person-name reader: runs of 2-3 capitalized non-stopwords in the title."""
    names: set[str] = set()
    for match in re.finditer(r"\b[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+)*\b", original_title):
        run: list[str] = []
        for word in [*match.group(0).split(), ""]:
            lowered = word.lower()
            if word and lowered not in NAME_STOPWORDS and lowered not in US_STATES:
                run.append(lowered)
                continue
            if 2 <= len(run) <= 3:
                candidate = " ".join(run)
                if candidate not in US_STATES and candidate not in COUNTRIES:
                    names.add(candidate)
            run = []
    return frozenset(names)


def _geography(text: str) -> frozenset[str]:
    found = set()
    for place in US_STATES | COUNTRIES:
        if re.search(rf"\b{re.escape(place)}\b", text):
            found.add(place)
    # "west virginia" also contains "virginia"; keep the longest match only
    return frozenset(p for p in found if not any(p != q and p in q for q in found))


# --------------------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------------------


def extract_proposition(market: NormalizedMarket) -> Proposition:
    """Build the :class:`Proposition` for ``market``'s YES outcome."""
    title = canonicalize(market.title)
    body_text = "\n".join(
        part for part in (market.rules, market.description, market.resolution_source or "") if part
    )
    body = canonicalize(body_text)
    t, b = title.canonical, body.canonical
    both = f"{t}\n{b}"
    notes: list[str] = []

    tz_name = _timezone(both)
    sources = _find_all(canonicalize(market.resolution_source or "").canonical, SOURCES) | _find_all(
        b, SOURCES
    )
    finality, revision = _extract_finality(b)
    cancellation = _sentence_outcomes(b, r"cancel|postpone|delay")
    suspension = _sentence_outcomes(b, r"suspend|halt|early close|closes? early")
    recount = _sentence_outcomes(b, r"recount|contested|legal challenge")
    conditional_match = re.search(r"(conditional on|contingent on|provided that|assuming that)[^.]*", b)
    conditional = conditional_match.group(0) if conditional_match else None
    rounding = _extract_rounding(b)
    seasonal = None
    if re.search(r"not seasonally adjusted", both):
        seasonal = "nsa"
    elif re.search(r"seasonally adjusted", both):
        seasonal = "sa"
    negated = bool(re.search(r"\b(will not|won't|fail to|fails to|does not|not be)\b", t))

    institution = _find_first(t, INSTITUTIONS) or _find_first(b, INSTITUTIONS)
    asset = _find_first(t, ASSETS)
    office = _find_first(t, OFFICES) or _find_first(b, OFFICES)
    is_election = bool(re.search(r"\b(election|nomination|primary|popular vote|electoral college)\b", both))

    kind = "generic"
    subject: str | None = None
    predicate: str | None = None
    metric: str | None = None
    region: IntervalSet | None = None
    region_axis: str | None = None
    region_text: str | None = None
    region_ambiguous = False
    deadline: Deadline | None = None
    observation: str | None = None
    winner_structure: str | None = None
    entities: set[str] = set()

    economic_metric = _economic_metric(both)
    if is_election and office:
        kind = "election"
        if re.search(r"\bpopular vote\b", both):
            predicate = "win_popular_vote"
        elif re.search(r"\bnomination\b|\bbe the (?:democratic |republican )?nominee\b", t):
            predicate = "win_nomination"
        elif re.search(r"\bprimary\b", t):
            predicate = "win_primary"
        else:
            predicate = "win_election"
        persons = _extract_person_names(market.title)
        party = re.search(r"\b(democratic|republican|libertarian|green|independent)\b", t)
        if persons:
            subject = sorted(persons)[0]
            entities |= persons
            if len(persons) > 1:
                notes.append("multiple person names in title")
        elif party:
            subject = f"party:{party.group(1)}"
        entities |= {subject} if subject else set()
        winner_structure = (
            "multi" if re.search(r"\btop (?:two|2|three|3)\b|\bone of\b|\bany of\b", t) else "single"
        )
    elif institution and re.search(
        r"\b(interest rates?|target range|federal funds|deposit facility|policy rate|bank rate|"
        r"basis points|cut|cuts|hike|hikes|hold|holds|decision|decreases?|increases?)\b",
        t,
    ):
        kind = "policy_decision"
        subject = institution
        entities.add(institution)
        metric = f"policy_rate_change:{institution}"
        region, region_text = _extract_policy_region(t)
        if region is None:
            region, region_text = _extract_policy_region(b)
        region_axis = "value:basis_points_change"
    elif economic_metric:
        kind = "economic_data"
        metric = economic_metric
        subject = economic_metric
        region, unit, region_text, region_ambiguous = _extract_threshold(t, b)
        region_axis = f"value:{unit or 'percent'}"
    elif asset:
        kind = "asset_price"
        subject = asset
        entities.add(asset)
        metric = f"price:{asset}"
        region, unit, region_text, region_ambiguous = _extract_threshold(t, b)
        region_axis = f"value:{unit or 'usd'}"
        observation = _extract_observation(t, tz_name) or _extract_observation(b, tz_name)
    else:
        deadline = _extract_deadline(t, tz_name) or _extract_deadline(b, tz_name)
        if deadline is not None:
            kind = "occurrence"
            region = _deadline_region(deadline)
            region_axis = "time"
            region_text = (
                f"event time {'<=' if deadline.semantics == 'by' else '<'} {deadline.instant.isoformat()}"
            )
            entities |= set(_extract_person_names(market.title))
            predicate = "occurs_by_deadline"
            subject = " ".join(sorted(_topic_tokens(title)))

    if negated and region is not None:
        region = region.complement()
        region_text = f"NOT({region_text})"
        notes.append("title negation applied to YES region")
    elif negated:
        notes.append("negated proposition without a region; treated as unknown polarity")

    return Proposition(
        venue_market_id=market.venue_market_id,
        kind=kind,
        title=title,
        body=body,
        subject=subject,
        entities=frozenset(entities),
        predicate=predicate,
        metric=metric,
        office=office,
        geography=_geography(t) or _geography(b),
        period=_extract_period(t, b) if kind != "occurrence" else None,
        region=region,
        region_axis=region_axis,
        region_text=region_text,
        region_ambiguous=region_ambiguous,
        deadline=deadline,
        observation=observation,
        timezone=tz_name,
        sources=sources,
        finality=finality,
        revision=revision,
        cancellation=cancellation,
        suspension=suspension,
        recount=recount,
        conditional=conditional,
        rounding=rounding,
        seasonal=seasonal,
        winner_structure=winner_structure,
        negated=negated,
        notes=tuple(notes),
    )


def _economic_metric(text: str) -> str | None:
    if re.search(r"\bunemployment rate\b", text):
        return "unemployment_rate"
    if re.search(r"\bcore (?:cpi|consumer price index)\b", text):
        return "core_cpi_yoy" if "year over year" in text else "core_cpi"
    if re.search(r"\bcpi\b|\bconsumer price index\b|\binflation\b", text):
        if "year over year" in text:
            return "cpi_yoy"
        if "month over month" in text:
            return "cpi_mom"
        return "cpi"
    if re.search(r"\bgdp\b|\bgross domestic product\b", text):
        return "real_gdp_growth_annualized" if "annualized" in text or "annual rate" in text else "gdp_growth"
    if re.search(r"\bnonfarm payrolls?\b", text):
        return "nonfarm_payrolls"
    return None


_TOPIC_STOP: Final = frozenset(set(MONTHS) | {"occur", "happen", "take", "place", "before", "by", "end"})


def _topic_tokens(title: CanonicalText) -> frozenset[str]:
    return frozenset(
        token
        for token in title.tokens
        if token not in _TOPIC_STOP and not re.fullmatch(r"\d+(?:st|nd|rd|th)?,?", token)
    )
