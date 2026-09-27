# Decisions

Architectural decisions with the alternatives considered. Each one records the trade-off
it accepts.

## D1. Modular monolith, one service

**Decision.** One Python service holds adapters, matching, arbitrage, the paper engine,
persistence and the API. The CLI and the API are thin shells over the same service layer.

**Why.** The work is a sequential pipeline over a few thousand markets. It has no
independent scaling needs, and one process keeps scans consistent and tests fast. Interfaces
(`MarketDataVenue`, `PaperExecutionVenue`, `RelationAdjudicator`, `EmbeddingProvider`) sit
where a split would happen later.

**Trade-off.** A long live scan and API requests share one event loop. Scans are serialized
by a lock, and the refresh endpoint returns 409 while one is running.

## D2. Read-only by construction, not by flag

**Decision.** No code path can send a trading request. The HTTP transport has no generic
POST, only an allow-listed read-only `POST /books`. `LiveExecutionVenue` exists only as a
protocol and a disabled stub that nothing imports. `PAPER_TRADING_ONLY=false` fails
validation.

**Why.** A flag can be flipped by accident or by an environment file. Missing code cannot.
Security tests pin all of this (see [SECURITY.md](SECURITY.md)).

## D3. Fixtures are raw venue payloads replayed through the real adapters

**Decision.** `fixtures/` holds Kalshi and Polymarket responses in their documented wire
formats. `FixtureTransport` serves them to the same `KalshiMarketDataVenue` and
`PolymarketMarketDataVenue` classes used live, including pagination, the batch-book
fallback and the Kalshi 401 path.

**Alternatives.** Hand-built `NormalizedMarket` objects would skip the normalizers, so the
demo would not show that real payloads parse.

**Trade-off.** Fixtures must track schema changes. `scripts/generate_fixtures.py`
regenerates them deterministically, and `fixtures/manifest.json` records the expected
approvals, rejections and outcomes that the demo's acceptance checks assert.

## D4. A frozen clock in fixture mode

**Decision.** In fixture mode the whole service uses `FrozenClock` pinned to the fixture's
`as_of` (2026-09-25T14:00:00Z).

**Why.** Staleness depends on time. With a real clock every fixture opportunity would be
stale by the next day and the demo would stop demonstrating execution. With a frozen clock,
IDs, statuses and P&L are reproducible byte for byte. The stale-book case is built into the
data: one book is 300 s older than the rest. The dashboard judges freshness with the
service clock from `/health`, never the browser's.

## D5. Conservative, deterministic matcher; the LLM can only veto

**Decision.** Relations come from proposition extraction plus interval and state algebra.
Anything unparseable is `AMBIGUOUS`. Approval needs `EQUIVALENT` or `COMPLEMENTARY`,
confidence >= 0.90 and no blocking mismatch. The optional Anthropic adjudicator runs only
on pairs the rules already approved, and it can only downgrade them.

**Alternatives.** Embedding similarity with a threshold, or an LLM classifier as in the
paper. Both produce plausible false positives, and in cross-venue arbitrage a false positive
is an unhedged position (see [PAPER_MAPPING.md](PAPER_MAPPING.md)).

**Trade-off.** Low recall on live data. Contract templates the extractor does not
recognize are rejected. This is the intended failure mode.

## D6. Hashing embeddings by default

**Decision.** Candidate ranking uses a deterministic hashed bag of character trigrams behind
the `EmbeddingProvider` interface. A neural model can be plugged in by implementing it.

**Why.** No model download, no network, deterministic results, and good enough for ranking
candidates that deterministic checks then verify. Embeddings never decide a relation.

## D7. Interval algebra for propositions

**Decision.** Each proposition's YES region is an `IntervalSet` over its metric (value,
date or count), with open and closed bounds. `classify_sets` returns equal, complement,
subset, superset, disjoint or overlapping.

