"""Command-line interface: ``python -m app.cli <command>`` (run from ``backend/``).

Every command is read-only toward the venues. ``paper-execute`` and ``demo`` mutate only
the local paper ledger. There is no command, flag or setting that places a real order.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn

from app.core.config import DataMode, Settings, get_settings
from app.core.logging import configure_logging
from app.domain.enums import ExecutionScenario, OpportunityStatus, StrategyType
from app.domain.models import ArbitrageOpportunity, MarketPair
from app.matching.eligibility import check_eligibility
from app.services.container import AppContainer
from app.services.demo import DemoResult, run_demo
from app.services.paper import ExecutionRequest, OpportunityNotFoundError, PaperService
from app.services.scan import ScanReport, run_scan

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

BANNER = "PAPER TRADING ONLY - read-only market data, simulated execution, no real orders"
EXIT_VENUE_UNAVAILABLE = 3


def _settings(verbose: bool) -> Settings:
    settings = get_settings()
    configure_logging("INFO" if verbose else "WARNING", json_output=settings.log_json)
    return settings


def _mode(live: bool, fixture: bool, settings: Settings) -> DataMode:
    if live and fixture:
        raise typer.BadParameter("choose at most one of --live / --fixture")
    if live:
        return DataMode.LIVE
    if fixture:
        return DataMode.FIXTURE
    return settings.data_mode


def _echo(text: str = "") -> None:
    typer.echo(text)


def _money(value: Decimal) -> str:
    return f"{value:,.4f}"


def _print_pairs(pairs: list[MarketPair], *, limit: int = 40) -> None:
    _echo(f"{'relation':22} {'ok':3} {'conf':5} kalshi <-> polymarket")
    for pair in sorted(
        pairs, key=lambda p: (not p.approved_for_arbitrage_calculation, p.relation.value, p.id)
    )[:limit]:
        flag = "yes" if pair.approved_for_arbitrage_calculation else "no"
        _echo(
            f"{pair.relation.value:22} {flag:3} {pair.confidence!s:5} {pair.kalshi_market_id} <-> {pair.polymarket_market_id}"
        )
        if not pair.approved_for_arbitrage_calculation and pair.blocking_mismatches:
            _echo(f"{'':32}blocked by: {pair.blocking_mismatches[0][:110]}")


def _print_opportunities(opps: list[ArbitrageOpportunity]) -> None:
    for opp in sorted(opps, key=lambda o: (o.status.value != "validated", -o.expected_net_profit, o.id)):
        _echo(
            f"- [{opp.status.value}] {opp.strategy_type.value}: {opp.direction}\n"
            f"    id={opp.id} qty={opp.max_executable_quantity} gross_cost={_money(opp.gross_cost)} "
            f"fees={_money(opp.explicit_fees)} buffers={_money(opp.safety_buffer)} "
            f"net={_money(opp.expected_net_profit)} roc={opp.return_on_capital}"
        )
        if opp.rejection_reason:
            _echo(f"    reason: {opp.rejection_reason[:160]}")


def _print_scan(report: ScanReport) -> None:
    summary = report.summary()
    _echo(f"run {report.run_id} ({report.kind}, data source: {report.data_source.value})")
    for venue, info in summary["venues"].items():
        state = "reachable" if info["reachable"] else f"UNREACHABLE ({info['error']})"
        _echo(f"  venue {venue}: {state}")
    stats = report.matching
    _echo(
        f"  markets: kalshi={stats.get('kalshi_markets', 0)} polymarket={stats.get('polymarket_markets', 0)} "
        f"eligible={stats.get('eligible_kalshi', 0)}/{stats.get('eligible_polymarket', 0)}"
    )
    _echo(
        f"  candidate reduction: {stats.get('possible_pairs', 0)} possible pairs -> "
        f"{stats.get('compared_after_blocking', 0)} compared after blocking -> "
        f"{stats.get('candidates', 0)} candidates -> {stats.get('approved_pairs', 0)} approved"
    )
    for error in report.errors:
        _echo(f"  ! {error}")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@app.command()
def health(
    live: Annotated[bool, typer.Option("--live", help="check live venue APIs")] = False,
    fixture: Annotated[bool, typer.Option("--fixture", help="check fixture venues")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Check database connectivity and venue reachability (read-only)."""
    settings = _settings(verbose)

    async def main() -> int:
        container = AppContainer(settings, data_mode=_mode(live, fixture, settings))
        await container.start()
        venues = container.venues()
        try:
            db_ok = await container.db.ping()
            results = [await venues.kalshi.healthcheck(), await venues.polymarket.healthcheck()]
        finally:
            await venues.aclose()
            await container.aclose()
        payload = {
            "paper_trading_only": True,
            "data_mode": container.data_mode.value,
            "database": "ok" if db_ok else "error",
            "venues": [r.model_dump(mode="json") for r in results],
        }
        _echo(json.dumps(payload, indent=2))
        return 0 if all(r.reachable for r in results) else EXIT_VENUE_UNAVAILABLE

    raise typer.Exit(_run(main()))


