"""Security tests: no secrets leak, logs are redacted, live order placement is impossible."""

from __future__ import annotations

import ast
import asyncio
import io
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.http import ReadOnlyHttpTransport
from app.core.logging import ContextFilter, JsonFormatter, RedactingFilter, log_event
from app.main import create_app
from app.services.container import AppContainer
from app.venues.live_execution_disabled import DisabledLiveExecutionVenue
from tests.conftest import make_settings

APP_DIR = Path(__file__).resolve().parents[2] / "app"
SECRET = "sk-ant-api03-SUPERSECRET-7f3c9a1b2d4e6f80"


@pytest.fixture
async def client(tmp_path: Any) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(tmp_path, auto_scan_on_startup=True, optional_llm_api_key=SECRET)
    container = AppContainer(settings)
    await container.db.reset()
    app: FastAPI = create_app(settings, container)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http,
    ):
        yield http
    await container.aclose()


async def test_no_endpoint_returns_secrets(client: httpx.AsyncClient) -> None:
    openapi = (await client.get("/openapi.json")).json()
    for path, operations in openapi["paths"].items():
        if "get" not in operations or "{" in path:
            continue
        response = await client.get(path)
        assert SECRET not in response.text, path
        assert "SUPERSECRET" not in response.text, path


def test_logs_redact_suspicious_secrets() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.security.redaction")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    log_event(
        logger,
        logging.WARNING,
        f"llm failed with key {SECRET}",
        api_key=SECRET,
        headers={"x-api-key": SECRET, "POLY_PASSPHRASE": "pp"},
        note=f"Bearer {SECRET}",
    )
    output = stream.getvalue()
    assert "SUPERSECRET" not in output and '"pp"' not in output


def test_live_execution_adapter_always_refuses() -> None:
    venue = DisabledLiveExecutionVenue()
    with pytest.raises(NotImplementedError, match="not implemented"):
        asyncio.run(venue.submit_order())
    with pytest.raises(NotImplementedError):
        asyncio.run(venue.cancel_order())


def _python_files() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_disabled_live_adapter_is_unreachable_from_application_code() -> None:
    offenders = []
    for path in _python_files():
        if path.name == "live_execution_disabled.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "live_execution_disabled" in node.module:
                offenders.append(str(path))
            if isinstance(node, ast.Name) and node.id == "DisabledLiveExecutionVenue":
                offenders.append(str(path))
    assert offenders == []


FORBIDDEN_FRAGMENTS = (
    "/portfolio/orders",  # Kalshi order placement
    "post_order",
    "create_order",
    "place_order",
    "sign_order",
    "create_or_derive_api",
    "py_clob_client",
    "eth_account",
    "web3",
    "private_key=",
    "/orders/batched",
)


def test_no_order_placement_code_exists() -> None:
    for path in _python_files():
        text = path.read_text(encoding="utf-8").lower()
        for fragment in FORBIDDEN_FRAGMENTS:
            assert fragment not in text, f"{fragment!r} found in {path}"


def test_venue_transports_are_read_only() -> None:
    allowed = {"get_json", "post_json_readonly", "aclose", "name", "last_success_at", "last_error"}
    public = {name for name in vars(ReadOnlyHttpTransport) if not name.startswith("_")}
    assert public == allowed
    container_source = (APP_DIR / "services" / "container.py").read_text(encoding="utf-8")
    assert 'POLYMARKET_READONLY_POSTS = frozenset({"/books"})' in container_source


def test_only_paper_routes_mutate(tmp_path: Path) -> None:
    schema = create_app(make_settings(tmp_path)).openapi()
    methods = {(path, method.upper()) for path, ops in schema["paths"].items() for method in ops}
    mutating = {path for path, method in methods if method in {"POST", "PUT", "PATCH", "DELETE"}}
    assert mutating == {
        "/api/market-pairs/refresh",
        "/api/paper/execute/{opportunity_id}",
        "/api/paper/kill-switch/reset",
    }
    assert not {method for _, method in methods} & {"PUT", "PATCH", "DELETE"}


def test_settings_contain_no_trading_credentials() -> None:
    names = set(Settings.model_fields)
    for forbidden in (
        "kalshi_api_key",
        "kalshi_private_key",
        "polymarket_private_key",
        "polymarket_api_secret",
        "wallet",
        "seed_phrase",
        "mnemonic",
        "live_trading",
    ):
        assert not any(forbidden in name for name in names), forbidden
