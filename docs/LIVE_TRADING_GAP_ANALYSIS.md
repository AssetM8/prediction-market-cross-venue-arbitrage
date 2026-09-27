# Live trading gap analysis

This version is paper-only by construction (see [SECURITY.md](SECURITY.md)). This document
lists what would have to exist before any real order could be sent. It is a scoping aid,
not a plan or a recommendation. Every item needs its own design review, and several need
legal review.

## 1. Legal and account prerequisites

- Eligibility on both venues in the operator's jurisdiction. Kalshi is a CFTC-regulated
  exchange with KYC. Polymarket's access rules depend on jurisdiction and product. Nothing in
  this system may bypass geographic, identity or eligibility controls.
- Terms-of-service review for automated trading and API use on each venue.
- Tax, reporting and record-keeping obligations for both venues and both collateral types
  (USD and pUSD).

## 2. Credentials and signing

| Venue | What live trading requires | Gap |
| --- | --- | --- |
| Kalshi | API key ID plus an RSA private key. Each request is signed (`KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP`) | No key storage, no signer, no authenticated transport |
| Polymarket | A funded wallet on Polygon, derived CLOB API credentials, and EIP-712 order signing | No wallet handling, no signer, no L1/L2 auth |

Needed: a secrets manager or HSM/KMS (never environment variables in plain text), key
rotation, least-privilege keys (trading only, no withdrawal), a signer isolated in its own
process with an audited interface, and redaction tests extended to the new formats.

## 3. Execution adapters

- Implement `LiveExecutionVenue` per venue: order placement, cancellation, amendments,
  order status queries, fills via authenticated WebSockets, and reconciliation after a
  restart. This is new code in a new module. Do not modify the disabled stub.
- Venue order semantics: Kalshi IOC and FOK, client order IDs, the `buy_max_cost` guard;
  Polymarket FOK/FAK/GTC, tick sizes, minimum order sizes, negative-risk markets.
- Idempotent client order IDs derived from the paper execution ID, so retries cannot
  double-submit.
- Handling of partial acknowledgement, unknown order state after a timeout (query before
  retrying), self-trade prevention and venue-side rate limits.

## 4. Risk controls (pre-trade and real-time)

- Hard limits enforced *outside* the strategy code: per-order notional, per-market
  position, per-venue exposure, daily loss, and total unhedged notional.
- Price bands: reject orders beyond the validated limit price plus slippage.
- Reconciliation of positions and balances against venue state before each execution. A
  mismatch halts trading.
- A kill switch that cancels resting orders and blocks new ones across both venues. It
  must survive process restarts and be operable manually.
- Automatic hedge or unwind routines tested against recorded market data, with human
  escalation for residuals above a threshold.

## 5. Capital and settlement

- Pre-funded balances on both venues sized for the maximum simultaneous bundles. Moving
  funds between Kalshi (USD, bank rails) and Polymarket (pUSD on Polygon) takes days, and
  is out of scope for automation here.
- Settlement mismatch handling: disputed or delayed resolution on one venue, contract
  amendments, and cancellation rules that differ (50/50 vs NO). This is the main source of
  basis risk even for perfectly matched pairs.
- Real capital cost and opportunity cost of capital locked until the later resolution.

## 6. Matching quality

- The deterministic matcher must be measured on a labeled set of real live pairs:
  precision on approvals should be effectively 100%, with every error analysed.
- Human review of each newly approved pair before the first live trade on it, with the
  reviewer and decision recorded in the audit trail.
- Monitoring for rule or settlement-source changes on approved pairs (Kalshi contract
  amendments, Polymarket description edits). Any change revokes approval.

## 7. Market data quality

- Authenticated WebSockets for Kalshi (lower latency, sequence numbers), sequence-gap
  detection and snapshot recovery.
- Clock synchronization (NTP) and latency measurement per venue. Staleness thresholds
  derived from measured latency, not defaults.
- Stress tests for thin books, fast markets and venue outages.

## 8. Operations

- Alembic migrations, durable storage for orders and fills (PostgreSQL), backups.
- Alerting on errors, desyncs, residual exposure, kill-switch activation and
  reconciliation failures.
- Authentication and authorization on the API (currently none), audit of every operator
  action, and separate read-only and trading roles.
- A staged rollout: shadow mode (live data, paper fills compared to hypothetical live
  fills), then tiny size limits, then gradual increases based on measured slippage and leg
  failure rates.

## 9. Testing

- A venue sandbox or demo environment where one exists (Kalshi provides a demo
  environment), used before any production credentials exist.
- Contract tests against recorded live responses. Chaos tests for every fault scenario the
  paper engine simulates, run against the real adapters in the sandbox.
- A test that fails the build if any trading capability becomes reachable without explicit
  configuration *and* a separate deployment artifact.

## Summary

The gap is large by design. Paper execution shows the arithmetic and the leg-risk
mechanics. Real trading adds regulated account access, key custody, signing, venue-specific
order semantics, reconciliation, capital logistics across two settlement systems, and a
matching process with human sign-off. None of these exist here.
