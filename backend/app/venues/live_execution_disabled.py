"""Disabled live-execution adapter.

This module exists only to make the absence of live trading explicit. The class below
satisfies :class:`app.domain.interfaces.LiveExecutionVenue` structurally, but every method
raises :class:`NotImplementedError`. No service, API route, CLI command or configuration
value constructs it; ``tests/security/test_no_live_trading.py`` fails the build if that
ever changes.

It intentionally contains no endpoint paths, signing code or credential handling.
"""

from __future__ import annotations

from typing import NoReturn

LIVE_TRADING_DISABLED_MESSAGE = (
    "Live order placement is not implemented in this version. The project is read-only "
    "and paper-trading only. See docs/LIVE_TRADING_GAP_ANALYSIS.md."
)


class DisabledLiveExecutionVenue:
    """Structural stand-in for a future live adapter; always refuses."""

    async def submit_order(self, *args: object, **kwargs: object) -> NoReturn:
        raise NotImplementedError(LIVE_TRADING_DISABLED_MESSAGE)

    async def cancel_order(self, *args: object, **kwargs: object) -> NoReturn:
        raise NotImplementedError(LIVE_TRADING_DISABLED_MESSAGE)
