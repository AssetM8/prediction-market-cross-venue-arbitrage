# Implementation plan

Written before implementation (2026-09-27) and kept as a record of the intended build order.
The final state of the repository is described in `README.md` and `docs/ARCHITECTURE.md`.

## Constraints discovered up front

- The build sandbox's egress proxy returns HTTP 403 for `api.elections.kalshi.com`,
  `external-api.kalshi.com`, `gamma-api.polymarket.com`, `clob.polymarket.com` and Docker Hub.
  Official documentation was read through the documentation fetch tool instead.
  Live adapters are therefore verified against mocked HTTP (respx) built from the documented
  schemas, and the end-to-end demo runs on checked-in fixtures in the venues' raw API formats.
- Kalshi's WebSocket requires API-key authentication even for public channels, so the
  credential-free MVP polls Kalshi over REST. Polymarket's market WebSocket is public and is
  used with REST polling as fallback.
- Both venues publish fee formulas that depend on `p * (1 - p)`; fees are modelled from
  documented metadata with conservative fallbacks.

## Build order (vertical slices)

1. Domain models, Decimal helpers, clock, IDs.
2. Venue normalizers for Kalshi and Polymarket, a read-only HTTP transport and a fixture
   transport that replays raw API payloads through the same normalizers.
3. Matching pipeline: eligibility, canonicalization, candidate blocking, proposition
   extraction, deterministic checks, relation classification via interval/state algebra,
   optional LLM veto, conservative approval.
4. Arbitrage engine: payoff verification by joint-state enumeration, depth-walking optimizer,
   fee models, buffers, gating; market-rebalancing and combinatorial analytics from the paper.
5. Paper execution simulator: policies, deterministic fault scenarios, unwind, kill switch,
   per-venue cash, P&L, audit trail.
6. Persistence (SQLAlchemy async, SQLite default), services, FastAPI, Typer CLI, metrics,
   structured logging with redaction.
7. Live read-only adapters (retry, rate limiting, pagination, WebSocket with REST fallback).
8. React dashboard, Vitest, Playwright smoke test.
9. Tests (unit, property, integration, security), Docker, Makefile, CI, docs.
10. Run everything, fix failures, review for secret leakage and live-order paths.
