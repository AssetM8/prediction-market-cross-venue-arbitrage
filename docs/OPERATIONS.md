# Operations

## Running

| Goal | Command |
| --- | --- |
| Install locked dependencies | `make install` (`uv sync --frozen --all-extras` + `npm ci`) |
| Offline demo | `make demo` |
| API + dashboard with reload | `make dev` (API `127.0.0.1:8000`, dashboard `127.0.0.1:5173`) |
| API only | `make dev-backend` or `cd backend && uv run python -m app.cli serve` |
| Live read-only scan (CLI) | `make live-scan` |
| API on live data | `DATA_MODE=live make dev-backend`, then use **Re-scan** in the dashboard or `POST /api/market-pairs/refresh` |
| Docker | `make docker-up` / `make docker-down` (API `127.0.0.1:8000`, dashboard `127.0.0.1:8080`) |

Configuration comes from environment variables or `.env` (copy `.env.example`). All
settings are documented there. The important ones:

- `DATA_MODE=fixture|live`. Fixture mode uses a simulated clock pinned to the fixture
  timestamp.
- `AUTO_SCAN_ON_STARTUP=true` runs a fixture scan at API start when the database is empty.
  Live scans are always explicit.
- `DATABASE_URL`: SQLite by default,
  `postgresql+asyncpg://user:pass@host:5432/db` for PostgreSQL (install the `postgres` extra).
- Thresholds: `BOOK_STALE_AFTER_SECONDS`, `MIN_NET_PROFIT`, `MIN_RETURN_ON_CAPITAL`,
  `MARKET_PAIR_MIN_CONFIDENCE` and the buffers.
- Live scan bounds: `LIVE_MAX_PAGES_KALSHI`, `LIVE_MAX_PAGES_POLYMARKET`,
  `LIVE_MAX_BOOK_CANDIDATES`, `KALSHI_MAX_REQUESTS_PER_SECOND`,
  `POLYMARKET_MAX_REQUESTS_PER_SECOND`.

### PostgreSQL

```bash
cd backend && uv sync --extra postgres
DATABASE_URL=postgresql+asyncpg://arb:arb@127.0.0.1:5432/arb uv run python -m app.cli demo
# integration test against a real server:
TEST_POSTGRES_URL=postgresql+asyncpg://arb:arb@127.0.0.1:5432/arb uv run pytest tests/integration/test_postgres.py
```

With Docker: `docker compose --profile postgres up -d --build` with
`BACKEND_EXTRAS="--extra postgres"` and
`DOCKER_DATABASE_URL=postgresql+asyncpg://arb:arb@postgres:5432/arb`.

## Resetting

| What | How |
| --- | --- |
| Everything (markets, pairs, opportunities, paper state, audit) | `make reset-db` or `python -m app.cli reset-db`. `make demo` also resets first |
| Kill switch only | Dashboard portfolio panel → **Reset kill switch**, `POST /api/paper/kill-switch/reset`, or `python -m app.cli kill-switch-reset` |
| Docker state | `docker compose down -v` removes the backend data volume |
| Fixtures | `make fixtures` regenerates them deterministically from `scripts/generate_fixtures.py` |

All state is derived from venue data and simulations, so a reset loses nothing that cannot
be recomputed. Export the audit trail first if it is needed:
`python -m app.cli export-audit --output audit.jsonl`.

## Observability

- **Logs.** JSON lines on stderr (`LOG_JSON=true`), one per event, with `correlation_id`
  (scan run or HTTP request), `venue`, `market_id`, `pair_id`, `opportunity_id` and
  `paper_order_id` when known. Secrets are redacted before formatting.
- **Metrics.** `GET /metrics` (JSON) or `GET /metrics?format=prometheus`.

  | Metric | Meaning |
  | --- | --- |
  | `http_requests_total{venue,method}`, `http_errors_total{venue,kind}`, `http_retries_total{venue}` | Venue API traffic and failures |
  | `venue_last_success{venue}` | Timestamp of the last successful venue call |
  | `api_requests_total{path,status}` | API traffic (first path segment) |
  | `markets_listed{venue}` | Markets returned by the last listing |
  | `pairs_evaluated_total`, `pairs_approved_total`, `pairs_rejected_total` | Matching outcomes |
  | `opportunities_total{strategy,status}` | Opportunities by status |
  | `stale_books_total{venue}`, `normalization_errors_total{venue}` | Data quality |
  | `book_fetch_failures_total`, `book_batch_fallbacks_total` | Order-book retrieval problems |
  | `ws_failures_total`, `ws_book_desync_total`, `ws_fallback_to_rest_total` | Polymarket stream health |
  | `paper_executions_total{outcome}` | Paper execution outcomes |

