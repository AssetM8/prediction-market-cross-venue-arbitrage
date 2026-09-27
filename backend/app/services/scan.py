"""Scan service: markets -> matching -> books -> opportunities -> persistence + audit.

Used by ``POST /api/market-pairs/refresh``, ``python -m app.cli scan`` (live or fixture)
and the deterministic demo. It never places orders; the only venue calls are read-only
market-data requests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.arbitrage.cross_venue import CrossVenueCalculator
from app.arbitrage.payoff import verified_constructions
from app.arbitrage.rebalancing import build_groups, evaluate_group
from app.core.http import VenueHttpError
from app.core.ids import stable_id
from app.core.logging import get_logger, log_context, log_event, new_correlation_id
from app.core.metrics import METRICS
from app.domain.enums import DataSource, OpportunityStatus, Relation, Venue
from app.domain.interfaces import BinaryBookSnapshot, VenueHealth
from app.domain.models import ArbitrageOpportunity, MarketPair, NormalizedMarket
from app.services.container import AppContainer, VenueBundle
from app.venues.common import NormalizationError

logger = get_logger(__name__)

ANALYTIC_RELATIONS = frozenset({Relation.A_IMPLIES_B, Relation.B_IMPLIES_A, Relation.MUTUALLY_EXCLUSIVE})
LIVE_MAX_REBALANCING_GROUPS = 5
MAX_GROUP_SIZE = 8


@dataclass
class ScanReport:
    run_id: str
    kind: str
    data_source: DataSource
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    matching: dict[str, Any] = field(default_factory=dict)
    pairs: list[MarketPair] = field(default_factory=list)
    opportunities: list[ArbitrageOpportunity] = field(default_factory=list)
    health: list[VenueHealth] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for opp in self.opportunities:
            by_status[opp.status.value] = by_status.get(opp.status.value, 0) + 1
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "data_source": self.data_source.value,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "matching": self.matching,
            "pairs": len(self.pairs),
            "approved_pairs": sum(1 for p in self.pairs if p.approved_for_arbitrage_calculation),
            "opportunities_by_status": by_status,
            "validated_opportunities": [
                o.id for o in self.opportunities if o.status is OpportunityStatus.VALIDATED
            ],
            "errors": self.errors,
            "venues": {h.venue.value: {"reachable": h.reachable, "error": h.last_error} for h in self.health},
        }


def relationship_edges(pairs: list[MarketPair]) -> list[dict[str, Any]]:
    """Graph edges for every pair with a determined logical relation (combinatorial view)."""
    edges = []
    for pair in pairs:
        if pair.relation in (Relation.UNRELATED, Relation.AMBIGUOUS):
            continue
        edges.append(
            {
                "id": pair.id,
                "pair_id": pair.id,
                "source": f"kalshi:{pair.kalshi_market_id}",
                "target": f"polymarket:{pair.polymarket_market_id}",
                "source_title": pair.kalshi_title,
                "target_title": pair.polymarket_title,
                "relation": pair.relation.value,
                "confidence": str(pair.confidence),
                "approved": pair.approved_for_arbitrage_calculation,
                "risk_free_in_mvp": pair.approved_for_arbitrage_calculation,
                "verified_constructions": [
                    {"kalshi": k.value, "polymarket": p.value, "min_payout": str(payout)}
                    for k, p, payout in verified_constructions(pair.relation)
                ],
            }
        )
    return edges


async def _list(venue: Any, name: str, max_pages: int | None, errors: list[str]) -> list[NormalizedMarket]:
    try:
        markets: list[NormalizedMarket] = await venue.list_active_markets(max_pages=max_pages)
    except (VenueHttpError, NormalizationError) as exc:
        errors.append(f"{name}: market listing failed: {exc}")
        log_event(logger, logging.ERROR, "market listing failed", venue=name, error=str(exc))
        return []
    return markets


async def _books(
    venue: Any, name: str, markets: list[NormalizedMarket], errors: list[str]
) -> dict[str, BinaryBookSnapshot]:
    if not markets:
        return {}
    try:
        books: dict[str, BinaryBookSnapshot] = await venue.get_order_books(markets)
    except (VenueHttpError, NormalizationError) as exc:
        errors.append(f"{name}: order books unavailable: {exc}")
        return {}
    missing = len(markets) - len(books)
    if missing:
        errors.append(f"{name}: {missing} of {len(markets)} order books unavailable")
    return books


async def run_scan(container: AppContainer, venues: VenueBundle, *, kind: str) -> ScanReport:
    """Run one full read-only scan and persist its results."""
    settings = container.settings
    clock = container.clock
    started = clock.now()
    live = venues.data_source is DataSource.LIVE
    run_id = stable_id("run", kind, started.isoformat(), new_correlation_id())
    report = ScanReport(run_id=run_id, kind=kind, data_source=venues.data_source, started_at=started)
    repo = container.repo
    await repo.save_run(
        run_id,
        kind=kind,
        data_source=venues.data_source.value,
        status="running",
        started_at=started,
        finished_at=None,
        summary={},
    )
    with log_context(correlation_id=run_id):
        report.health = [await venues.kalshi.healthcheck(), await venues.polymarket.healthcheck()]
        await repo.save_health(report.health)
        for health in report.health:
            if not health.reachable:
                report.errors.append(f"{health.venue.value} unreachable: {health.last_error}")
        kalshi_markets = await _list(
            venues.kalshi, "kalshi", settings.live_max_pages_kalshi if live else None, report.errors
        )
        poly_markets = await _list(
            venues.polymarket,
            "polymarket",
            settings.live_max_pages_polymarket if live else None,
            report.errors,
        )
        matching = await container.pipeline.run(
            kalshi_markets,
            poly_markets,
            now=clock.now(),
            data_source=venues.data_source,
            enrich_kalshi=venues.kalshi.enrich_markets,
        )
        report.matching = matching.stats
        report.pairs = matching.pairs
        markets = {m.key: m for m in [*kalshi_markets, *poly_markets]}
        # candidate markets re-normalized with venue metadata (e.g. Kalshi series fees)
        markets.update(matching.enriched_markets)
        await repo.upsert_markets(list(markets.values()), matching.eligibility, clock.now())
        await repo.replace_pairs(run_id, matching.pairs, relationship_edges(matching.pairs))

        # ---- books ---------------------------------------------------------------------------
        selected = [p for p in matching.pairs if p.approved_for_arbitrage_calculation]
        analytic = [p for p in matching.pairs if p.relation in ANALYTIC_RELATIONS]
        if live:
            analytic = analytic[: settings.live_max_book_candidates]
            selected = selected[: settings.live_max_book_candidates]
        groups = build_groups(list(markets.values()))
        groups = [g for g in groups if len(g.markets) <= MAX_GROUP_SIZE]
        if live:
            groups = groups[:LIVE_MAX_REBALANCING_GROUPS]
        k_needed: dict[str, NormalizedMarket] = {}
        p_needed: dict[str, NormalizedMarket] = {}
        for pair in [*selected, *analytic]:
            k_needed[pair.kalshi_market_id] = markets[f"kalshi:{pair.kalshi_market_id}"]
            p_needed[pair.polymarket_market_id] = markets[f"polymarket:{pair.polymarket_market_id}"]
        for group in groups:
            target = k_needed if group.venue is Venue.KALSHI else p_needed
            for market in group.markets:
                target[market.venue_market_id] = markets.get(market.key, market)
        k_books = await _books(venues.kalshi, "kalshi", list(k_needed.values()), report.errors)
        p_books = await _books(venues.polymarket, "polymarket", list(p_needed.values()), report.errors)
        await repo.upsert_books(
            [book for snap in [*k_books.values(), *p_books.values()] for book in (snap.yes, snap.no)]
        )

        # ---- opportunities -------------------------------------------------------------------
        now = clock.now()
        trading = {h.venue: h.trading_active is not False for h in report.health}
        calculator = CrossVenueCalculator(container.arbitrage)
        opportunities: list[ArbitrageOpportunity] = []
        for pair in [*selected, *analytic]:
            k_snap = k_books.get(pair.kalshi_market_id)
            p_snap = p_books.get(pair.polymarket_market_id)
            if k_snap is None or p_snap is None:
                continue
            with log_context(pair_id=pair.id):
                opportunities.extend(
                    calculator.evaluate_pair(
                        pair,
                        markets[f"kalshi:{pair.kalshi_market_id}"],
                        markets[f"polymarket:{pair.polymarket_market_id}"],
                        k_snap,
                        p_snap,
                        now=now,
                        data_source=venues.data_source,
                        venue_trading=trading,
                    )
                )
        for group in groups:
            snaps = k_books if group.venue is Venue.KALSHI else p_books
            opportunities.extend(
                evaluate_group(
                    group, snaps, now=now, config=container.arbitrage, data_source=venues.data_source
                )
            )
        report.opportunities = opportunities
        await repo.save_opportunities(run_id, opportunities)
        for opp in opportunities:
            METRICS.inc("opportunities_recorded_total", status=opp.status.value)

        # ---- audit -------------------------------------------------------------------------------
        for pair in matching.pairs:
            await repo.add_audit(
                ts=now,
                category="pair_decision",
                message=(
                    f"{'approved' if pair.approved_for_arbitrage_calculation else 'rejected'} "
                    f"{pair.kalshi_market_id} <-> {pair.polymarket_market_id}: {pair.relation.value}"
                ),
                data_source=venues.data_source.value,
                correlation_id=run_id,
                pair_id=pair.id,
                payload={
                    "relation": pair.relation.value,
                    "confidence": str(pair.confidence),
                    "approved": pair.approved_for_arbitrage_calculation,
                    "decision_reasons": list(pair.decision_reasons),
                    "blocking_mismatches": list(pair.blocking_mismatches),
                    "semantic_explanation": pair.semantic_explanation.model_dump(mode="json"),
                },
            )
        for opp in opportunities:
            await repo.add_audit(
                ts=now,
                category="opportunity",
                message=f"{opp.strategy_type.value} {opp.status.value}: {opp.direction}",
                data_source=venues.data_source.value,
                correlation_id=run_id,
                pair_id=opp.pair_id,
                opportunity_id=opp.id,
                payload={
                    "status": opp.status.value,
                    "expected_net_profit": str(opp.expected_net_profit),
                    "max_executable_quantity": str(opp.max_executable_quantity),
                    "rejection_reason": opp.rejection_reason,
                    "assumptions": list(opp.assumptions),
                },
            )
        report.finished_at = clock.now()
        report.status = "completed" if not report.errors else "completed_with_errors"
        await repo.save_run(
            run_id,
            kind=kind,
            data_source=venues.data_source.value,
            status="completed",
            started_at=started,
            finished_at=report.finished_at,
            summary=report.summary(),
        )
        await repo.add_audit(
            ts=report.finished_at,
            category="scan",
            message=f"{kind} scan {report.status}",
            data_source=venues.data_source.value,
            correlation_id=run_id,
            payload=report.summary(),
        )
        log_event(
            logger,
            logging.INFO,
            "scan complete",
            run_id=run_id,
            status=report.status,
            opportunities=len(opportunities),
            errors=len(report.errors),
        )
    return report
