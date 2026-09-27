"""Read-only HTTP transport with timeouts, bounded retries, rate limiting and metrics.

The transport deliberately exposes only two operations:

* ``get_json`` - any GET under the configured base URL.
* ``post_json_readonly`` - POST restricted to an explicit allow-list of *read-only* batch
  endpoints (Polymarket ``/books``). Any other POST raises :class:`ReadOnlyViolationError`.

There is no generic ``request`` method, no PUT/PATCH/DELETE, and no header or signing
hook, so order placement cannot be expressed through this layer.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from app.core.clock import Clock, SystemClock
from app.core.logging import get_logger, log_event
from app.core.metrics import METRICS, MetricsRegistry

logger = get_logger(__name__)

RETRIABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
QueryParams = Mapping[str, str | int | float | bool | None]


class VenueHttpError(Exception):
    """A venue request failed after retries (or was not retriable)."""

    def __init__(self, venue: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"{venue}: {message}")
        self.venue = venue
        self.status = status


class VenueAuthRequiredError(VenueHttpError):
    """The venue answered 401: this endpoint needs credentials we deliberately do not use."""


class ReadOnlyViolationError(RuntimeError):
    """Raised when code attempts a non-allow-listed mutation-capable request."""


class Transport(Protocol):
    """Minimal read-only transport used by venue adapters."""

    @property
    def name(self) -> str: ...

    @property
    def last_success_at(self) -> datetime | None: ...

    @property
    def last_error(self) -> str | None: ...

    async def get_json(self, path: str, params: QueryParams | None = None) -> Any: ...

    async def post_json_readonly(self, path: str, body: Any) -> Any: ...

    async def aclose(self) -> None: ...


class AsyncRateLimiter:
    """Simple async token bucket: at most ``rate`` acquisitions per second on average."""

    def __init__(
        self,
        rate_per_second: float,
        *,
        burst: int = 1,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate_per_second
        self._capacity = float(max(burst, 1))
        self._tokens = self._capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._updated = monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._rate)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter.

    Delay before attempt ``n`` (n >= 2) is ``uniform(0, min(max, base * 2 ** (n - 2)))``.
    A ``Retry-After`` header (seconds) is honoured but capped at ``max_delay``.
    """

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int, rng: random.Random, retry_after: float | None) -> float:
        ceiling = min(self.max_delay, self.base_delay * (2 ** max(attempt - 2, 0)))
        jittered = rng.uniform(0, ceiling)
        if retry_after is not None:
            return min(self.max_delay, max(retry_after, jittered))
        return jittered


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class ReadOnlyHttpTransport:
    """httpx-based transport implementing :class:`Transport` for one venue base URL."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        connect_timeout: float,
        read_timeout: float,
        rate_per_second: float,
        retry: RetryPolicy,
        readonly_post_paths: Iterable[str] = (),
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        metrics: MetricsRegistry = METRICS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "pm-arb-readonly/0.1"},
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._limiter = AsyncRateLimiter(rate_per_second, sleep=sleep)
        self._retry = retry
        self._readonly_post_paths = frozenset(readonly_post_paths)
        self._clock = clock or SystemClock()
        self._metrics = metrics
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not cryptography
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def last_success_at(self) -> datetime | None:
        return self._last_success_at

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def get_json(self, path: str, params: QueryParams | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        return await self._send("GET", path, params=clean, body=None)

    async def post_json_readonly(self, path: str, body: Any) -> Any:
        if path not in self._readonly_post_paths:
            raise ReadOnlyViolationError(
                f"{self._name}: POST {path} is not an allow-listed read-only endpoint"
            )
        return await self._send("POST", path, params=None, body=body)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _send(self, method: str, path: str, *, params: dict[str, Any] | None, body: Any) -> Any:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            await self._limiter.acquire()
            self._metrics.inc("http_requests_total", venue=self._name, method=method)
            retry_after: float | None = None
            try:
                if method == "GET":
                    response = await self._client.get(url, params=params)
                else:
                    response = await self._client.post(url, json=body)
            except httpx.TransportError as exc:
                last_exc = exc
                self._record_error(f"transport error: {type(exc).__name__}: {exc}")
                self._metrics.inc("http_errors_total", venue=self._name, kind="transport")
            else:
                if response.status_code < 400:
                    self._last_success_at = self._clock.now()
                    self._last_error = None
                    self._metrics.mark("venue_last_success", self._last_success_at, venue=self._name)
                    try:
                        return response.json()
                    except ValueError as exc:
                        self._record_error(f"invalid JSON from {path}")
                        raise VenueHttpError(self._name, f"invalid JSON from {path}") from exc
                status = response.status_code
                self._metrics.inc("http_errors_total", venue=self._name, kind=str(status))
                message = f"HTTP {status} for {method} {path}"
                self._record_error(message)
                if status == 401:
                    raise VenueAuthRequiredError(self._name, message, status=status)
                if status not in RETRIABLE_STATUS:
                    raise VenueHttpError(self._name, message, status=status)
                retry_after = _parse_retry_after(response)
                last_exc = VenueHttpError(self._name, message, status=status)
            if attempt < self._retry.max_attempts:
                delay = self._retry.delay(attempt + 1, self._rng, retry_after)
                self._metrics.inc("http_retries_total", venue=self._name)
                log_event(
                    logger,
                    logging.INFO,
                    "retrying venue request",
                    venue=self._name,
                    path=path,
                    attempt=attempt,
                    delay_seconds=round(delay, 3),
                )
                await self._sleep(delay)
        if last_exc is None:
            raise VenueHttpError(self._name, "request failed without a recorded error")
        if isinstance(last_exc, VenueHttpError):
            raise last_exc
        raise VenueHttpError(self._name, f"request failed: {last_exc}") from last_exc

    def _record_error(self, message: str) -> None:
        self._last_error = message
        log_event(logger, logging.WARNING, "venue request failed", venue=self._name, error=message)