- **Health.** `GET /health` reports database status, data mode, the service clock, the last
  scan and the last recorded reachability of each venue. `GET /api/venues?check=true` probes both venues now.
- **Audit.** `GET /api/audit?category=…`, or the dashboard's audit tab. Categories: `scan`,
  `pair_decision`, `opportunity`, `paper_execution`, `kill_switch`.

## Recovery

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Live scan exits `3` | Venue unreachable: network, proxy policy, geography or outage | Read the `venue request failed` log lines (host and status). Retry later. Do not route around a policy denial |
| `http_retries_total` climbing, `429` in logs | Rate limiting | Lower `*_MAX_REQUESTS_PER_SECOND`. The client already backs off with jitter |
| Kalshi books flagged `depth_limited` | Order-book endpoint returned 401 | Expected fallback to top of book. Opportunities are sized to the best level only |
| `ws_book_desync_total` climbing | Polymarket WebSocket delta semantics changed | Books are re-seeded from REST automatically. Set `POLYMARKET_WS_ENABLED=false` to poll only |
| Many `normalization_errors_total` | Venue schema change | Compare a raw response with the normalizer's schema notes and with [SOURCES.md](SOURCES.md). Affected markets are skipped, never guessed |
| Every opportunity suppressed as stale | Scan too slow or `BOOK_STALE_AFTER_SECONDS` too tight | Re-scan. Lower `LIVE_MAX_BOOK_CANDIDATES`, or raise the threshold knowingly |
| Kill switch engaged | `KILL_SWITCH_MAX_CONSECUTIVE_FAILURES` failed paper executions | Review the executions, then reset |
| Refresh returns `409` | A scan is already running | Wait for it to finish |
| Schema error after upgrading | No migrations yet | `make reset-db` |

## Verification log

What was run while building this repository on **2026-09-27**, in a Linux sandbox with
Python 3.12.3 (uv 0.8.17), Node 22 and Docker. Commands were run from the repository root
unless noted.

| Check | Command | Result |
| --- | --- | --- |
| Install from lock files | `make install` | OK |
| Lint | `make lint` (ruff check, ruff format --check, eslint --max-warnings=0) | Clean |
| Type check | `make typecheck` (mypy --strict on 92 files, tsc) | Clean |
| Tests | `make test` | Backend: 224 passed, 1 skipped (the PostgreSQL test without `TEST_POSTGRES_URL`). Frontend: 14 passed |
| PostgreSQL | `TEST_POSTGRES_URL=… pytest tests/integration/test_postgres.py` against a local PostgreSQL 16 | 1 passed |
| Demo | `make demo` | 8/8 acceptance checks pass. 4 pairs approved out of 14 candidates, 2 validated opportunities, hedged and leg-risk executions |
| Browser smoke | `make smoke` (Playwright, Chromium) | 1 passed: dashboard loads, paper-executes a validated opportunity |
| Production build | `make build` | OK |
| CLI | `health`, `sync-markets`, `match-markets`, `scan`, `paper-execute` (normal, `second_leg_reject`, `price_move_before_second_leg`), `export-audit` | All exit 0 in fixture mode |
| Settings | `.env.example` loaded by `Settings`. `PAPER_TRADING_ONLY=false` | Loads. `false` rejected |
| Docker | `docker compose up -d --build --wait`, then curl `/health`, the dashboard, `/api/opportunities` through the dashboard proxy, and the `X-Paper-Trading-Only` header | Both containers healthy, running as non-root users. All checks pass |
| Live read-only scan | `make live-scan` | **Could not evaluate**: both venues returned HTTP 403 from the sandbox's egress proxy (`gateway answered 403 to CONNECT` for `external-api.kalshi.com` and `gamma-api.polymarket.com`). The CLI reported venue data unavailable and exited `3`. No opportunity was reported |

Environment caveats:

- **Venue APIs were unreachable** from the sandbox (organization egress policy), so the
  live adapters are verified against mocked HTTP built from the documented schemas
  (`tests/integration/test_live_adapters_mocked.py`, `test_polymarket_stream.py`), and
  against fixtures in the venues' raw formats. A live scan from an unrestricted network is
  the first thing to run elsewhere.
- **Docker Hub was unreachable** too, so `python:3.12-slim` and `node:22-alpine` could not be
  pulled. The stack was verified with a locally built stand-in base image passed via the
  `PYTHON_IMAGE` and `NODE_IMAGE` build arguments. The Dockerfiles default to the official
  images. The CI workflow (`.github/workflows/ci.yml`) is set up to build and smoke-test the
  stack with them, but it has not been run from here: the repository was not pushed.
  The optional `postgres` Compose profile was not exercised for the same reason (its image
  could not be pulled). PostgreSQL support itself was verified against a local server, as above.
