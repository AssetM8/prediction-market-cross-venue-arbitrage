"""Application configuration.

Every setting is documented in ``.env.example``. No credential is required for any
feature in this version. The only secret the application can hold is an optional LLM
API key, stored as :class:`pydantic.SecretStr` and never returned by the API or logged.

``PAPER_TRADING_ONLY`` exists so operators can see the mode explicitly, but it cannot be
turned off: setting it to ``false`` fails validation at startup because no live execution
path exists in this version.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURES_DIR = REPO_ROOT / "fixtures"


class DataMode(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class ExecutionPolicy(StrEnum):
    SIMULTANEOUS_IOC = "simultaneous_ioc"
    SEQUENTIAL = "sequential"


class LLMProvider(StrEnum):
    NONE = "none"
    ANTHROPIC = "anthropic"


class EmbeddingProviderName(StrEnum):
    NONE = "none"
    HASHING = "hashing"


class Settings(BaseSettings):
    """Runtime settings loaded from the environment (and an optional ``.env`` file)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application -----------------------------------------------------------------
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./arbitrage.db"
    paper_trading_only: bool = True
    data_mode: DataMode = DataMode.FIXTURE
    auto_scan_on_startup: bool = True
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # --- venue endpoints (read-only) ---------------------------------------------------
    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    polymarket_ws_enabled: bool = True

    # --- HTTP behaviour -----------------------------------------------------------------
    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 15.0
    http_max_attempts: int = Field(default=4, ge=1, le=8)
    http_backoff_base_seconds: float = 0.5
    http_backoff_max_seconds: float = 8.0
    kalshi_max_requests_per_second: float = Field(default=8.0, gt=0)
    polymarket_max_requests_per_second: float = Field(default=10.0, gt=0)

    # --- live scan bounds -----------------------------------------------------------------
    live_max_pages_kalshi: int = Field(default=10, ge=1)
    live_max_pages_polymarket: int = Field(default=20, ge=1)
    live_max_book_candidates: int = Field(default=40, ge=1)
    book_depth_levels: int = Field(default=0, ge=0, le=100)
    poll_interval_seconds: float = Field(default=5.0, gt=0)

    # --- matching -----------------------------------------------------------------------
    market_pair_min_confidence: Decimal = Decimal("0.90")
    candidate_min_similarity: Decimal = Decimal("0.30")
    candidate_time_window_days: int = Field(default=400, ge=1)
    max_close_time_diff_hours: int = Field(default=72, ge=0)
    max_candidates_per_market: int = Field(default=25, ge=1)

    # --- arbitrage thresholds and buffers (all in collateral dollars) --------------------
    book_stale_after_seconds: int = Field(default=30, ge=1)
    min_net_profit: Decimal = Decimal("1.00")
    min_return_on_capital: Decimal = Decimal("0.005")
    settlement_cost_buffer: Decimal = Decimal("0.0050")
    latency_risk_buffer: Decimal = Decimal("0.0025")
    capital_cost_annual_rate: Decimal = Decimal("0.05")
    min_top_of_book_depth: Decimal = Decimal("1")
    quantity_step: Decimal = Decimal("1")
    max_trade_quantity: Decimal = Decimal("5000")

    # --- fees -----------------------------------------------------------------------------
    kalshi_taker_fee_rate: Decimal = Decimal("0.07")
    kalshi_fee_rounding_quantum: Decimal = Decimal("0.0001")
    kalshi_fallback_fee_multiplier: Decimal = Decimal("2")
    polymarket_fallback_fee_rate: Decimal = Decimal("0.07")
    polymarket_fee_rounding_quantum: Decimal = Decimal("0.00001")

    # --- paper execution ------------------------------------------------------------------
    execution_policy: ExecutionPolicy = ExecutionPolicy.SEQUENTIAL
    max_unhedged_notional: Decimal = Decimal("25")
    partial_fill_fraction: Decimal = Decimal("0.4")
    adverse_price_move: Decimal = Decimal("0.03")
    max_hedge_slippage: Decimal = Decimal("0.02")
    unwind_haircut: Decimal = Decimal("0.01")
    kill_switch_max_consecutive_failures: int = Field(default=3, ge=1)
    paper_starting_cash_kalshi: Decimal = Decimal("10000")
    paper_starting_cash_polymarket: Decimal = Decimal("10000")

    # --- optional semantic components -----------------------------------------------------
    optional_llm_provider: LLMProvider = LLMProvider.NONE
    optional_llm_model: str = ""
    optional_llm_api_key: SecretStr = SecretStr("")
    optional_llm_base_url: str = "https://api.anthropic.com"
    optional_llm_timeout_seconds: float = 30.0
    optional_embeddings: EmbeddingProviderName = EmbeddingProviderName.HASHING

    @field_validator("paper_trading_only")
    @classmethod
    def _paper_only(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "PAPER_TRADING_ONLY=false is not supported: this version has no live "
                "execution path. See docs/LIVE_TRADING_GAP_ANALYSIS.md."
            )
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator(
        "min_net_profit",
        "min_return_on_capital",
        "settlement_cost_buffer",
        "latency_risk_buffer",
        "capital_cost_annual_rate",
        "max_unhedged_notional",
        "max_hedge_slippage",
        "unwind_haircut",
        "kalshi_taker_fee_rate",
        "polymarket_fallback_fee_rate",
    )
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("must be non-negative")
        return value

    @field_validator("quantity_step", "min_top_of_book_depth", "max_trade_quantity")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @field_validator("market_pair_min_confidence", "candidate_min_similarity")
    @classmethod
    def _unit_interval(cls, value: Decimal) -> Decimal:
        if not Decimal(0) <= value <= Decimal(1):
            raise ValueError("must be within [0, 1]")
        return value

    def public_view(self) -> dict[str, Any]:
        """Settings that are safe to expose through ``GET /api/config/public``.

        This is an allow-list: new settings are private until added here.
        """
        return {
            "app_env": self.app_env,
            "paper_trading_only": self.paper_trading_only,
            "data_mode": self.data_mode.value,
            "kalshi_base_url": self.kalshi_base_url,
            "polymarket_gamma_url": self.polymarket_gamma_url,
            "polymarket_clob_url": self.polymarket_clob_url,
            "polymarket_ws_enabled": self.polymarket_ws_enabled,
            "book_stale_after_seconds": self.book_stale_after_seconds,
            "market_pair_min_confidence": str(self.market_pair_min_confidence),
            "min_net_profit": str(self.min_net_profit),
            "min_return_on_capital": str(self.min_return_on_capital),
            "settlement_cost_buffer": str(self.settlement_cost_buffer),
            "latency_risk_buffer": str(self.latency_risk_buffer),
            "capital_cost_annual_rate": str(self.capital_cost_annual_rate),
            "min_top_of_book_depth": str(self.min_top_of_book_depth),
            "quantity_step": str(self.quantity_step),
            "max_trade_quantity": str(self.max_trade_quantity),
            "max_unhedged_notional": str(self.max_unhedged_notional),
            "max_hedge_slippage": str(self.max_hedge_slippage),
            "kill_switch_max_consecutive_failures": self.kill_switch_max_consecutive_failures,
            "execution_policy": self.execution_policy.value,
            "kalshi_taker_fee_rate": str(self.kalshi_taker_fee_rate),
            "polymarket_fallback_fee_rate": str(self.polymarket_fallback_fee_rate),
            "optional_llm_provider": self.optional_llm_provider.value,
            "optional_llm_configured": bool(self.optional_llm_api_key.get_secret_value()),
            "optional_embeddings": self.optional_embeddings.value,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (tests construct :class:`Settings` directly)."""
    return Settings()
