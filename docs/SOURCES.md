# Sources

Official documentation used to implement the venue adapters and fee models. All pages were
accessed on **2026-09-27**. Venue APIs change often: re-check these pages before relying on
this code, and prefer the live documentation over this summary.

The build environment could not reach the venue APIs themselves (the sandbox egress proxy
returns HTTP 403 for `*.kalshi.com` and `*.polymarket.com` API hosts), so the schemas below
come from the documentation. They are encoded in the normalizers, the fixtures and the
mocked-HTTP tests.

## Research

| Source | Used for |
| --- | --- |
| Saguillo, Ghafouri, Kiffer, Suarez-Tangil, *Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets*, [arXiv:2508.03474](https://arxiv.org/abs/2508.03474) | Definitions 2–4, heuristic reduction, market rebalancing vs combinatorial arbitrage (see [PAPER_MAPPING.md](PAPER_MAPPING.md)) |

## Kalshi

| Page | Used for |
| --- | --- |
| [Quick start: market data](https://docs.kalshi.com/getting_started/quick_start_market_data.md) | Base URL `https://external-api.kalshi.com/trade-api/v2`. Market-data endpoints need no API keys |
| [Get Events](https://docs.kalshi.com/api-reference/events/get-events.md) | `GET /events?status=open&with_nested_markets=true`, cursor pagination, `limit` ≤ 200 |
| [Get Series](https://docs.kalshi.com/api-reference/market/get-series.md) | `fee_type`, `fee_multiplier`, settlement sources, contract URLs |
| [Get Market](https://docs.kalshi.com/api-reference/market/get-market.md) | Market fields: `rules_primary`/`rules_secondary`, `yes_sub_title`, fixed-point `*_dollars` / `*_fp` values, status enum |
| [Get Market Orderbook](https://docs.kalshi.com/api-reference/market/get-market-orderbook) | `GET /markets/{ticker}/orderbook`, `orderbook_fp.yes_dollars` / `no_dollars` as `[price, count]` bids, `depth` parameter |
| [Orderbook responses guide](https://docs.kalshi.com/getting_started/orderbook_responses.md) | Bids only. "A YES BID at price X is equivalent to a NO ASK at price ($1.00 - X)". Arrays in ascending price order, best bid last |
| [Get Exchange Status](https://docs.kalshi.com/api-reference/exchange/get-exchange-status.md) | `trading_active` / `exchange_active` for the venue health panel and gating |
| [Rate limits](https://docs.kalshi.com/getting_started/rate_limits.md) | Token-bucket read budgets per tier (Basic: 200/s). The client stays far below this by default (`KALSHI_MAX_REQUESTS_PER_SECOND`) |
| [WebSocket connection](https://docs.kalshi.com/websockets/websocket-connection.md) | "Authentication is required to establish the connection … Some channels carry only public market data, but the connection itself still requires authentication." |
| [Fee rounding](https://docs.kalshi.com/getting_started/fee_rounding.md) | Per fill: `trade_fee = ceil_6dp(model_fee)`, then balance alignment to the member's precision tier ($0.0001 direct, $0.01 non-direct) with a rounding fee and an accumulator rebate |
| [Fixed-point migration](https://docs.kalshi.com/getting_started/fixed_point_migration.md) | Subpenny prices and fractional contracts: everything parsed as `Decimal` from strings |
| [Fee schedule (PDF), effective July 7, 2026](https://kalshi.com/docs/kalshi-fee-schedule.pdf) | Taker: `fees = round up(M x 0.07 x C x P x (1-P))`, where "round up = rounds up such that the fee + positionCost is rounded to a centicent". Maker 0.0175. Series multipliers (for example `0 0` for fee-free series) |
| [API changelog](https://docs.kalshi.com/changelog/index.md) | Deprecations (market `title`/`subtitle`, integer-cent fields) |

## Polymarket

| Page | Used for |
| --- | --- |
| [List markets (keyset pagination)](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination) | Gamma `GET /markets/keyset`, `limit` ≤ 100, `after_cursor` → `next_cursor`. `outcomes` / `clobTokenIds` (JSON-encoded arrays), `feesEnabled`, `feeSchedule`, `negRisk`. No auth |
| [Get order book](https://docs.polymarket.com/api-reference/market-data/get-order-book) | CLOB `GET /book?token_id=`: `bids`, `asks`, `timestamp`, `hash`, `tick_size`, `min_order_size`, `neg_risk`. No auth |
| [Get order books (request body)](https://docs.polymarket.com/api-reference/market-data/get-order-books-request-body) | CLOB `POST /books` with `[{"token_id": ...}]`, a read-only batch query |
| [Prices and order books](https://docs.polymarket.com/market-data/prices-order-books) | "Maximum 500 items per request" for `POST /books` |
| [Market channel (WebSocket)](https://docs.polymarket.com/developers/CLOB/websocket/market-channel) | `wss://ws-subscriptions-clob.polymarket.com/ws/market`, subscription `{"assets_ids": [...], "type": "market"}`, `PING` every 10 s, `book` / `price_change` / `tick_size_change` / `last_trade_price` events. No auth |
| [Rate limits](https://docs.polymarket.com/api-reference/rate-limits) | Gamma `/markets` 300 req / 10 s. CLOB `/book` 1,500 / 10 s, `/books` 500 / 10 s. Excess requests are throttled (queued) rather than rejected |
| [Fees](https://docs.polymarket.com/trading/fees) | `fee = C × feeRate × p × (1 − p)`, takers only, rounded to 5 decimals (minimum 0.00001). Category rates: crypto 0.07; sports, economics, culture, weather, other 0.05; finance, politics, mentions, tech 0.04; geopolitics 0 |

## Documented conflicts and how the code handles them

| Conflict | Handling |
| --- | --- |
| **Kalshi order-book auth.** The quick-start and order-book guides say market data needs no authentication. The API reference page for `GET /markets/{ticker}/orderbook` lists the `KALSHI-ACCESS-*` headers. | Requests are sent without credentials. On HTTP 401 the adapter falls back to market-level top of book (`yes_bid_dollars`, `yes_ask_dollars` and sizes from `GET /markets/{ticker}`), flags the book `depth_limited`, and adds that to the opportunity's assumptions. Nothing asks for keys. |
| **Kalshi level ordering.** The guide says ascending (best bid last). The reference says "best to worst". | Levels are always re-sorted after parsing (`app/venues/common.py::build_levels`). |
| **Polymarket level ordering.** The API reference says bids descending and asks ascending. The market-data guide says bids ascending and asks descending, with best prices last. | Levels are always re-sorted. The fixtures use the guide's ordering, so the re-sort is exercised. |
| **Polymarket WebSocket shapes.** The docs show both the classic event shape (`event_type`, `price_changes[]`, `asset_id`) and an envelope shape (`type` + `payload.priceChanges[]`, `tokenId`). It is not explicit whether `price_change.size` is the new aggregate level size. | Both shapes are parsed. Updates are applied as new aggregate sizes, and every update that carries the venue's best bid/ask is cross-checked. On any disagreement the token is marked desynchronized, produces no snapshots, and is re-seeded from REST (`ws_book_desync_total` metric). |
| **Polymarket fee rounding direction.** The docs give 5-decimal precision but not the direction. | Rounded **up**, which never understates cost. |
| **Kalshi fee rounding.** The PDF rounds fee + position cost to a centicent. The API guide rounds the trade fee to 6 dp first, then aligns to the member's precision tier, with a possible rebate. | Both steps are applied per fill. The tier quantum is `KALSHI_FEE_ROUNDING_QUANTUM` (default 0.0001; set 0.01 for non-direct members). The rebate is ignored, which is conservative. |
| **Missing fee metadata.** Some markets omit `feeSchedule`, or series omit `fee_multiplier`. | Conservative fallbacks: `POLYMARKET_FALLBACK_FEE_RATE=0.07` (the highest published category rate) and `KALSHI_FALLBACK_FEE_MULTIPLIER=2`. The fallback is recorded in the opportunity's assumptions. |
| **Kalshi base URL.** Older material uses `https://api.elections.kalshi.com/trade-api/v2`. | `external-api.kalshi.com` is the default, per the current quick start. The host is configurable (`KALSHI_BASE_URL`). |

## Assumptions not stated by either venue

- Each winning binary contract pays exactly 1 unit of collateral: USD on Kalshi, pUSD
  (USDC-backed, since the CLOB V2 upgrade) on Polymarket. The two are treated as 1:1.
  Conversion, bridging and withdrawal costs are covered only by `SETTLEMENT_COST_BUFFER`.
- Displayed depth is assumed to be available at the book timestamp. Hidden liquidity,
  self-trade prevention and queue position are not modelled. The paper engine covers the
  resulting leg risk with fault scenarios.
- Access restrictions (geography, KYC, eligibility) are the user's responsibility. The code
  does not implement or evade any of them.
