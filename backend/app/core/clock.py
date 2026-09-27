"""Clock abstraction.

All timestamps are timezone-aware UTC internally. Fixture mode uses a
:class:`FrozenClock` pinned to the fixture set's ``as_of`` instant so that staleness
checks, opportunity expiry and paper execution are deterministic. The dashboard labels
fixture data and shows the simulated clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""
        ...

    @property
    def simulated(self) -> bool:
        """True when the clock does not follow wall-clock time."""
        ...


class SystemClock:
    """Wall-clock UTC time."""

    @property
    def simulated(self) -> bool:
        return False

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock:
    """A clock pinned to a fixed instant, optionally advanced manually (tests, fixtures)."""

    def __init__(self, instant: datetime) -> None:
        self._instant = ensure_utc(instant)

    @property
    def simulated(self) -> bool:
        return True

    def now(self) -> datetime:
        return self._instant

    def advance(self, delta: timedelta) -> None:
        self._instant = self._instant + delta

    def set(self, instant: datetime) -> None:
        self._instant = ensure_utc(instant)


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime; naive values are rejected."""
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not allowed; supply a timezone")
    return value.astimezone(UTC)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` suffix accepted) into aware UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        # Venue timestamps without an offset are documented as UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def from_epoch_millis(value: int | str) -> datetime:
    """Convert epoch milliseconds (int or numeric string) into aware UTC."""
    millis = int(value)
    seconds, remainder = divmod(millis, 1000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=remainder)
