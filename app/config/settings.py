from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration with fail-safe trading defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CTCC V2"
    app_version: str = "1.5.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = Field(
        default="postgresql+asyncpg://ctcc:ctcc_dev_password@postgres:5432/ctcc"
    )
    redis_url: str = "redis://redis:6379/0"
    api_token: SecretStr = SecretStr("")

    readiness_require_redis: bool = True
    readiness_require_database: bool = True

    trading_mode: Literal["analysis_only", "paper", "okx_demo", "live"] = "analysis_only"
    auto_trade: bool = False
    live_trading: bool = False

    okx_rest_base_url: str = "https://openapi.okx.com"
    okx_public_timeout_seconds: float = 10.0
    okx_public_max_retries: int = 2

    okx_ws_enabled: bool = False
    okx_ws_public_url: str = "wss://ws.okx.com:8443/ws/v5/public"
    okx_ws_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    okx_ws_connect_timeout_seconds: float = Field(default=15, gt=1, le=120)
    okx_ws_receive_timeout_seconds: float = Field(default=25, gt=5, le=120)
    okx_ws_ping_timeout_seconds: float = Field(default=10, gt=1, le=60)
    okx_ws_reconnect_initial_seconds: float = Field(default=1, gt=0, le=30)
    okx_ws_reconnect_max_seconds: float = Field(default=30, gt=1, le=300)
    okx_ws_max_message_size: int = Field(default=2_000_000, ge=65_536, le=16_000_000)
    paper_auto_ticks: bool = True

    strategy_min_score: int = Field(default=72, ge=0, le=100)
    strategy_min_risk_reward: float = Field(default=1.8, gt=0, le=10)

    risk_per_trade_pct: float = Field(default=0.005, gt=0, le=0.05)
    max_daily_loss_pct: float = Field(default=0.02, gt=0, le=0.25)
    max_weekly_loss_pct: float = Field(default=0.05, gt=0, le=0.50)
    max_drawdown_pct: float = Field(default=0.10, gt=0, le=0.80)
    max_consecutive_losses: int = Field(default=3, ge=1, le=20)
    max_open_positions: int = Field(default=2, ge=1, le=20)
    max_same_direction_positions: int = Field(default=1, ge=1, le=20)
    max_correlated_positions: int = Field(default=1, ge=1, le=20)
    order_size_cap_usdt: float = Field(default=5000, gt=0)

    paper_starting_balance: float = Field(default=10000, gt=0)
    paper_taker_fee_rate: float = Field(default=0.0005, ge=0, le=0.01)
    paper_maker_fee_rate: float = Field(default=0.0002, ge=0, le=0.01)
    paper_slippage_bps: float = Field(default=2, ge=0, le=100)

    # Auto-paper orchestrator. This is never exchange execution.
    paper_auto_execution: bool = False
    paper_scan_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    paper_scan_interval_seconds: int = Field(default=300, ge=30, le=86_400)
    paper_scan_initial_delay_seconds: int = Field(default=10, ge=0, le=600)
    paper_scan_candle_limit: int = Field(default=250, ge=200, le=300)
    paper_scan_max_snapshot_age_seconds: int = Field(default=30, ge=5, le=300)
    paper_scan_max_entry_drift_bps: float = Field(default=50, ge=1, le=1000)
    paper_scan_cooldown_seconds: int = Field(default=900, ge=0, le=86_400)
    paper_scan_history_limit: int = Field(default=100, ge=10, le=1000)

    # Persistence and restart recovery.
    paper_persistence_enabled: bool = True
    paper_persist_mark_interval_seconds: int = Field(default=30, ge=5, le=3600)
    paper_recovery_history_limit: int = Field(default=100, ge=10, le=1000)

    # OKX Live credentials are isolated from Demo. Runtime remains hard-blocked.
    okx_live_api_key: SecretStr = SecretStr("")
    okx_live_api_secret: SecretStr = SecretStr("")
    okx_live_api_passphrase: SecretStr = SecretStr("")

    # OKX Demo REST execution. Credentials are never returned by the API.
    okx_demo_enabled: bool = False
    okx_demo_allow_order_writes: bool = False
    okx_demo_rest_base_url: str = "https://openapi.okx.com"
    okx_demo_api_key: SecretStr = SecretStr("")
    okx_demo_api_secret: SecretStr = SecretStr("")
    okx_demo_api_passphrase: SecretStr = SecretStr("")
    okx_demo_timeout_seconds: float = Field(default=10, gt=1, le=60)
    okx_demo_read_max_retries: int = Field(default=2, ge=0, le=5)
    okx_demo_allowed_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    okx_demo_max_order_size_contracts: Decimal = Field(default=Decimal("1"), gt=0, le=1000)
    okx_demo_max_open_positions: int = Field(default=1, ge=1, le=10)
    okx_demo_max_leverage: int = Field(default=3, ge=1, le=20)
    okx_demo_require_protection: bool = True
    okx_demo_auto_reconcile_on_start: bool = False
    okx_demo_order_detail_poll_attempts: int = Field(default=3, ge=1, le=10)
    okx_demo_order_detail_poll_delay_seconds: float = Field(default=0.4, ge=0, le=5)

    # Explicitly armed OKX Demo automation. Never used for real trading.
    okx_demo_auto_execution: bool = False
    okx_demo_scan_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    okx_demo_scan_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    okx_demo_scan_initial_delay_seconds: int = Field(default=10, ge=0, le=600)
    okx_demo_scan_candle_limit: int = Field(default=250, ge=200, le=300)
    okx_demo_scan_max_snapshot_age_seconds: int = Field(default=30, ge=5, le=300)
    okx_demo_scan_max_entry_drift_bps: Decimal = Field(default=Decimal("30"), ge=1, le=500)
    okx_demo_trade_cooldown_seconds: int = Field(default=1800, ge=0, le=86_400)
    okx_demo_max_trades_per_day: int = Field(default=3, ge=1, le=20)
    okx_demo_daily_loss_limit_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.10"))
    okx_demo_automation_max_consecutive_losses: int = Field(default=2, ge=1, le=10)
    okx_demo_automation_leverage: int = Field(default=1, ge=1, le=5)
    okx_demo_automation_history_limit: int = Field(default=100, ge=10, le=1000)

    # Controlled Demo execution soak and observability. Execute stays disabled by default.
    okx_demo_observability_enabled: bool = True
    okx_demo_observability_heartbeat_seconds: int = Field(default=15, ge=1, le=300)
    okx_demo_observability_stale_after_seconds: int = Field(default=90, ge=10, le=3600)
    okx_demo_observability_error_threshold: int = Field(default=3, ge=1, le=20)
    okx_demo_observability_event_limit: int = Field(default=500, ge=50, le=5000)
    okx_demo_observability_metrics_run_limit: int = Field(default=5000, ge=100, le=50_000)
    okx_demo_soak_enabled: bool = True
    okx_demo_soak_allow_execute: bool = False
    okx_demo_soak_default_duration_minutes: int = Field(default=60, ge=1, le=10_080)
    okx_demo_soak_max_duration_minutes: int = Field(default=1440, ge=1, le=10_080)
    okx_demo_soak_interval_seconds: int = Field(default=300, ge=1, le=86_400)
    okx_demo_soak_max_runs: int = Field(default=288, ge=1, le=10_000)

    # Controlled execute-soak guardrails. These never enable writes or arm automatically.
    okx_demo_execution_soak_max_submissions: int = Field(default=1, ge=1, le=10)
    okx_demo_execution_soak_loss_limit_pct: Decimal = Field(
        default=Decimal("0.0025"), gt=0, le=Decimal("0.02")
    )
    okx_demo_execution_soak_require_flat_start: bool = True
    okx_demo_execution_soak_require_protection: bool = True
    okx_demo_execution_soak_auto_disarm: bool = True
    okx_demo_execution_soak_reconcile_attempts: int = Field(default=10, ge=1, le=20)
    okx_demo_execution_soak_reconcile_delay_seconds: float = Field(
        default=1.0, ge=0, le=10
    )

    # v1.5 Demo reliability and performance validation. Analytics are read-only.
    okx_demo_performance_window_days: int = Field(default=30, ge=1, le=365)
    okx_demo_performance_snapshot_retention_days: int = Field(default=90, ge=7, le=730)
    okx_demo_performance_snapshot_query_limit: int = Field(default=50_000, ge=100, le=200_000)
    okx_demo_performance_order_query_limit: int = Field(default=10_000, ge=100, le=100_000)
    okx_demo_performance_min_active_days: int = Field(default=7, ge=1, le=365)
    okx_demo_performance_min_realized_trades: int = Field(default=20, ge=1, le=10_000)
    okx_demo_performance_max_average_slippage_bps: Decimal = Field(
        default=Decimal("10"), ge=0, le=Decimal("500")
    )
    okx_demo_performance_min_profit_factor: Decimal = Field(
        default=Decimal("1.0"), ge=0, le=Decimal("20")
    )
    okx_demo_performance_max_drawdown_pct: Decimal = Field(
        default=Decimal("0.02"), gt=0, le=Decimal("0.50")
    )
    okx_demo_strategy_review_min_trades: int = Field(default=5, ge=1, le=1000)
    okx_demo_strategy_review_min_win_rate: Decimal = Field(
        default=Decimal("0.35"), ge=0, le=Decimal("1")
    )
    okx_demo_strategy_auto_disable: bool = False

    @property
    def okx_ws_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_ws_symbols.split(",") if item.strip()]

    @property
    def paper_scan_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.paper_scan_symbols.split(",") if item.strip()]

    @property
    def okx_demo_allowed_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_demo_allowed_symbols.split(",") if item.strip()]

    @property
    def okx_demo_scan_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_demo_scan_symbols.split(",") if item.strip()]

    @property
    def api_token_value(self) -> str:
        return self.api_token.get_secret_value()

    @property
    def api_token_is_safe(self) -> bool:
        return len(self.api_token_value.strip()) >= 32

    @property
    def okx_live_credentials_configured(self) -> bool:
        return all(
            len(secret.get_secret_value().strip()) >= 3
            for secret in (
                self.okx_live_api_key,
                self.okx_live_api_secret,
                self.okx_live_api_passphrase,
            )
        )

    @property
    def okx_demo_credentials_configured(self) -> bool:
        return all(
            len(secret.get_secret_value().strip()) >= 3
            for secret in (
                self.okx_demo_api_key,
                self.okx_demo_api_secret,
                self.okx_demo_api_passphrase,
            )
        )

    @model_validator(mode="after")
    def enforce_v15_safety(self) -> "Settings":
        if self.live_trading:
            raise ValueError("LIVE_TRADING must remain false in CTCC V2 v1.5")
        if self.auto_trade:
            raise ValueError("AUTO_TRADE must remain false in CTCC V2 v1.5")
        if self.trading_mode == "live":
            raise ValueError("live mode is unavailable in CTCC V2 v1.5")

        if self.paper_auto_execution:
            if self.trading_mode != "paper":
                raise ValueError("PAPER_AUTO_EXECUTION requires TRADING_MODE=paper")
            if not self.okx_ws_enabled:
                raise ValueError("PAPER_AUTO_EXECUTION requires OKX_WS_ENABLED=true")
            if not self.paper_auto_ticks:
                raise ValueError("PAPER_AUTO_EXECUTION requires PAPER_AUTO_TICKS=true")
            if not self.paper_persistence_enabled:
                raise ValueError("PAPER_AUTO_EXECUTION requires PAPER_PERSISTENCE_ENABLED=true")

        parsed = urlparse(self.okx_demo_rest_base_url)
        if parsed.scheme != "https" or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("OKX_DEMO_REST_BASE_URL must be an HTTPS origin without a path")
        if parsed.hostname not in {"openapi.okx.com", "www.okx.com", "us.okx.com"}:
            raise ValueError("OKX_DEMO_REST_BASE_URL must use an approved OKX API host")

        if self.okx_demo_allow_order_writes:
            if not self.okx_demo_enabled:
                raise ValueError("OKX_DEMO_ALLOW_ORDER_WRITES requires OKX_DEMO_ENABLED=true")
            if self.trading_mode != "okx_demo":
                raise ValueError("OKX_DEMO_ALLOW_ORDER_WRITES requires TRADING_MODE=okx_demo")
            if not self.okx_demo_credentials_configured:
                raise ValueError("OKX Demo credentials are required before enabling order writes")
            if self.paper_auto_execution:
                raise ValueError("PAPER_AUTO_EXECUTION must be false when OKX Demo writes are enabled")

        if self.okx_demo_auto_reconcile_on_start:
            if not self.okx_demo_enabled or not self.okx_demo_credentials_configured:
                raise ValueError(
                    "OKX_DEMO_AUTO_RECONCILE_ON_START requires enabled Demo credentials"
                )

        if self.okx_demo_auto_execution:
            if self.trading_mode != "okx_demo":
                raise ValueError("OKX_DEMO_AUTO_EXECUTION requires TRADING_MODE=okx_demo")
            if not self.okx_demo_enabled or not self.okx_demo_allow_order_writes:
                raise ValueError(
                    "OKX_DEMO_AUTO_EXECUTION requires enabled Demo order writes"
                )
            if not self.okx_demo_credentials_configured:
                raise ValueError("OKX Demo credentials are required for Demo automation")
            if not self.okx_ws_enabled:
                raise ValueError("OKX_DEMO_AUTO_EXECUTION requires OKX_WS_ENABLED=true")
            if self.paper_auto_execution:
                raise ValueError(
                    "PAPER_AUTO_EXECUTION must be false when Demo automation is enabled"
                )
            if self.okx_demo_automation_leverage > self.okx_demo_max_leverage:
                raise ValueError(
                    "OKX_DEMO_AUTOMATION_LEVERAGE cannot exceed OKX_DEMO_MAX_LEVERAGE"
                )
            if not set(self.okx_demo_scan_symbol_list).issubset(
                set(self.okx_demo_allowed_symbol_list)
            ):
                raise ValueError(
                    "OKX_DEMO_SCAN_SYMBOLS must be a subset of OKX_DEMO_ALLOWED_SYMBOLS"
                )


        if self.okx_demo_soak_default_duration_minutes > self.okx_demo_soak_max_duration_minutes:
            raise ValueError(
                "OKX_DEMO_SOAK_DEFAULT_DURATION_MINUTES cannot exceed "
                "OKX_DEMO_SOAK_MAX_DURATION_MINUTES"
            )
        if self.okx_demo_execution_soak_max_submissions > self.okx_demo_max_trades_per_day:
            raise ValueError(
                "OKX_DEMO_EXECUTION_SOAK_MAX_SUBMISSIONS cannot exceed "
                "OKX_DEMO_MAX_TRADES_PER_DAY"
            )
        if self.okx_demo_execution_soak_loss_limit_pct > self.okx_demo_daily_loss_limit_pct:
            raise ValueError(
                "OKX_DEMO_EXECUTION_SOAK_LOSS_LIMIT_PCT cannot exceed "
                "OKX_DEMO_DAILY_LOSS_LIMIT_PCT"
            )

        if self.okx_demo_performance_window_days > self.okx_demo_performance_snapshot_retention_days:
            raise ValueError(
                "OKX_DEMO_PERFORMANCE_WINDOW_DAYS cannot exceed "
                "OKX_DEMO_PERFORMANCE_SNAPSHOT_RETENTION_DAYS"
            )
        if self.okx_demo_performance_min_active_days > self.okx_demo_performance_window_days:
            raise ValueError(
                "OKX_DEMO_PERFORMANCE_MIN_ACTIVE_DAYS cannot exceed "
                "OKX_DEMO_PERFORMANCE_WINDOW_DAYS"
            )
        if self.okx_demo_strategy_auto_disable:
            raise ValueError(
                "OKX_DEMO_STRATEGY_AUTO_DISABLE must remain false; strategy controls are operator-only"
            )

        if self.okx_demo_soak_allow_execute:
            if not self.okx_demo_soak_enabled:
                raise ValueError("OKX_DEMO_SOAK_ALLOW_EXECUTE requires OKX_DEMO_SOAK_ENABLED=true")
            if not self.okx_demo_auto_execution:
                raise ValueError(
                    "OKX_DEMO_SOAK_ALLOW_EXECUTE requires OKX_DEMO_AUTO_EXECUTION=true"
                )
            if self.okx_demo_soak_interval_seconds < 60:
                raise ValueError(
                    "OKX_DEMO_SOAK_INTERVAL_SECONDS must be at least 60 when execute soak is allowed"
                )
            if not self.okx_demo_execution_soak_require_flat_start:
                raise ValueError(
                    "OKX_DEMO_EXECUTION_SOAK_REQUIRE_FLAT_START must remain true "
                    "when execute soak is allowed"
                )
            if not self.okx_demo_execution_soak_require_protection:
                raise ValueError(
                    "OKX_DEMO_EXECUTION_SOAK_REQUIRE_PROTECTION must remain true "
                    "when execute soak is allowed"
                )
            if not self.okx_demo_execution_soak_auto_disarm:
                raise ValueError(
                    "OKX_DEMO_EXECUTION_SOAK_AUTO_DISARM must remain true "
                    "when execute soak is allowed"
                )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