@app.command("sync-markets")
def sync_markets(
    live: Annotated[bool, typer.Option("--live")] = False,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Download active markets from both venues and store them (no matching)."""
    settings = _settings(verbose)

    async def main() -> int:
        container = AppContainer(settings, data_mode=_mode(live, fixture, settings))
        await container.start()
        venues = container.venues()
        try:
            live_mode = container.data_mode is DataMode.LIVE
            k = await venues.kalshi.list_active_markets(
                max_pages=settings.live_max_pages_kalshi if live_mode else None
            )
            p = await venues.polymarket.list_active_markets(
                max_pages=settings.live_max_pages_polymarket if live_mode else None
            )
            now = container.clock.now()
            await container.repo.upsert_markets([*k, *p], [check_eligibility(m, now) for m in [*k, *p]], now)
        finally:
            await venues.aclose()
            await container.aclose()
        _echo(f"stored {len(k)} Kalshi and {len(p)} Polymarket markets ({container.data_source.value} data)")
        return 0

    raise typer.Exit(_run(main()))


@app.command("match-markets")
def match_markets(
    live: Annotated[bool, typer.Option("--live")] = False,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the staged matching pipeline and print every evaluated pair with its decision."""
    settings = _settings(verbose)

    async def main() -> int:
        container = AppContainer(settings, data_mode=_mode(live, fixture, settings))
        await container.start()
        venues = container.venues()
        try:
            live_mode = container.data_mode is DataMode.LIVE
            k = await venues.kalshi.list_active_markets(
                max_pages=settings.live_max_pages_kalshi if live_mode else None
            )
            p = await venues.polymarket.list_active_markets(
                max_pages=settings.live_max_pages_polymarket if live_mode else None
            )
            report = await container.pipeline.run(
                k,
                p,
                now=container.clock.now(),
                data_source=venues.data_source,
                enrich_kalshi=venues.kalshi.enrich_markets,
            )
        finally:
            await venues.aclose()
            await container.aclose()
        _echo(BANNER)
        _echo(json.dumps(report.stats, indent=2, default=str))
        _print_pairs(report.pairs)
        if not report.approved:
            _echo("No pair passed conservative approval.")
        return 0

    raise typer.Exit(_run(main()))


@app.command()
def scan(
    live: Annotated[bool, typer.Option("--live", help="scan live public venue data (read-only)")] = False,
    fixture: Annotated[bool, typer.Option("--fixture", help="scan the checked-in fixture set")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Full read-only scan: markets, matching, books, net opportunities. Never trades."""
    settings = _settings(verbose)

    async def main() -> int:
        container = AppContainer(settings, data_mode=_mode(live, fixture, settings))
        await container.start()
        venues = container.venues()
        try:
            report = await run_scan(container, venues, kind="scan")
        finally:
            await venues.aclose()
            await container.aclose()
        _echo(BANNER)
        _print_scan(report)
        executable = [o for o in report.opportunities if o.status is OpportunityStatus.VALIDATED]
        cross = [o for o in report.opportunities if o.strategy_type is StrategyType.CROSS_VENUE_BINARY]
        approved = [p for p in report.pairs if p.approved_for_arbitrage_calculation]
        if report.opportunities:
            _print_opportunities(report.opportunities)
        unreachable = [h for h in report.health if not h.reachable]
        if unreachable and not report.pairs:
            names = ", ".join(h.venue.value for h in unreachable)
            _echo(
                f"Result: could not evaluate - venue data unavailable ({names}); see errors above. "
                "No opportunity reported."
            )
        elif not approved:
            _echo(
                "Result: no market pair passed conservative equivalence validation. No opportunity reported."
            )
        elif not executable:
            _echo(
                f"Result: {len(approved)} approved pair(s), {len(cross)} priced direction(s), "
                "none profitable after fees, buffers and gating. No opportunity reported."
            )
        else:
            _echo(f"Result: {len(executable)} validated paper opportunit(ies). Nothing was traded.")
        return EXIT_VENUE_UNAVAILABLE if unreachable and not report.pairs else 0

    raise typer.Exit(_run(main()))


def _print_demo(result: DemoResult) -> None:
    _echo("=" * 88)
    _echo(BANNER)
    _echo("Deterministic fixture demo (synthetic data, simulated clock)")
    _echo("=" * 88)
    _print_scan(result.scan)
    _echo("\nMarket pairs:")
    _print_pairs(result.scan.pairs, limit=60)
    _echo("\nOpportunities:")
    _print_opportunities(result.scan.opportunities)
    _echo("\nPaper executions:")
    for execution in result.executions:
        _echo(
            f"- {execution.outcome.value} (scenario {execution.scenario.value}): hedged {execution.hedged_quantity}"
            f" of {execution.requested_quantity}, residual {execution.residual_quantity}, fees "
            f"{_money(execution.total_fees)}, locked-in {_money(execution.expected_locked_in_pnl)}, realized "
            f"{_money(execution.realized_pnl)}\n    {execution.reason}"
        )
    if result.portfolio:
        pf = result.portfolio
        cash = ", ".join(f"{c.venue.value}={_money(c.cash)}" for c in pf.cash)
        _echo(
            f"\nPortfolio: cash [{cash}] locked-in={_money(pf.locked_in_pnl)} realized={_money(pf.realized_pnl)} "
            f"unrealized(bid marks)={_money(pf.unrealized_pnl)} residual_notional={_money(pf.residual_exposure_notional)}"
        )
    _echo("\nAcceptance checks:")
    for name, ok in result.checks.items():
        _echo(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    _echo("\nResults are stored in the database; start the API (make dev) to browse them in the dashboard.")


@app.command()
def demo(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """Run the deterministic fixture demo end to end (no network, no credentials)."""
    settings = _settings(verbose)
    result: DemoResult = _run(run_demo(settings))
    _print_demo(result)
    raise typer.Exit(0 if all(result.checks.values()) else 1)


@app.command("paper-execute")
def paper_execute(
    opportunity_id: Annotated[str, typer.Option("--opportunity-id", help="opportunity to simulate")],
    quantity: Annotated[
        str | None, typer.Option("--quantity", help="contracts per leg (default: max)")
    ] = None,
    scenario: Annotated[ExecutionScenario, typer.Option("--scenario")] = ExecutionScenario.NORMAL,
    idempotency_key: Annotated[str | None, typer.Option("--idempotency-key")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Simulate a two-leg paper execution for a stored opportunity (local state only)."""
    settings = _settings(verbose)
    key = idempotency_key or f"cli-{opportunity_id}-{scenario.value}-{quantity or 'max'}"

    async def main() -> int:
        container = AppContainer(settings)
        await container.start()
        try:
            response = await PaperService(container).execute(
                ExecutionRequest(
                    opportunity_id=opportunity_id,
                    idempotency_key=key,
                    quantity=Decimal(quantity) if quantity else None,
                    scenario=scenario,
                )
            )
        except OpportunityNotFoundError:
            _echo(f"opportunity {opportunity_id} not found (run a scan or the demo first)")
            return 1
        finally:
            await container.aclose()
        _echo(BANNER)
        _echo(json.dumps(response.execution.model_dump(mode="json"), indent=2))
        return 0

    raise typer.Exit(_run(main()))


@app.command("export-audit")
def export_audit(
    output: Annotated[Path, typer.Option("--output", help="JSON Lines file to write")],
    limit: Annotated[int, typer.Option("--limit")] = 10000,
) -> None:
    """Export the audit log as JSON Lines."""
    settings = _settings(False)

    async def main() -> int:
        container = AppContainer(settings)
        await container.start()
        try:
            rows = await container.repo.list_audit(limit=limit)
        finally:
            await container.aclose()
        with output.open("w", encoding="utf-8") as handle:
            for row in reversed(rows):
                handle.write(
                    json.dumps(
                        {
                            "id": row.id,
                            "ts": row.ts.isoformat(),
                            "category": row.category,
                            "message": row.message,
                            "data_source": row.data_source,
                            "correlation_id": row.correlation_id,
                            "pair_id": row.pair_id,
                            "opportunity_id": row.opportunity_id,
                            "execution_id": row.execution_id,
                            "paper_order_id": row.paper_order_id,
                            "payload": row.payload,
                        }
                    )
                    + "\n"
                )
        _echo(f"wrote {len(rows)} audit events to {output}")
        return 0

    raise typer.Exit(_run(main()))


@app.command("reset-db")
def reset_db() -> None:
    """Drop and recreate all tables (paper state included)."""
    settings = _settings(False)

    async def main() -> None:
        container = AppContainer(settings)
        try:
            await container.db.reset()
        finally:
            await container.aclose()

    _run(main())
    _echo("database reset")


@app.command("kill-switch-reset")
def kill_switch_reset() -> None:
    """Reset the paper execution kill switch."""
    settings = _settings(False)

    async def main() -> None:
        container = AppContainer(settings)
        await container.start()
        try:
            await PaperService(container).reset_kill_switch()
        finally:
            await container.aclose()

    _run(main())
    _echo("kill switch reset")


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """Start the API with uvicorn."""
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app(prog_name="python -m app.cli")
