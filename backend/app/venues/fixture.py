"""Fixture transport: replays checked-in raw venue payloads through the real adapters.

Fixture files under ``fixtures/`` use the venues' documented wire formats (Kalshi
``/events`` pages and ``orderbook_fp`` books; Polymarket Gamma keyset pages and CLOB book
summaries). The Kalshi and Polymarket adapters therefore run their real normalization code
in fixture mode; only the byte source differs. Every record produced this way carries
``data_source=fixture`` and the dashboard labels it as such.

The fixture set is synthetic: market names, prices and dates are invented for a
deterministic demonstration and do not describe real listings.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.clock import parse_iso_datetime
from app.core.http import QueryParams, ReadOnlyViolationError, VenueHttpError

Handler = Callable[[str, str, Mapping[str, Any] | None, Any], Any]


class FixtureNotFoundError(VenueHttpError):
    """The fixture set has no payload for the requested path (maps to HTTP 404)."""


class FixtureTransport:
    """In-process :class:`app.core.http.Transport` backed by fixture payloads."""

    def __init__(
        self,
        name: str,
        handler: Handler,
        *,
        readonly_post_paths: frozenset[str] = frozenset(),
        now: Callable[[], datetime],
    ) -> None:
        self._name = name
        self._handler = handler
        self._readonly_post_paths = readonly_post_paths
        self._now = now
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        self.requests: list[tuple[str, str]] = []

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
        return self._call("GET", path, dict(params or {}), None)

    async def post_json_readonly(self, path: str, body: Any) -> Any:
        if path not in self._readonly_post_paths:
            raise ReadOnlyViolationError(f"{self._name}: POST {path} is not allow-listed")
        return self._call("POST", path, None, body)

    async def aclose(self) -> None:
        return None

    def _call(self, method: str, path: str, params: Mapping[str, Any] | None, body: Any) -> Any:
        self.requests.append((method, path))
        try:
            result = self._handler(method, path, params, body)
        except FixtureNotFoundError as exc:
            self._last_error = str(exc)
            raise
        self._last_success_at = self._now()
        return json.loads(json.dumps(result))  # defensive deep copy


@dataclass
class FixtureSet:
    """All fixture payloads plus the manifest describing expected demo outcomes."""

    root: Path
    manifest: dict[str, Any]
    kalshi_events: list[dict[str, Any]]
    kalshi_series: dict[str, dict[str, Any]]
    kalshi_orderbooks: dict[str, dict[str, Any]]
    kalshi_exchange_status: dict[str, Any]
    polymarket_markets: list[dict[str, Any]]
    polymarket_books: dict[str, dict[str, Any]]
    kalshi_markets: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def as_of(self) -> datetime:
        return parse_iso_datetime(str(self.manifest["as_of"]))

    @classmethod
    def load(cls, root: Path) -> FixtureSet:
        def read(relative: str) -> Any:
            return json.loads((root / relative).read_text(encoding="utf-8"))

        events_page = read("kalshi/events.json")
        books = read("polymarket/books.json")
        fixture = cls(
            root=root,
            manifest=read("manifest.json"),
            kalshi_events=list(events_page["events"]),
            kalshi_series={s["ticker"]: s for s in read("kalshi/series.json")["series"]},
            kalshi_orderbooks=dict(read("kalshi/orderbooks.json")),
            kalshi_exchange_status=read("kalshi/exchange_status.json"),
            polymarket_markets=list(read("polymarket/markets.json")["markets"]),
            polymarket_books={str(b["asset_id"]): b for b in books},
        )
        for event in fixture.kalshi_events:
            for market in event.get("markets") or []:
                fixture.kalshi_markets[str(market["ticker"])] = market
        return fixture

    # ---- handlers ----------------------------------------------------------------------

    def kalshi_handler(self, method: str, path: str, params: Mapping[str, Any] | None, body: Any) -> Any:
        del params, body
        if method != "GET":
            raise FixtureNotFoundError("kalshi-fixture", f"unsupported {method} {path}", status=405)
        parts = [part for part in path.split("/") if part]
        if parts == ["events"]:
            return {"events": self.kalshi_events, "cursor": ""}
        if parts == ["exchange", "status"]:
            return self.kalshi_exchange_status
        if len(parts) == 2 and parts[0] == "events":
            for event in self.kalshi_events:
                if event.get("event_ticker") == parts[1]:
                    return {"event": {k: v for k, v in event.items() if k != "markets"}}
        if len(parts) == 2 and parts[0] == "series" and parts[1] in self.kalshi_series:
            return {"series": self.kalshi_series[parts[1]]}
        if len(parts) == 2 and parts[0] == "markets" and parts[1] in self.kalshi_markets:
            return {"market": self.kalshi_markets[parts[1]]}
        if (
            len(parts) == 3
            and parts[0] == "markets"
            and parts[2] == "orderbook"
            and parts[1] in self.kalshi_orderbooks
        ):
            return self.kalshi_orderbooks[parts[1]]
        raise FixtureNotFoundError("kalshi-fixture", f"no fixture for GET {path}", status=404)

    def gamma_handler(self, method: str, path: str, params: Mapping[str, Any] | None, body: Any) -> Any:
        del body
        if method != "GET":
            raise FixtureNotFoundError("gamma-fixture", f"unsupported {method} {path}", status=405)
        if path == "/markets/keyset":
            limit = int((params or {}).get("limit") or len(self.polymarket_markets))
            return {"markets": self.polymarket_markets[:limit]}
        if path.startswith("/markets/"):
            market_id = path.removeprefix("/markets/")
            for market in self.polymarket_markets:
                if str(market.get("id")) == market_id:
                    return market
        raise FixtureNotFoundError("gamma-fixture", f"no fixture for GET {path}", status=404)

    def clob_handler(self, method: str, path: str, params: Mapping[str, Any] | None, body: Any) -> Any:
        if method == "POST" and path == "/books":
            tokens = [str(item.get("token_id")) for item in body or [] if isinstance(item, dict)]
            return [self.polymarket_books[t] for t in tokens if t in self.polymarket_books]
        if method == "GET" and path == "/book":
            token = str((params or {}).get("token_id") or "")
            if token in self.polymarket_books:
                return self.polymarket_books[token]
        raise FixtureNotFoundError("clob-fixture", f"no fixture for {method} {path}", status=404)
