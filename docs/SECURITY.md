# Security

## Paper-only guarantees

This version cannot place, sign, amend or cancel a real order on any venue. The guarantee
comes from what the code lacks, not from a flag that can be flipped. Each point below is
enforced by a test in `backend/tests/security/test_security.py`.

| Guarantee | Mechanism | Test |
| --- | --- | --- |
| No order-placement code | No module references Kalshi's `/portfolio/orders`, `create_order`, `post_order`, `place_order`, `sign_order`, `/orders/batched`, `py_clob_client`, `eth_account`, `web3` or `private_key=` | `test_no_order_placement_code_exists` |
| Network access is read-only | `ReadOnlyHttpTransport` exposes only `get_json`, `post_json_readonly`, `aclose` and read-only properties. `post_json_readonly` accepts only allow-listed paths: exactly one, Polymarket's read-only batch book query `POST /books`. Any other POST raises `ReadOnlyViolationError` before a request is built | `test_venue_transports_are_read_only` |
| The live-execution interface is not implemented | `LiveExecutionVenue` is a protocol. Its only class, `DisabledLiveExecutionVenue`, raises `NotImplementedError` from every method | `test_live_execution_adapter_always_refuses` |
| The disabled adapter is unreachable | An AST scan of every application module proves nothing imports `live_execution_disabled` | `test_disabled_live_adapter_is_unreachable_from_application_code` |
| No setting enables live trading | `PAPER_TRADING_ONLY=false` fails settings validation at startup. No setting holds a trading key, wallet, seed phrase or `live_trading` switch | `test_settings_contain_no_trading_credentials`, unit tests on `Settings` |
| Only paper actions mutate | The OpenAPI schema has exactly three POST routes: refresh a scan, paper-execute, reset the kill switch. No PUT, PATCH or DELETE | `test_only_paper_routes_mutate` |
| No secrets in responses | Every parameterless GET endpoint is called with an LLM key configured, and no response contains it. `/api/config/public` is an allow-list that reports only whether an optional key is set | `test_no_endpoint_returns_secrets` |
| Secrets are redacted from logs | The log filter removes PEM blocks, bearer tokens, `key=`/`secret=`/`password=`-style values, `sk-…` API keys, 64-hex strings (for example private keys) and seed-phrase-like strings, and drops fields whose names look secret | `test_logs_redact_suspicious_secrets` |

Every paper order has `simulated=True`. Every API response carries
`X-Paper-Trading-Only: true`. The dashboard shows a "Paper trading only" banner that cannot
be dismissed.

Enabling real trading would take deliberate engineering, not configuration: implement a
new adapter, wire it through the composition root, add signing and credential storage, and
change the tests above. [LIVE_TRADING_GAP_ANALYSIS.md](LIVE_TRADING_GAP_ANALYSIS.md) lists
what that would involve.

## Credentials

- **None are required.** The demo, the fixture mode and the live read-only scan run without
  any credential. All venue endpoints used are public market-data endpoints.
- **None are requested.** The code never asks for or reads venue API keys, private keys,
  wallet keys, seed phrases or signatures. If Kalshi's order-book endpoint ever answers
  401, the adapter falls back to public market-level top of book instead of asking for keys.
- **The only optional secret** is `OPTIONAL_LLM_API_KEY`, used by the optional veto-only
  relation adjudicator. It is a `SecretStr`, never logged, never returned by the API, and
  sent only to `OPTIONAL_LLM_BASE_URL`.
- `.env` is git-ignored. `.env.example` contains no secrets.

## Threat model

| Asset or threat | Risk | Mitigation |
| --- | --- | --- |
| Accidental real order | Financial loss | No order code, read-only transport, disabled adapter unreachable, tests above |
| Credential leakage | Account takeover | No trading credentials exist. Log redaction. Settings allow-list for public config. No secrets in fixtures |
| Wrong equivalence approval | A "hedge" that is really a directional bet | Conservative matcher (only proven EQUIVALENT/COMPLEMENTARY, unknown critical field → AMBIGUOUS), LLM can only veto, audit record of every check, basis-risk warnings in the UI |
| Stale or malformed data priced as real | Phantom opportunities | Quote gating: stale, future-dated, crossed, malformed, empty, thin and non-trading books are suppressed. WebSocket desync detection with REST re-seed. Decimal parsing that rejects NaN, infinity and booleans |
| Prompt injection via market text sent to the optional LLM | Manipulated relation judgement | Market text is wrapped as data with contract delimiters neutralized. The model's answer is parsed strictly, and it can only veto, never approve |
| Duplicate paper execution (double click, retry) | Inflated paper P&L | Required `Idempotency-Key` on `POST /api/paper/execute/{id}`: the same key replays the stored result, a different payload returns 409, and a service-wide lock serializes executions |
| Runaway failures | Unbounded simulated losses | Kill switch after `KILL_SWITCH_MAX_CONSECUTIVE_FAILURES`. `MAX_UNHEDGED_NOTIONAL` forces unwinds |
| Hostile or oversized venue responses | Crash or resource exhaustion | Timeouts, bounded retries with jittered backoff, page limits (`LIVE_MAX_PAGES_*`), schema validation with per-field errors recorded as integrity issues |
| Cross-site requests to the local API | Unwanted paper actions from another origin | CORS limited to configured origins, `GET`/`POST` only, and a small header allow-list. The API has no authentication, so it must be bound to localhost or a private network (see below) |

## Deployment notes

- The API has **no authentication**. It is designed for a single user on localhost or a
  private network. Do not expose it to the internet. Put an authenticating reverse proxy in
  front if remote access is needed.
- The Docker images run as a non-root user and install only locked runtime dependencies (no
  dev tools). Docker Compose publishes ports on `127.0.0.1` only.
- Dependencies are pinned by `backend/uv.lock` and `frontend/package-lock.json`.

## Compliance boundaries

- The code does not implement, and must not be extended to implement, evasion of geographic
  restrictions, identity verification, exchange controls, rate limits or terms of service.
  It uses public endpoints at conservative request rates and respects the venues' documented
  limits.
- There is no deposit, withdrawal, fund transfer, wallet approval or blockchain
  transaction code.
- Access to Kalshi and Polymarket is subject to each venue's eligibility rules and
  jurisdictional restrictions. Using public market data does not grant permission to trade.
- This is an educational research tool. It is not financial, legal or tax advice.

## Reporting

Please report a suspected security issue privately to the repository owner rather than in
a public issue, and include the steps to reproduce it.
