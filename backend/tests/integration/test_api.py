"""HTTP API tests via ASGI transport (no network, fixture data)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from app.services.container import AppContainer
from tests.conftest import make_settings

SECRET = "sk-ant-api03-TOPSECRETVALUE0123456789"


@pytest.fixture
async def api(tmp_path: Any) -> AsyncIterator[tuple[httpx.AsyncClient, AppContainer]]:
    settings: Settings = make_settings(
        tmp_path, auto_scan_on_startup=True, optional_llm_api_key=SECRET, optional_llm_model="model-x"
    )
    container = AppContainer(settings)
    await container.db.reset()
    app: FastAPI = create_app(settings, container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, container
    await container.aclose()


async def test_health_and_config(api: tuple[httpx.AsyncClient, AppContainer]) -> None:
    client, _ = api
    response = await client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["paper_trading_only"] is True and body["data_mode"] == "fixture" and body["simulated_clock"]
    assert body["last_scan"]["kind"] == "startup"
    assert response.headers["X-Paper-Trading-Only"] == "true"
    assert response.headers["X-Correlation-ID"]
    config = (await client.get("/api/config/public")).json()
    assert config["paper_trading_only"] is True
    assert config["optional_llm_configured"] is True
    assert SECRET not in (await client.get("/api/config/public")).text


async def test_markets_pairs_and_filters(api: tuple[httpx.AsyncClient, AppContainer]) -> None:
    client, _ = api
    markets = (await client.get("/api/markets", params={"eligible": "false"})).json()
    assert len(markets) == 5 and all(m["eligibility_reasons"] for m in markets)
    approved = (await client.get("/api/market-pairs", params={"approved": "true"})).json()
    assert {p["relation"] for p in approved} == {"EQUIVALENT", "COMPLEMENTARY"}
    implications = (
        await client.get(
            "/api/market-pairs", params=[("relation", "A_IMPLIES_B"), ("relation", "B_IMPLIES_A")]
        )
    ).json()
    assert len(implications) == 3 and not any(p["approved_for_arbitrage_calculation"] for p in implications)
    confident = (await client.get("/api/market-pairs", params={"min_confidence": "0.95"})).json()
    assert all(float(p["confidence"]) >= 0.95 for p in confident)
    active = (await client.get("/api/market-pairs", params={"venue_status": "active"})).json()
    assert len(active) >= len(approved)
    pair = (await client.get(f"/api/market-pairs/{approved[0]['id']}")).json()
    assert pair["deterministic_checks"] and pair["semantic_explanation"]["relation"] == pair["relation"]
    assert (await client.get("/api/market-pairs/nope")).status_code == 404
    graph = (await client.get("/api/relationships")).json()
    assert graph["nodes"] and graph["edges"]
    books = (await client.get("/api/books/kalshi/KXFEDDECISION-26DEC-C25")).json()
    assert [b["outcome_side"] for b in books] == ["yes", "no"] and books[0]["derived_asks"]
    assert (await client.get("/api/market-pairs", params={"venue_status": "bogus"})).status_code == 422


async def test_opportunities_and_paper_execution(api: tuple[httpx.AsyncClient, AppContainer]) -> None:
    client, _ = api
    validated = (await client.get("/api/opportunities", params={"status": "validated"})).json()
    assert len(validated) == 2
    assert all(o["data_source"] == "fixture" for o in validated)
    profitable = (await client.get("/api/opportunities", params={"min_profit": "5"})).json()
    assert all(float(o["expected_net_profit"]) >= 5 for o in profitable)
    stale = (await client.get("/api/opportunities", params={"freshness": "stale"})).json()
    assert stale and all(o["status"] == "suppressed" for o in stale if o["pair_id"])
    detail = (await client.get(f"/api/opportunities/{validated[0]['id']}")).json()
    assert detail["executable"] and detail["fresh"] and len(detail["books"]) == 2 and detail["pair"]
    opp_id = validated[0]["id"]

    missing_key = await client.post(f"/api/paper/execute/{opp_id}", json={})
    assert missing_key.status_code == 400
    bad_key = await client.post(f"/api/paper/execute/{opp_id}", json={}, headers={"Idempotency-Key": "x"})
    assert bad_key.status_code == 400
    for body in (
        {"quantity": "-5"},
        {"quantity": "abc"},
        {"quantity": "1e9"},
        {"scenario": "yolo"},
        {"quantity": "1", "live": True},
    ):
        invalid = await client.post(
            f"/api/paper/execute/{opp_id}", json=body, headers={"Idempotency-Key": "valid-key-1"}
        )
        assert invalid.status_code == 422, body

    headers = {"Idempotency-Key": "api-test-000001"}
    first = await client.post(f"/api/paper/execute/{opp_id}", json={"quantity": "10"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["paper_trading_only"] is True and first.json()["execution"]["outcome"] == "hedged"
    again = await client.post(f"/api/paper/execute/{opp_id}", json={"quantity": "10"}, headers=headers)
    assert again.headers["Idempotent-Replayed"] == "true" and again.json()["replayed"] is True
    assert again.json()["execution"]["id"] == first.json()["execution"]["id"]
    conflict = await client.post(f"/api/paper/execute/{validated[1]['id']}", json={}, headers=headers)
    assert conflict.status_code == 409
    unknown = await client.post(
        "/api/paper/execute/opp_unknown", json={}, headers={"Idempotency-Key": "unknown-0001"}
    )
    assert unknown.status_code == 404

    assert len((await client.get("/api/paper/executions")).json()) == 1
    orders = (await client.get("/api/paper/orders")).json()
    assert len(orders) == 2 and all(o["simulated"] for o in orders)
    assert (await client.get("/api/paper/positions")).json()
    portfolio = (await client.get("/api/paper/portfolio")).json()
    assert float(portfolio["locked_in_pnl"]) > 0
    audit = (await client.get("/api/audit", params={"category": "paper_execution"})).json()
    assert len(audit) == 1 and audit[0]["execution_id"] == first.json()["execution"]["id"]
    reset = await client.post("/api/paper/kill-switch/reset")
    assert reset.status_code == 200 and reset.json()["kill_switch_engaged"] is False


async def test_refresh_and_metrics(api: tuple[httpx.AsyncClient, AppContainer]) -> None:
    client, _ = api
    headers = {"Idempotency-Key": "refresh-00000001"}
    first = await client.post("/api/market-pairs/refresh", headers=headers)
    assert first.status_code == 200 and first.json()["summary"]["approved_pairs"] == 4
    again = await client.post("/api/market-pairs/refresh", headers=headers)
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.json()["summary"]["run_id"] == first.json()["summary"]["run_id"]
    assert (
        await client.post("/api/market-pairs/refresh", headers={"Idempotency-Key": "bad"})
    ).status_code == 422
    metrics = (await client.get("/metrics")).json()
    assert "opportunities_total" in metrics["counters"]
    prom = await client.get("/metrics", params={"format": "prometheus"})
    assert "pm_arb_opportunities_total" in prom.text
    venues = (await client.get("/api/venues", params={"check": "true"})).json()
    assert {v["venue"] for v in venues["venues"]} == {"kalshi", "polymarket"}
    docs = (await client.get("/openapi.json")).json()
    assert "/api/paper/execute/{opportunity_id}" in docs["paths"]
