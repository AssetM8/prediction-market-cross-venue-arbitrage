from __future__ import annotations

import io
import json
import logging
from decimal import Decimal

import pytest

from app.core.logging import (
    REDACTED,
    ContextFilter,
    JsonFormatter,
    RedactingFilter,
    log_context,
    log_event,
    redact_text,
    redact_value,
)
from app.core.metrics import MetricsRegistry
from app.domain.enums import OrderAction, OutcomeSide, PaperOrderStatus, Venue
from app.domain.paper import PaperFill, PaperOrder
from app.execution.ledger import PaperLedger
from tests.conftest import NOW

D = Decimal
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxyz\n-----END RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    "secret_text",
    [
        PEM,
        "Authorization: Bearer abcdefghijklmnop.qrstu",
        "api_key=sk-live-1234567890abcdef",
        "OPTIONAL_LLM_API_KEY: 'hunter2hunter2'",
        "key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
        "0x4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318",
        "legal winner thank year wave sausage worth useful legal winner thank yellow",
    ],
)
def test_redact_text_removes_secrets(secret_text: str) -> None:
    redacted = redact_text(secret_text)
    assert REDACTED in redacted
    for fragment in (
        "MIIEowIBAAKCAQEAxyz",
        "abcdefghijklmnop",
        "1234567890abcdef",
        "hunter2hunter2",
        "ABCDEFGHIJKLMNOPQRSTUV",
        "4c0883a69102937d6231471b5dbb6204",
        "sausage",
    ):
        assert fragment not in redacted


def test_ordinary_text_is_kept() -> None:
    text = (
        "will the federal reserve cut rates at the december meeting after the labor market cools down again"
    )
    assert redact_text(text) == text
    assert redact_value("token_id", "12345") == "12345"  # market token ids are public
    assert redact_value("api_key", "abc") == REDACTED
    assert redact_value("headers", {"KALSHI-ACCESS-SIGNATURE": "sig", "Accept": "json"}) == {
        "KALSHI-ACCESS-SIGNATURE": REDACTED,
        "Accept": "json",
    }


def test_log_records_are_json_with_context_and_redacted() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.redaction")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    with log_context(
        correlation_id="abc123",
        venue="kalshi",
        pair_id="pair_1",
        opportunity_id="opp_1",
        paper_order_id="po_1",
        market_id="M",
    ):
        log_event(logger, logging.INFO, "calling with api_key=supersecretvalue", passphrase="p@ss", depth=3)
    record = json.loads(stream.getvalue())
    assert record["correlation_id"] == "abc123" and record["venue"] == "kalshi"
    assert record["pair_id"] == "pair_1" and record["paper_order_id"] == "po_1"
    assert "supersecretvalue" not in stream.getvalue()
    assert record["fields"]["passphrase"] == REDACTED
    assert record["fields"]["depth"] == 3


def test_log_context_rejects_unknown_fields() -> None:
    with pytest.raises(KeyError), log_context(password="x"):
        pass


def test_metrics_registry() -> None:
    metrics = MetricsRegistry()
    metrics.inc("http_requests_total", venue="kalshi")
    metrics.inc("http_requests_total", 2, venue="kalshi")
    metrics.set_gauge("markets_listed", 5, venue="polymarket")
    metrics.mark("venue_last_success", NOW, venue="kalshi")
    assert metrics.counter("http_requests_total", venue="kalshi") == 3
    assert metrics.timestamp("venue_last_success", venue="kalshi") == NOW
    text = metrics.prometheus_text()
    assert 'pm_arb_http_requests_total{venue="kalshi"} 3' in text
    assert metrics.snapshot()["gauges"]["markets_listed"][0]["value"] == 5


def _order(action: OrderAction, quantity: str, price: str, fee: str = "0") -> PaperOrder:
    fill = PaperFill(id="f", order_id="o", price=D(price), quantity=D(quantity), fee=D(fee), filled_at=NOW)
    return PaperOrder(
        id="o",
        execution_id="e",
        opportunity_id="op",
        leg_index=0,
        purpose="entry",
        venue=Venue.KALSHI,
        market_id="M",
        outcome_id="M:yes",
        outcome_side=OutcomeSide.YES,
        action=action,
        requested_quantity=D(quantity),
        limit_price=D(price),
        filled_quantity=D(quantity),
        average_price=D(price),
        fees=D(fee),
        status=PaperOrderStatus.FILLED,
        created_at=NOW,
        fills=(fill,),
    )


def test_ledger_realized_and_unrealized_pnl() -> None:
    ledger = PaperLedger.fresh({Venue.KALSHI: D(100)})
    ledger.apply_order(_order(OrderAction.BUY, "10", "0.40", "0.1"), NOW)
    realized = ledger.apply_order(_order(OrderAction.SELL, "4", "0.50", "0.02"), NOW)
    assert realized == D("0.38")  # 4*0.50 - 4*0.40 - 0.02
    portfolio = ledger.portfolio(NOW, {"kalshi:M:M:yes": D("0.45")})
    position = portfolio.positions[0]
    assert position.quantity == D(6)
    assert position.unrealized_pnl == D("0.30")  # 6*0.45 - 2.40
    assert portfolio.cash[0].cash == D(100) - D("4.1") + D("1.98")
    with pytest.raises(ValueError, match="short selling"):
        ledger.apply_order(_order(OrderAction.SELL, "7", "0.50"), NOW)
