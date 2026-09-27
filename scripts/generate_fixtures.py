"""Generate the deterministic, synthetic fixture set in ``fixtures/``.

The payloads follow the venues' documented wire formats (Kalshi ``GET /events`` with nested
markets, ``GET /series/{ticker}``, ``GET /markets/{ticker}/orderbook`` with
``orderbook_fp``; Polymarket Gamma ``GET /markets/keyset`` and CLOB book summaries) so the
real normalizers run in fixture mode.

All names, prices and dates are invented. Run from the repository root:

    python scripts/generate_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "fixtures"
AS_OF = datetime(2026, 9, 25, 14, 0, 0, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms(moment: datetime) -> str:
    return str(int(moment.timestamp() * 1000))


def token(label: str) -> str:
    """Deterministic 77-digit decimal token id (Polymarket token ids are large integers)."""
    digest = hashlib.sha256(f"token:{label}".encode()).hexdigest()
    return str(int(digest, 16))[:77].ljust(77, "7")


def condition(label: str) -> str:
    return "0x" + hashlib.sha256(f"condition:{label}".encode()).hexdigest()


# --------------------------------------------------------------------------------------
# Kalshi
# --------------------------------------------------------------------------------------

SERIES = [
    {
        "ticker": "KXFEDDECISION",
        "title": "Fed rate decision",
        "category": "Economics",
        "settlement_sources": [{"name": "Federal Reserve", "url": "https://www.federalreserve.gov"}],
    },
    {
        "ticker": "KXUNRATE",
        "title": "Unemployment rate",
        "category": "Economics",
        "settlement_sources": [{"name": "Bureau of Labor Statistics", "url": "https://www.bls.gov"}],
    },
    {
        "ticker": "KXCPIYOY",
        "title": "CPI year-over-year",
        "category": "Economics",
        "settlement_sources": [{"name": "Bureau of Labor Statistics", "url": "https://www.bls.gov"}],
    },
    {
        "ticker": "KXGDP",
        "title": "GDP growth",
        "category": "Economics",
        "settlement_sources": [{"name": "Bureau of Economic Analysis", "url": "https://www.bea.gov"}],
    },
    {
        "ticker": "KXINXY",
        "title": "S&P 500 year-end",
        "category": "Financials",
        "settlement_sources": [{"name": "S&P Dow Jones Indices", "url": "https://www.spglobal.com/spdji/"}],
    },
    {
        "ticker": "KXBTCD",
        "title": "Bitcoin daily price",
        "category": "Crypto",
        "settlement_sources": [{"name": "CF Benchmarks", "url": "https://www.cfbenchmarks.com"}],
    },
    {
        "ticker": "KXSENATEOH",
        "title": "Ohio Senate race",
        "category": "Politics",
        "settlement_sources": [{"name": "Associated Press", "url": "https://apnews.com"}],
    },
    {
        "ticker": "KXPRESPARTY",
        "title": "Presidential election party",
        "category": "Politics",
        "settlement_sources": [{"name": "Associated Press", "url": "https://apnews.com"}],
    },
    {
        "ticker": "KXECBRATE",
        "title": "ECB deposit rate",
        "category": "Economics",
        "settlement_sources": [{"name": "European Central Bank", "url": "https://www.ecb.europa.eu"}],
    },
    {
        "ticker": "KXPAYROLLS",
        "title": "Nonfarm payrolls",
        "category": "Economics",
        "settlement_sources": [{"name": "Bureau of Labor Statistics", "url": "https://www.bls.gov"}],
    },
    {
        "ticker": "KXHIGHNY",
        "title": "NYC high temperature",
        "category": "Climate and Weather",
        "settlement_sources": [{"name": "National Weather Service", "url": "https://www.weather.gov"}],
    },
    # KXARTEMIS is intentionally absent: its markets exercise the fee-metadata fallback.
]
for series in SERIES:
    series.update(
        {
            "frequency": "custom",
            "categories": [series["category"]],
            "tags": [],
            "contract_url": f"https://kalshi.com/contracts/{series['ticker'].lower()}",
            "contract_terms_url": f"https://kalshi.com/regulatory/{series['ticker'].lower()}.pdf",
            "product_metadata": None,
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "additional_prohibitions": None,
            "volume_fp": "100000.00",
            "last_updated_ts": iso(AS_OF - timedelta(days=3)),
            "exchange_index": 0,
        }
    )


def kmarket(
    ticker: str,
    event_ticker: str,
    yes_sub: str,
    rules_primary: str,
    rules_secondary: str,
    *,
    close: datetime,
    expected: datetime,
    status: str = "active",
    market_type: str = "binary",
    step: str = "0.01",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": market_type,
        "yes_sub_title": yes_sub,
        "no_sub_title": yes_sub,
        "created_time": iso(AS_OF - timedelta(days=30)),
        "updated_time": iso(AS_OF - timedelta(hours=1)),
        "open_time": iso(AS_OF - timedelta(days=30)),
        "close_time": iso(close),
        "expected_expiration_time": iso(expected),
        "latest_expiration_time": iso(expected + timedelta(days=7)),
        "settlement_timer_seconds": 3600,
        "status": status,
        "notional_value_dollars": "1.0000",
        "yes_bid_dollars": "0.0000",
        "yes_ask_dollars": "0.0000",
        "no_bid_dollars": "0.0000",
        "no_ask_dollars": "0.0000",
        "yes_bid_size_fp": "0.00",
        "yes_ask_size_fp": "0.00",
        "last_price_dollars": "0.0000",
        "previous_yes_bid_dollars": "0.0000",
        "previous_yes_ask_dollars": "0.0000",
        "previous_price_dollars": "0.0000",
        "volume_fp": "25000.00",
        "volume_24h_fp": "1200.00",
        "open_interest_fp": "18000.00",
        "result": "",
        "can_close_early": True,
        "expiration_value": "",
        "rules_primary": rules_primary,
        "rules_secondary": rules_secondary,
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.01", "end": "0.99", "step": step}],
    }


FOMC_CLOSE = datetime(2026, 12, 16, 18, 55, tzinfo=UTC)
FOMC_EXPECTED = datetime(2026, 12, 16, 19, 30, tzinfo=UTC)
FED_SECONDARY = (
    "The outcome is determined by the FOMC statement published on federalreserve.gov. "
    "If the meeting is postponed, the market resolves based on the rescheduled meeting."
)

KALSHI_EVENTS: list[dict[str, Any]] = [
    {
        "event_ticker": "KXFEDDECISION-26DEC",
        "series_ticker": "KXFEDDECISION",
        "title": "Fed decision in December 2026?",
        "sub_title": "FOMC meeting concluding December 16, 2026",
        "mutually_exclusive": True,
        "markets": [
            kmarket(
                "KXFEDDECISION-26DEC-C26",
                "KXFEDDECISION-26DEC",
                "Cut >25bps",
                "If the Federal Reserve lowers the upper bound of the federal funds target range by "
                "more than 25 basis points at the FOMC meeting concluding on December 16, 2026, then "
                "the market resolves to Yes.",
                FED_SECONDARY,
                close=FOMC_CLOSE,
                expected=FOMC_EXPECTED,
            ),
            kmarket(
                "KXFEDDECISION-26DEC-C25",
                "KXFEDDECISION-26DEC",
                "Cut 25bps",
                "If the Federal Reserve lowers the upper bound of the federal funds target range by "
                "exactly 25 basis points at the FOMC meeting concluding on December 16, 2026, then the "
                "market resolves to Yes.",
                FED_SECONDARY,
                close=FOMC_CLOSE,
                expected=FOMC_EXPECTED,
            ),
            kmarket(
                "KXFEDDECISION-26DEC-H0",
                "KXFEDDECISION-26DEC",
                "Hold",
                "If the Federal Reserve maintains the federal funds target range unchanged at the FOMC "
                "meeting concluding on December 16, 2026, then the market resolves to Yes.",
                FED_SECONDARY,
                close=FOMC_CLOSE,
                expected=FOMC_EXPECTED,
            ),
            kmarket(
                "KXFEDDECISION-26DEC-HK",
                "KXFEDDECISION-26DEC",
                "Hike",
                "If the Federal Reserve raises the upper bound of the federal funds target range at the "
                "FOMC meeting concluding on December 16, 2026, then the market resolves to Yes.",
                FED_SECONDARY,
                close=FOMC_CLOSE,
                expected=FOMC_EXPECTED,
            ),
        ],
    },
    {
        "event_ticker": "KXUNRATE-26OCT",
        "series_ticker": "KXUNRATE",
        "title": "Unemployment rate in October 2026?",
        "sub_title": "Employment Situation report",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXUNRATE-26OCT-T4.5",
                "KXUNRATE-26OCT",
                "Above 4.5%",
                "If the U.S. unemployment rate (U-3, seasonally adjusted) for October 2026, as first "
                "reported by the Bureau of Labor Statistics in the Employment Situation report, is above "
                "4.5%, then the market resolves to Yes.",
                "The rate is reported to one decimal place. Subsequent revisions will not be considered. "
                "If the report is delayed, the market will remain open until the report is released.",
                close=datetime(2026, 11, 6, 13, 25, tzinfo=UTC),
                expected=datetime(2026, 11, 6, 13, 30, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXCPIYOY-26NOV",
        "series_ticker": "KXCPIYOY",
        "title": "CPI inflation in November 2026?",
        "sub_title": "12-month change, CPI-U",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXCPIYOY-26NOV-T3.0",
                "KXCPIYOY-26NOV",
                "Above 3.0%",
                "If the 12-month change in the U.S. Consumer Price Index for All Urban Consumers (CPI-U), "
                "not seasonally adjusted, for November 2026, as first reported by the Bureau of Labor "
                "Statistics, is above 3.0%, then the market resolves to Yes.",
                "The value is reported to one decimal place. Subsequent revisions will not be considered. "
                "If the release is delayed, the market will remain open until the data is released.",
                close=datetime(2026, 12, 10, 13, 25, tzinfo=UTC),
                expected=datetime(2026, 12, 10, 13, 30, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXGDP-26Q3",
        "series_ticker": "KXGDP",
        "title": "Q3 2026 GDP growth?",
        "sub_title": "Advance estimate",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXGDP-26Q3-T3.0",
                "KXGDP-26Q3",
                "At least 3.0%",
                "If the seasonally adjusted annualized rate of U.S. real GDP growth for Q3 2026, as first "
                "reported by the Bureau of Economic Analysis in the advance estimate, is at least 3.0%, "
                "then the market resolves to Yes.",
                "The rate is reported to one decimal place. Subsequent revisions will not be considered.",
                close=datetime(2026, 10, 29, 12, 25, tzinfo=UTC),
                expected=datetime(2026, 10, 29, 12, 30, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXINXY-26DEC31",
        "series_ticker": "KXINXY",
        "title": "S&P 500 on December 31, 2026?",
        "sub_title": "Official closing level",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXINXY-26DEC31-T7000",
                "KXINXY-26DEC31",
                "Above 7,000",
                "If the official closing value of the S&P 500 index on December 31, 2026, as published "
                "by S&P Dow Jones Indices, is above 7,000, then the market resolves to Yes.",
                "If markets are closed on that date, the most recent official close is used.",
                close=datetime(2026, 12, 31, 20, 55, tzinfo=UTC),
                expected=datetime(2026, 12, 31, 22, 0, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXBTCD-26DEC3117",
        "series_ticker": "KXBTCD",
        "title": "Bitcoin price on Dec 31, 2026 at 5pm ET?",
        "sub_title": "CF Benchmarks BRTI",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXBTCD-26DEC3117-T150000",
                "KXBTCD-26DEC3117",
                "Above $150,000",
                "If the CF Benchmarks Bitcoin Real Time Index (BRTI) value at 5:00 PM ET on December 31, "
                "2026 is above $150,000, then the market resolves to Yes.",
                "The index value published by CF Benchmarks is final.",
                close=datetime(2026, 12, 31, 21, 59, tzinfo=UTC),
                expected=datetime(2026, 12, 31, 22, 5, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXARTEMIS3-27JUL",
        "series_ticker": "KXARTEMIS",
        "title": "Will a crewed Artemis III Moon landing occur before July 1, 2027?",
        "sub_title": "NASA Artemis III",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXARTEMIS3-27JUL",
                "KXARTEMIS3-27JUL",
                "Landing before July 1, 2027",
                "If NASA's Artemis III mission lands astronauts on the surface of the Moon before "
                "July 1, 2027 (Eastern Time), as confirmed by NASA, then the market resolves to Yes.",
                "Landing is defined by NASA's official mission updates.",
                close=datetime(2027, 6, 30, 23, 59, tzinfo=UTC),
                expected=datetime(2027, 7, 1, 16, 0, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXSENATEOH-26",
        "series_ticker": "KXSENATEOH",
        "title": "2026 Ohio Senate election winner?",
        "sub_title": "General election, November 3, 2026",
        "mutually_exclusive": True,
        "markets": [
            kmarket(
                "KXSENATEOH-26-JAVE",
                "KXSENATEOH-26",
                "Jordan Avery",
                "If Jordan Avery wins the 2026 Ohio Senate general election, as called by the Associated "
                "Press, then the market resolves to Yes.",
                "If the race is contested, the market resolves based on the certified results.",
                close=datetime(2026, 11, 4, 4, 0, tzinfo=UTC),
                expected=datetime(2026, 11, 5, 15, 0, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXPRESPARTY-28",
        "series_ticker": "KXPRESPARTY",
        "title": "Which party will win the 2028 U.S. presidential election?",
        "sub_title": "Winner of the Electoral College",
        "mutually_exclusive": True,
        "markets": [
            kmarket(
                "KXPRESPARTY-28-DEM",
                "KXPRESPARTY-28",
                "Democratic Party",
                "If the Democratic Party's nominee wins the 2028 United States presidential election, "
                "as called by the Associated Press and confirmed by the Electoral College vote, then the "
                "market resolves to Yes.",
                "Faithless electors do not change the outcome unless they alter the winner.",
                close=datetime(2028, 11, 7, 4, 0, tzinfo=UTC),
                expected=datetime(2028, 11, 8, 15, 0, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXECBRATE-26OCT",
        "series_ticker": "KXECBRATE",
        "title": "ECB deposit rate decision in October 2026?",
        "sub_title": "Monetary policy meeting concluding October 29, 2026",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXECBRATE-26OCT-CUT",
                "KXECBRATE-26OCT",
                "Cut",
                "If the European Central Bank lowers its deposit facility rate at the monetary policy "
                "meeting concluding on October 29, 2026, then the market resolves to Yes.",
                "Source: the ECB monetary policy decision press release on ecb.europa.eu. If the meeting "
                "is postponed, the market resolves based on the rescheduled meeting.",
                close=datetime(2026, 10, 29, 12, 10, tzinfo=UTC),
                expected=datetime(2026, 10, 29, 12, 30, tzinfo=UTC),
            ),
        ],
    },
    {
        "event_ticker": "KXPAYROLLS-26OCT",
        "series_ticker": "KXPAYROLLS",
        "title": "Nonfarm payrolls in October 2026?",
        "sub_title": "Scalar market (out of MVP scope)",
        "mutually_exclusive": False,
        "markets": [
            kmarket(
                "KXPAYROLLS-26OCT",
                "KXPAYROLLS-26OCT",
                "Payrolls change",
                "The market settles to the change in total nonfarm payrolls for October 2026 as first "
                "reported by the Bureau of Labor Statistics.",
                "Scalar payout between the floor and cap strikes.",
                close=datetime(2026, 11, 6, 13, 25, tzinfo=UTC),
                expected=datetime(2026, 11, 6, 13, 30, tzinfo=UTC),
                market_type="scalar",
            ),
        ],
    },
    {
        "event_ticker": "KXHIGHNY-26SEP24",
        "series_ticker": "KXHIGHNY",
        "title": "Highest temperature in NYC on Sep 24, 2026?",
        "sub_title": "Central Park",
        "mutually_exclusive": True,
        "markets": [
            kmarket(
                "KXHIGHNY-26SEP24-T80",
                "KXHIGHNY-26SEP24",
                "80° or above",
                "If the highest temperature recorded in Central Park on September 24, 2026 is 80°F or "
                "above, then the market resolves to Yes.",
                "Source: National Weather Service climatological report.",
                close=datetime(2026, 9, 25, 4, 0, tzinfo=UTC),
                expected=datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
                status="closed",
            ),
        ],
    },
]

# YES bids and NO bids ([price, count], ascending, best last) per Kalshi documentation.
KALSHI_BOOKS: dict[str, tuple[list[list[str]], list[list[str]]]] = {
    # profitable direction A with the Polymarket Fed market
    "KXFEDDECISION-26DEC-C25": (
        [["0.3100", "500.00"], ["0.3400", "300.00"], ["0.3600", "200.00"]],
        [["0.5500", "400.00"], ["0.5800", "250.00"], ["0.6000", "150.00"]],
    ),
    "KXFEDDECISION-26DEC-C26": (
        [["0.0600", "400.00"], ["0.0800", "300.00"]],
        [["0.8800", "300.00"], ["0.9000", "250.00"]],
    ),
    "KXFEDDECISION-26DEC-H0": (
        [["0.4500", "300.00"], ["0.4700", "200.00"]],
        [["0.4600", "300.00"], ["0.4800", "250.00"]],
    ),
    "KXFEDDECISION-26DEC-HK": (
        [["0.0100", "900.00"], ["0.0200", "400.00"]],
        [["0.9600", "500.00"], ["0.9700", "300.00"]],
    ),
    # equivalent but not profitable
    "KXUNRATE-26OCT-T4.5": (
        [["0.2800", "150.00"], ["0.3100", "400.00"]],
        [["0.6000", "200.00"], ["0.6600", "300.00"]],
    ),
    # complementary and profitable with the Polymarket "at or below 3.0%" market
    "KXCPIYOY-26NOV-T3.0": (
        [["0.4000", "250.00"], ["0.4200", "200.00"]],
        [["0.5000", "100.00"], ["0.5300", "200.00"], ["0.5500", "300.00"]],
    ),
    "KXGDP-26Q3-T3.0": (
        [["0.4100", "200.00"], ["0.4300", "150.00"]],
        [["0.5200", "200.00"], ["0.5400", "150.00"]],
    ),
    # implication A => B where Polymarket's weaker proposition is underpriced
    "KXINXY-26DEC31-T7000": (
        [["0.4600", "300.00"], ["0.4800", "300.00"]],
        [["0.4600", "200.00"], ["0.4900", "200.00"]],
    ),
    "KXBTCD-26DEC3117-T150000": (
        [["0.2200", "300.00"], ["0.2400", "200.00"]],
        [["0.7200", "300.00"], ["0.7400", "200.00"]],
    ),
    "KXARTEMIS3-27JUL": (
        [["0.1800", "300.00"], ["0.2000", "300.00"]],
        [["0.7200", "300.00"], ["0.7500", "300.00"]],
    ),
    "KXSENATEOH-26-JAVE": (
        [["0.5000", "300.00"], ["0.5200", "200.00"]],
        [["0.4400", "300.00"], ["0.4600", "200.00"]],
    ),
    "KXPRESPARTY-28-DEM": (
        [["0.4800", "500.00"], ["0.5000", "400.00"]],
        [["0.4600", "500.00"], ["0.4800", "400.00"]],
    ),
    # would look profitable, but the Polymarket side is stale
    "KXECBRATE-26OCT-CUT": (
        [["0.2600", "300.00"], ["0.2800", "200.00"]],
        [["0.6600", "300.00"], ["0.7000", "250.00"]],
    ),
}

# --------------------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------------------

FEE_ECONOMICS = {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.2}
FEE_FINANCE = {"exponent": 1, "rate": 0.04, "takerOnly": True, "rebateRate": 0.2}
FEE_POLITICS = {"exponent": 1, "rate": 0.04, "takerOnly": True, "rebateRate": 0.2}
FEE_CRYPTO = {"exponent": 1, "rate": 0.07, "takerOnly": True, "rebateRate": 0.2}
FEE_OTHER = {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.2}


def pmarket(
    market_id: str,
    question: str,
    description: str,
    *,
    end: datetime,
    category: str,
    resolution_source: str,
    event_id: str,
    event_title: str,
    event_slug: str,
    fee_schedule: dict[str, Any] | None,
    outcomes: tuple[str, str] = ("Yes", "No"),
    active: bool = True,
    closed: bool = False,
    neg_risk: bool = False,
    neg_risk_id: str | None = None,
    event_market_ids: list[str] | None = None,
    tick: float = 0.01,
    min_size: float = 5,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": market_id,
        "question": question,
        "conditionId": condition(market_id),
        "slug": f"{event_slug}-{market_id}",
        "description": description,
        "resolutionSource": resolution_source,
        "endDate": iso(end),
        "endDateIso": end.date().isoformat(),
        "startDate": iso(AS_OF - timedelta(days=40)),
        "startDateIso": (AS_OF - timedelta(days=40)).date().isoformat(),
        "category": category,
        "outcomes": json.dumps(list(outcomes)),
        "outcomePrices": json.dumps(["0.5", "0.5"]),
        "clobTokenIds": json.dumps([token(f"{market_id}:0"), token(f"{market_id}:1")]),
        "active": active,
        "closed": closed,
        "archived": False,
        "acceptingOrders": active and not closed,
        "enableOrderBook": True,
        "orderPriceMinTickSize": tick,
        "orderMinSize": min_size,
        "negRisk": neg_risk,
        "restricted": False,
        "umaResolutionStatus": "",
        "volumeNum": 150000.0,
        "liquidityNum": 40000.0,
        "events": [
            {
                "id": event_id,
                "title": event_title,
                "slug": event_slug,
                **({"markets": [{"id": mid} for mid in event_market_ids]} if event_market_ids else {}),
            }
        ],
    }
    if neg_risk_id:
        raw["negRiskMarketID"] = neg_risk_id
    if fee_schedule is not None:
        raw["feesEnabled"] = True
        raw["feeSchedule"] = fee_schedule
    return raw


RIVERTON_IDS = ["620101", "620102", "620103", "620104"]
RIVERTON_NEG = "0x" + hashlib.sha256(b"negrisk:riverton").hexdigest()

POLY_MARKETS: list[dict[str, Any]] = [
    pmarket(
        "610001",
        "Fed decreases interest rates by 25 bps after December 2026 meeting?",
        'This market will resolve to "Yes" if the Federal Reserve decreases the upper bound of the target '
        "federal funds range by exactly 25 basis points at its December 15-16, 2026 meeting. Otherwise, "
        'this market will resolve to "No". The resolution source for this market is the FOMC statement '
        "released after the meeting on federalreserve.gov. If the meeting is postponed, the outcome of the "
        "rescheduled meeting will be used.",
        end=datetime(2026, 12, 16, 20, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        event_id="71001",
        event_title="Fed decision in December 2026",
        event_slug="fed-decision-in-december-2026",
        fee_schedule=FEE_ECONOMICS,
    ),
    pmarket(
        "610002",
        "October 2026 US unemployment rate greater than 4.5%?",
        'This market will resolve to "Yes" if the United States unemployment rate (SA) for October 2026, as '
        "initially published by the U.S. Bureau of Labor Statistics in its Employment Situation release, is "
        'higher than 4.5%. Otherwise it resolves to "No". The rate is published rounded to one decimal place. '
        "Resolution is based on the initial publication, regardless of any later revisions. If the release is "
        "postponed, this market will remain open until the release.",
        end=datetime(2026, 11, 6, 14, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.bls.gov/news.release/empsit.toc.htm",
        event_id="71002",
        event_title="US unemployment rate October 2026",
        event_slug="us-unemployment-oct-2026",
        fee_schedule=FEE_ECONOMICS,
    ),
    pmarket(
        "610003",
        "November 2026 CPI YoY at or below 3.0%?",
        'This market will resolve to "Yes" if the year-over-year CPI-U inflation rate for November 2026 (NSA) '
        "in the United States, as initially published by the U.S. Bureau of Labor Statistics, is at or below "
        "3.0%. The figure is published rounded to one decimal place. The market resolves on the initial "
        "release, regardless of any later revisions. If the release is postponed, this market will remain "
        "open until the release.",
        end=datetime(2026, 12, 10, 14, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.bls.gov/cpi/",
        event_id="71003",
        event_title="November 2026 CPI",
        event_slug="november-2026-cpi",
        fee_schedule=FEE_ECONOMICS,
    ),
    pmarket(
        "610004",
        "US Q3 2026 GDP growth greater than 3.0%?",
        'Resolves "Yes" if U.S. real GDP growth (seasonally adjusted annualized rate) for Q3 2026, as '
        "initially published by the Bureau of Economic Analysis in its advance estimate, is greater than "
        "3.0%. The rate is published rounded to one decimal place. Later revisions are ignored.",
        end=datetime(2026, 10, 29, 13, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.bea.gov/data/gdp/gross-domestic-product",
        event_id="71004",
        event_title="US GDP Q3 2026",
        event_slug="us-gdp-q3-2026",
        fee_schedule=FEE_ECONOMICS,
    ),
    pmarket(
        "610005",
        "Will the S&P 500 close above 6,800 on December 31, 2026?",
        'Resolves "Yes" if the official closing level of the S&P 500 on December 31, 2026, as published by '
        "S&P Dow Jones Indices, is greater than 6,800. If markets are closed on that date, the most recent "
        "official close is used.",
        end=datetime(2026, 12, 31, 22, 0, tzinfo=UTC),
        category="Finance",
        resolution_source="https://www.spglobal.com/spdji/",
        event_id="71005",
        event_title="S&P 500 year-end 2026",
        event_slug="sp500-year-end-2026",
        fee_schedule=FEE_FINANCE,
    ),
    pmarket(
        "610006",
        "Bitcoin above $150,000 on December 31, 2026 at 5:00 PM ET?",
        'Resolves "Yes" if the Binance BTC/USDT 1-minute candle close price at 5:00 PM ET on December 31, 2026 '
        'is higher than $150,000. Otherwise "No". Resolution source: Binance.',
        end=datetime(2026, 12, 31, 22, 5, tzinfo=UTC),
        category="Crypto",
        resolution_source="https://www.binance.com/en/trade/BTC_USDT",
        event_id="71006",
        event_title="Bitcoin price December 31, 2026",
        event_slug="btc-dec-31-2026",
        fee_schedule=FEE_CRYPTO,
    ),
    pmarket(
        "610007",
        "Artemis III crewed Moon landing by December 31, 2027?",
        'This market will resolve to "Yes" if NASA\'s Artemis III mission lands a crew on the lunar surface '
        "by December 31, 2027 (Eastern Time), as confirmed by NASA. Otherwise it resolves to No.",
        end=datetime(2027, 12, 31, 23, 59, tzinfo=UTC),
        category="Science",
        resolution_source="https://www.nasa.gov/artemis",
        event_id="71007",
        event_title="Artemis III landing",
        event_slug="artemis-iii-landing",
        fee_schedule=FEE_OTHER,
    ),
    pmarket(
        "610008",
        "Will Jordan Avery win the 2026 Ohio gubernatorial election?",
        'Resolves "Yes" if Jordan Avery wins the 2026 Ohio gubernatorial general election, as called by the '
        "Associated Press. If the race is contested, the market resolves based on the certified results.",
        end=datetime(2026, 11, 5, 15, 0, tzinfo=UTC),
        category="Politics",
        resolution_source="https://apnews.com",
        event_id="71008",
        event_title="Ohio Governor 2026",
        event_slug="ohio-governor-2026",
        fee_schedule=FEE_POLITICS,
    ),
    pmarket(
        "610009",
        "Will the Democratic candidate win the popular vote in the 2028 U.S. presidential election?",
        'Resolves "Yes" if the Democratic Party\'s candidate receives the most votes nationwide (popular vote) '
        "in the 2028 United States presidential election, as reported by the Associated Press.",
        end=datetime(2028, 11, 8, 15, 0, tzinfo=UTC),
        category="Politics",
        resolution_source="https://apnews.com",
        event_id="71009",
        event_title="2028 popular vote",
        event_slug="2028-popular-vote",
        fee_schedule=FEE_POLITICS,
    ),
    pmarket(
        "610010",
        "ECB cuts deposit facility rate at October 2026 meeting?",
        'Resolves "Yes" if the European Central Bank reduces its deposit facility rate at its monetary policy '
        "meeting on October 29, 2026. Resolution source: the official ECB press release on ecb.europa.eu. "
        "If the meeting is postponed, the outcome of the rescheduled meeting will be used.",
        end=datetime(2026, 10, 29, 13, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.ecb.europa.eu/press/pr/html/index.en.html",
        event_id="71010",
        event_title="ECB October 2026",
        event_slug="ecb-october-2026",
        fee_schedule=None,
    ),  # no fee metadata -> conservative fallback
    pmarket(
        "610011",
        "Bitcoin Up or Down on September 26?",
        "Resolves to Up if the BTC/USDT close is higher than the open.",
        end=datetime(2026, 9, 26, 16, 0, tzinfo=UTC),
        category="Crypto",
        resolution_source="https://www.binance.com",
        event_id="71011",
        event_title="BTC up or down",
        event_slug="btc-up-down-sep-26",
        fee_schedule=FEE_CRYPTO,
        outcomes=("Up", "Down"),
    ),
    pmarket(
        "610012",
        "Will the 2026 Riverton mayoral debate be held before October 15?",
        "",
        end=datetime(2026, 10, 15, 4, 0, tzinfo=UTC),
        category="Politics",
        resolution_source="",
        event_id="71012",
        event_title="Riverton debate",
        event_slug="riverton-debate",
        fee_schedule=FEE_POLITICS,
    ),
    pmarket(
        "610013",
        "Will the Bank of Canada cut rates in September 2026?",
        'Resolved "No" on September 10, 2026.',
        end=datetime(2026, 9, 10, 16, 0, tzinfo=UTC),
        category="Economics",
        resolution_source="https://www.bankofcanada.ca",
        event_id="71013",
        event_title="Bank of Canada September 2026",
        event_slug="boc-sep-2026",
        fee_schedule=FEE_ECONOMICS,
        active=True,
        closed=True,
    ),
    *[
        pmarket(
            mid,
            question,
            f'Resolves "Yes" if {name} wins the 2026 Riverton mayoral election, as certified by the Riverton '
            "City Clerk. This market is part of a negative-risk group: exactly one market in the group "
            "resolves Yes.",
            end=datetime(2026, 11, 4, 15, 0, tzinfo=UTC),
            category="Politics",
            resolution_source="Riverton City Clerk",
            event_id="71020",
            event_title="Riverton mayoral election 2026",
            event_slug="riverton-mayor-2026",
            fee_schedule=FEE_POLITICS,
            neg_risk=True,
            neg_risk_id=RIVERTON_NEG,
            event_market_ids=RIVERTON_IDS,
        )
        for mid, name, question in [
            ("620101", "Dana Whitfield", "Will Dana Whitfield win the 2026 Riverton mayoral election?"),
            ("620102", "Luis Ortega", "Will Luis Ortega win the 2026 Riverton mayoral election?"),
            ("620103", "Priya Raman", "Will Priya Raman win the 2026 Riverton mayoral election?"),
            ("620104", "another candidate", "Will another candidate win the 2026 Riverton mayoral election?"),
        ]
    ],
]

# (yes asks, yes bids, no asks, no bids) as [price, size]; stale flag moves the timestamp back.
POLY_BOOKS: dict[str, dict[str, Any]] = {
    "610001": {
        "yes": ([["0.51", "200"], ["0.53", "300"]], [["0.47", "150"], ["0.45", "300"]]),
        "no": ([["0.52", "120"], ["0.54", "200"], ["0.57", "400"]], [["0.48", "150"], ["0.46", "300"]]),
    },
    "610002": {
        "yes": ([["0.33", "500"], ["0.35", "400"]], [["0.31", "300"]]),
        "no": ([["0.68", "400"], ["0.70", "300"]], [["0.66", "200"]]),
    },
    "610003": {
        "yes": ([["0.48", "250"], ["0.50", "400"]], [["0.46", "200"]]),
        "no": ([["0.53", "300"], ["0.55", "300"]], [["0.51", "100"]]),
    },
    "610004": {"yes": ([["0.44", "300"]], [["0.42", "200"]]), "no": ([["0.57", "300"]], [["0.55", "200"]])},
    "610005": {
        "yes": ([["0.40", "180"], ["0.44", "300"]], [["0.38", "200"]]),
        "no": ([["0.61", "300"]], [["0.59", "200"]]),
    },
    "610006": {"yes": ([["0.24", "300"]], [["0.22", "300"]]), "no": ([["0.77", "300"]], [["0.75", "300"]])},
    "610007": {"yes": ([["0.45", "300"]], [["0.43", "300"]]), "no": ([["0.56", "300"]], [["0.54", "300"]])},
    "610008": {"yes": ([["0.30", "300"]], [["0.28", "300"]]), "no": ([["0.71", "300"]], [["0.69", "300"]])},
    "610009": {"yes": ([["0.55", "300"]], [["0.53", "300"]]), "no": ([["0.46", "300"]], [["0.44", "300"]])},
    "610010": {
        "yes": ([["0.36", "300"]], [["0.34", "300"]]),
        "no": ([["0.62", "300"], ["0.64", "300"]], [["0.60", "300"]]),
        "stale": True,
    },
    "610011": {"yes": ([["0.52", "300"]], [["0.50", "300"]]), "no": ([["0.49", "300"]], [["0.47", "300"]])},
    "620101": {
        "yes": ([["0.40", "300"], ["0.43", "300"]], [["0.38", "300"]]),
        "no": ([["0.62", "300"]], [["0.60", "300"]]),
    },
    "620102": {
        "yes": ([["0.30", "250"], ["0.33", "300"]], [["0.28", "300"]]),
        "no": ([["0.72", "300"]], [["0.70", "300"]]),
    },
    "620103": {
        "yes": ([["0.18", "200"], ["0.21", "300"]], [["0.16", "300"]]),
        "no": ([["0.84", "300"]], [["0.82", "300"]]),
    },
    "620104": {"yes": ([["0.05", "500"]], [["0.03", "500"]]), "no": ([["0.96", "500"]], [["0.94", "500"]])},
}

STALE_BY = timedelta(minutes=5)


def book_payload(market: dict[str, Any], index: int, spec: dict[str, Any], side: str) -> dict[str, Any]:
    asks, bids = spec[side]
    stamp = AS_OF - (STALE_BY if spec.get("stale") else timedelta(seconds=2))
    tokens = json.loads(market["clobTokenIds"])
    # Polymarket's market-data guide lists bids ascending and asks descending (best last);
    # the fixtures use that wire order and the normalizer re-sorts.
    payload = {
        "market": market["conditionId"],
        "asset_id": tokens[index],
        "timestamp": ms(stamp),
        "hash": hashlib.sha1(f"{market['id']}:{side}:{stamp.isoformat()}".encode()).hexdigest(),  # noqa: S324
        "bids": [{"price": p, "size": s} for p, s in sorted(bids, key=lambda x: float(x[0]))],
        "asks": [{"price": p, "size": s} for p, s in sorted(asks, key=lambda x: -float(x[0]))],
        "min_order_size": str(market["orderMinSize"]),
        "tick_size": str(market["orderPriceMinTickSize"]),
        "neg_risk": market["negRisk"],
        "last_trade_price": asks[0][0],
    }
    return payload


def main() -> None:
    (ROOT / "kalshi").mkdir(parents=True, exist_ok=True)
    (ROOT / "polymarket").mkdir(parents=True, exist_ok=True)
    books = []
    for market in POLY_MARKETS:
        spec = POLY_BOOKS.get(market["id"])
        if spec is None:
            continue
        books.append(book_payload(market, 0, spec, "yes"))
        books.append(book_payload(market, 1, spec, "no"))
    kalshi_books = {
        ticker: {"orderbook_fp": {"yes_dollars": yes, "no_dollars": no}}
        for ticker, (yes, no) in KALSHI_BOOKS.items()
    }
    manifest = {
        "as_of": iso(AS_OF),
        "synthetic": True,
        "description": (
            "Synthetic, deterministic fixture set in documented Kalshi/Polymarket wire formats. "
            "Names, prices and dates are invented; nothing here describes a real listing."
        ),
        "expected": {
            "approved_pairs": {
                "KXFEDDECISION-26DEC-C25|610001": "EQUIVALENT",
                "KXUNRATE-26OCT-T4.5|610002": "EQUIVALENT",
                "KXCPIYOY-26NOV-T3.0|610003": "COMPLEMENTARY",
                "KXECBRATE-26OCT-CUT|610010": "EQUIVALENT",
            },
            "rejected_pairs": {
                "KXARTEMIS3-27JUL|610007": "A_IMPLIES_B",
                "KXINXY-26DEC31-T7000|610005": "A_IMPLIES_B",
                "KXSENATEOH-26-JAVE|610008": "UNRELATED",
                "KXPRESPARTY-28-DEM|610009": "PARTIALLY_OVERLAPPING",
                "KXGDP-26Q3-T3.0|610004": "B_IMPLIES_A",
                "KXBTCD-26DEC3117-T150000|610006": "AMBIGUOUS",
                "KXFEDDECISION-26DEC-H0|610001": "MUTUALLY_EXCLUSIVE",
            },
            "cases": {
                "true_equivalent": "KXFEDDECISION-26DEC-C25|610001",
                "complementary": "KXCPIYOY-26NOV-T3.0|610003",
                "same_topic_different_deadline": "KXARTEMIS3-27JUL|610007",
                "same_event_different_threshold": "KXINXY-26DEC31-T7000|610005",
                "same_candidate_different_office": "KXSENATEOH-26-JAVE|610008",
                "election_vs_popular_vote": "KXPRESPARTY-28-DEM|610009",
                "at_least_vs_greater_than": "KXGDP-26Q3-T3.0|610004",
                "different_resolution_sources": "KXBTCD-26DEC3117-T150000|610006",
                "independently_worded_equivalent": "KXUNRATE-26OCT-T4.5|610002",
                "stale_book_suppression": "KXECBRATE-26OCT-CUT|610010",
            },
            "validated_opportunities": [
                "KXFEDDECISION-26DEC-C25|610001|A",
                "KXCPIYOY-26NOV-T3.0|610003|C",
            ],
            "ineligible_markets": [
                "kalshi:KXPAYROLLS-26OCT",
                "kalshi:KXHIGHNY-26SEP24-T80",
                "polymarket:610011",
                "polymarket:610012",
                "polymarket:610013",
            ],
        },
    }

    def dump(relative: str, payload: Any) -> None:
        path = ROOT / relative
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    dump("manifest.json", manifest)
    dump("kalshi/events.json", {"events": KALSHI_EVENTS, "cursor": ""})
    dump("kalshi/series.json", {"series": SERIES})
    dump("kalshi/orderbooks.json", kalshi_books)
    dump(
        "kalshi/exchange_status.json",
        {
            "exchange_active": True,
            "trading_active": True,
            "intra_exchange_transfers_active": True,
            "exchange_estimated_resume_time": None,
            "exchange_index_statuses": [],
        },
    )
    dump("polymarket/markets.json", {"markets": POLY_MARKETS})
    dump("polymarket/books.json", books)


if __name__ == "__main__":
    main()