**Why.** Threshold wording (`>=` vs `>`, "at least", "more than", "by" vs "before")
decides the relation. Exact set comparison catches the cases where two contracts differ
only at a boundary value.

## D8. Decimal everywhere, with explicit rounding direction

**Decision.** Prices, quantities, fees and P&L are `Decimal`, parsed from strings.
Floats are converted through `repr`, and NaN, infinity and booleans are rejected. Costs
round up, proceeds round down, quantities floor to the lot size, and venue fee rounding is
reproduced per fill.

**Why.** The spec requires it, and binary-contract edges are often a fraction of a cent.

## D9. Storage: validated JSON documents plus indexed columns, `create_all`, no migrations yet

**Decision.** SQLAlchemy 2 async. Domain objects are stored as validated JSON documents
next to the columns used for filtering. The schema is created at startup. SQLite is the
default. PostgreSQL works through the `postgres` extra and is covered by an optional
integration test (`TEST_POSTGRES_URL`) that runs in CI.

**Trade-off.** Without Alembic, schema changes need `reset-db` (acceptable: all state is
derived from venue data and paper simulations). Add Alembic before any persistent
deployment.

## D10. Kalshi over REST polling, Polymarket over WebSocket with REST fallback

**Decision.** Kalshi's WebSocket requires API keys even for public channels, so Kalshi
books are polled. Polymarket's market channel is public and is used with bounded reconnects,
then REST polling. Local WebSocket books are cross-checked against the venue's reported best
bid/ask, and desynchronized books are re-seeded from REST.

**Why.** It keeps the "no credentials" guarantee. A misunderstood delta format degrades to
REST data rather than to a wrong book.

## D11. Executable scope: two-leg equivalence and complement only

**Decision.** Only `CROSS_VENUE_BINARY` opportunities on `EQUIVALENT` or `COMPLEMENTARY`
pairs can be `VALIDATED` and paper-executed. Implication, exclusivity, market-rebalancing
and multi-condition combinatorial results are computed with the same depth, fee and buffer
logic, and shown as analytics capped at `CANDIDATE`.

**Why.** The spec restricts executable relations. Implications are real arbitrage when
correct, but their correctness rests on wording analysis. Market rebalancing needs
multi-leg execution, which is out of scope for this MVP.

## D12. Sequential execution policy by default

**Decision.** The default `EXECUTION_POLICY=sequential` sends Kalshi first, then hedges
the filled quantity on Polymarket within `MAX_HEDGE_SLIPPAGE`. `simultaneous_ioc` is
available.

**Why.** Sequential limits the naked exposure to what actually filled, at the cost of
latency between legs. That latency is exactly the risk the fault scenarios model.

## D13. Frontend toolchain pins

- **Vitest 4.0.18** instead of 4.1.x: `npm ci` failed with an npm arborist error
  (`Cannot read properties of null (reading 'edgesOut')`) when resolving the 4.1 dependency
  tree in this environment. 4.0.18 resolves cleanly.
- **ESLint 10** (flat config) with `typescript-eslint` 8.70 and `eslint-plugin-react-hooks`
  7.1, including the purity rules. These caught a `Date.now()` call during render, which was
  removed.
- **Dashboard runtime**: the Docker image serves the built bundle with `vite preview`, which
  also proxies `/api` to the backend, instead of nginx. It keeps one proxy configuration for
  development and Docker. For production, serve `dist/` from a CDN or reverse proxy.

## D14. Default data mode is fixture

**Decision.** `DATA_MODE=fixture` by default. The live scan is explicit (`--live`,
`make live-scan`, or `DATA_MODE=live`).

**Why.** The first run must work offline without credentials, and a scan against a venue
should be a deliberate choice.

## D15. Docker build TLS

**Decision.** The backend Dockerfile sets `UV_NATIVE_TLS=true`, so uv trusts the system
certificate store.

**Why.** Corporate and sandbox proxies that re-sign TLS break uv's bundled roots. With the
public CA store the setting has no effect.
