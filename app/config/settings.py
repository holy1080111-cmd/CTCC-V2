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
    app_version: str = "1.6.8"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000
    web_concurrency: int = Field(default=1, ge=1, le=8)

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

    # OKX Live production boundary. Read access can be enabled independently;
    # every write and automation switch remains disabled by default.
    okx_live_enabled: bool = False
    okx_live_allow_order_writes: bool = False
    okx_live_rest_base_url: str = "https://openapi.okx.com"
    okx_live_api_key: SecretStr = SecretStr("")
    okx_live_api_secret: SecretStr = SecretStr("")
    okx_live_api_passphrase: SecretStr = SecretStr("")
    okx_live_timeout_seconds: float = Field(default=10, gt=1, le=60)
    okx_live_read_max_retries: int = Field(default=2, ge=0, le=5)
    okx_live_allowed_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    okx_live_max_order_size_contracts: Decimal = Field(
        default=Decimal("1"), gt=0, le=Decimal("10")
    )
    okx_live_max_notional_usdt: Decimal = Field(
        default=Decimal("1000"), gt=0, le=Decimal("10000")
    )
    okx_live_max_open_positions: int = Field(default=1, ge=1, le=2)
    okx_live_max_leverage: int = Field(default=1, ge=1, le=3)
    okx_live_require_protection: bool = True
    okx_live_require_ip_bound_key: bool = True
    okx_live_forbid_withdraw_permission: bool = True
    okx_live_auto_reconcile_on_start: bool = False
    okx_live_order_detail_poll_attempts: int = Field(default=5, ge=1, le=10)
    okx_live_order_detail_poll_delay_seconds: float = Field(default=0.5, ge=0, le=5)
    okx_live_order_expiry_milliseconds: int = Field(
        default=5000, ge=1000, le=10000
    )

    # An arm is process-local, short lived, flat-start, and one-submission only.
    okx_live_arm_ttl_seconds: int = Field(default=300, ge=60, le=900)
    okx_live_max_submissions_per_arm: int = Field(default=1, ge=1, le=1)
    okx_live_session_loss_limit_pct: Decimal = Field(
        default=Decimal("0.0025"), gt=0, le=Decimal("0.01")
    )
    okx_live_cancel_all_after_seconds: int = Field(default=30, ge=10, le=120)
    okx_live_order_tag: str = Field(
        default="CTCCV168",
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9]+$",
    )
    okx_live_require_flat_start: bool = True
    okx_live_auto_disarm: bool = True

    # Explicitly armed production automation. It never restores an arm after
    # restart and is capped at one protected order per arm.
    okx_live_auto_execution: bool = False
    okx_live_scan_symbols: str = "BTC-USDT-SWAP,ETH-USDT-SWAP"
    okx_live_scan_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    okx_live_scan_initial_delay_seconds: int = Field(default=10, ge=0, le=600)
    okx_live_scan_candle_limit: int = Field(default=250, ge=200, le=300)
    okx_live_scan_max_snapshot_age_seconds: int = Field(default=30, ge=5, le=300)
    okx_live_scan_max_entry_drift_bps: Decimal = Field(
        default=Decimal("20"), ge=1, le=Decimal("100")
    )
    okx_live_automation_leverage: int = Field(default=1, ge=1, le=3)

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
    # Optional continuous Demo session. It removes the daily-loss, daily
    # trade-count, consecutive-loss, and post-close cooldown gates. Protected
    # stops, weekly-loss/drawdown, portfolio risk, capital buckets, duplicate
    # suppression, and execution-authority gates remain mandatory. Disabled by
    # default.
    okx_demo_continuous_session_enabled: bool = False
    okx_demo_trade_reconcile_grace_seconds: int = Field(default=30, ge=5, le=300)
    okx_demo_max_trades_per_day: int = Field(default=3, ge=1, le=20)
    okx_demo_daily_loss_limit_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.10"))
    okx_demo_automation_max_consecutive_losses: int = Field(default=3, ge=1, le=10)
    okx_demo_automation_leverage: int = Field(default=1, ge=1, le=5)
    okx_demo_automation_history_limit: int = Field(default=100, ge=10, le=1000)

    # Score-tiered Demo portfolio sizing plus the downward-only shared
    # mathematical gate. It remains disabled by default and never changes the
    # isolated OKX Live production boundary.
    okx_demo_score_risk_enabled: bool = False
    okx_demo_score_medium_min: int = Field(default=80, ge=1, le=99)
    okx_demo_score_high_min: int = Field(default=90, ge=2, le=100)
    okx_demo_score_low_risk_pct: Decimal = Field(
        default=Decimal("0.005"), gt=0, le=Decimal("0.02")
    )
    okx_demo_score_medium_risk_pct: Decimal = Field(
        default=Decimal("0.0075"), gt=0, le=Decimal("0.02")
    )
    okx_demo_score_high_risk_pct: Decimal = Field(
        default=Decimal("0.01"), gt=0, le=Decimal("0.02")
    )
    okx_demo_score_low_leverage: int = Field(default=1, ge=1, le=5)
    okx_demo_score_medium_leverage: int = Field(default=2, ge=1, le=5)
    okx_demo_score_high_leverage: int = Field(default=3, ge=1, le=5)
    okx_demo_score_low_margin_pct: Decimal = Field(
        default=Decimal("0.15"), gt=0, le=Decimal("0.50")
    )
    okx_demo_score_medium_margin_pct: Decimal = Field(
        default=Decimal("0.20"), gt=0, le=Decimal("0.50")
    )
    okx_demo_score_high_margin_pct: Decimal = Field(
        default=Decimal("0.25"), gt=0, le=Decimal("0.50")
    )
    okx_demo_portfolio_max_risk_pct: Decimal = Field(
        default=Decimal("0.02"), gt=0, le=Decimal("0.20")
    )
    okx_demo_portfolio_max_margin_pct: Decimal = Field(
        default=Decimal("0.60"), gt=0, le=Decimal("0.80")
    )
    # Optional absolute USDT capital buckets for adaptive Demo sizing. The
    # feature is disabled by default and cannot authorize a Demo or Live write.
    okx_demo_capital_bucket_enabled: bool = False
    okx_demo_position_margin_bucket_usdt: Decimal = Field(
        default=Decimal("2000"), gt=0, le=Decimal("10000")
    )

    # Opt-in structural Demo risk model.  It consumes confirmed K-line swing
    # geometry, deducts estimated costs before reward/risk approval, uses
    # isolated margin, and selects the smallest score-capped leverage needed
    # for the risk budget.  It cannot enable exchange writes or Live trading.
    okx_demo_structural_dynamic_leverage_enabled: bool = False
    okx_demo_structural_score_elite_min: int = Field(default=95, ge=3, le=99)
    okx_demo_structural_score_extreme_min: int = Field(default=98, ge=4, le=100)
    okx_demo_structural_low_risk_pct: Decimal = Field(
        default=Decimal("0.015"), gt=0, le=Decimal("0.10")
    )
    okx_demo_structural_medium_risk_pct: Decimal = Field(
        default=Decimal("0.025"), gt=0, le=Decimal("0.10")
    )
    okx_demo_structural_high_risk_pct: Decimal = Field(
        default=Decimal("0.03"), gt=0, le=Decimal("0.10")
    )
    okx_demo_structural_elite_risk_pct: Decimal = Field(
        default=Decimal("0.04"), gt=0, le=Decimal("0.10")
    )
    okx_demo_structural_extreme_risk_pct: Decimal = Field(
        default=Decimal("0.06"), gt=0, le=Decimal("0.10")
    )
    okx_demo_structural_low_leverage_cap: int = Field(default=3, ge=1, le=20)
    okx_demo_structural_medium_leverage_cap: int = Field(default=5, ge=1, le=20)
    okx_demo_structural_high_leverage_cap: int = Field(default=8, ge=1, le=20)
    okx_demo_structural_elite_leverage_cap: int = Field(default=10, ge=1, le=20)
    okx_demo_structural_extreme_leverage_cap: int = Field(default=20, ge=1, le=20)
    okx_demo_structural_round_trip_fee_bps: Decimal = Field(
        default=Decimal("10"), ge=0, le=Decimal("100")
    )
    okx_demo_structural_round_trip_slippage_bps: Decimal = Field(
        default=Decimal("4"), ge=0, le=Decimal("100")
    )
    okx_demo_structural_funding_buffer_bps: Decimal = Field(
        default=Decimal("2"), ge=0, le=Decimal("100")
    )
    okx_demo_structural_min_net_risk_reward: Decimal = Field(
        default=Decimal("2.0"), gt=0, le=Decimal("10")
    )
    okx_demo_structural_20x_min_confidence: Decimal = Field(
        default=Decimal("0.65"), ge=0, le=1
    )
    okx_demo_structural_20x_min_reliability: Decimal = Field(
        default=Decimal("0.65"), ge=0, le=1
    )
    okx_demo_structural_20x_max_instability: Decimal = Field(
        default=Decimal("0.20"), ge=0, le=1
    )

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
    def okx_live_allowed_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_live_allowed_symbols.split(",") if item.strip()]

    @property
    def okx_live_scan_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_live_scan_symbols.split(",") if item.strip()]

    @property
    def okx_demo_scan_symbol_list(self) -> list[str]:
        return [item.strip() for item in self.okx_demo_scan_symbols.split(",") if item.strip()]

    @property
    def api_token_value(self) -> str:
        return self.api_token.get_secret_value()

    @property
    def api_token_is_safe(self) -> bool:
        value = self.api_token_value.strip()
        normalized = value.lower()
        return (
            len(value) >= 32
            and not normalized.startswith("replace_with")
            and normalized
            not in {
                "change_me",
                "changeme",
                "your_api_token_here",
            }
        )

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
    def enforce_v168_safety(self) -> "Settings":
        if self.auto_trade:
            raise ValueError(
                "AUTO_TRADE is a legacy unsafe switch and must remain false; "
                "use explicitly armed OKX_LIVE_AUTO_EXECUTION"
            )

        if self.paper_auto_execution:
            if self.trading_mode != "paper":
                raise ValueError("PAPER_AUTO_EXECUTION requires TRADING_MODE=paper")
            if not self.okx_ws_enabled:
                raise ValueError("PAPER_AUTO_EXECUTION requires OKX_WS_ENABLED=true")
            if not self.paper_auto_ticks:
                raise ValueError("PAPER_AUTO_EXECUTION requires PAPER_AUTO_TICKS=true")
            if not self.paper_persistence_enabled:
                raise ValueError("PAPER_AUTO_EXECUTION requires PAPER_PERSISTENCE_ENABLED=true")

        live_parsed = urlparse(self.okx_live_rest_base_url)
        if (
            live_parsed.scheme != "https"
            or live_parsed.path not in {"", "/"}
            or live_parsed.query
            or live_parsed.fragment
            or live_parsed.username is not None
            or live_parsed.password is not None
            or live_parsed.port not in {None, 443}
        ):
            raise ValueError(
                "OKX_LIVE_REST_BASE_URL must be an HTTPS origin without credentials, path, query, or fragment"
            )
        if live_parsed.hostname not in {"openapi.okx.com", "eea.okx.com"}:
            raise ValueError("OKX_LIVE_REST_BASE_URL must use an approved OKX API host")

        if self.trading_mode == "live":
            if not self.okx_live_enabled:
                raise ValueError("TRADING_MODE=live requires OKX_LIVE_ENABLED=true")
            if not self.okx_live_credentials_configured:
                raise ValueError("TRADING_MODE=live requires OKX Live credentials")
            if self.paper_auto_execution:
                raise ValueError("PAPER_AUTO_EXECUTION must be false in live mode")
            if self.okx_demo_auto_execution or self.okx_demo_allow_order_writes:
                raise ValueError("OKX Demo writes and automation must be disabled in live mode")

        if self.live_trading:
            if self.trading_mode != "live":
                raise ValueError("LIVE_TRADING=true requires TRADING_MODE=live")
            if not self.okx_live_enabled or not self.okx_live_credentials_configured:
                raise ValueError("LIVE_TRADING=true requires enabled OKX Live credentials")
            if not self.okx_live_allow_order_writes:
                raise ValueError(
                    "LIVE_TRADING=true requires OKX_LIVE_ALLOW_ORDER_WRITES=true"
                )
            if self.environment != "production":
                raise ValueError("OKX Live writes require ENVIRONMENT=production")
            if not self.api_token_is_safe:
                raise ValueError("OKX Live writes require an API token of at least 32 characters")
            if self.web_concurrency != 1:
                raise ValueError("OKX Live writes require WEB_CONCURRENCY=1")

        if self.okx_live_allow_order_writes:
            if not self.live_trading:
                raise ValueError(
                    "OKX_LIVE_ALLOW_ORDER_WRITES=true requires LIVE_TRADING=true"
                )
            if not self.okx_live_require_protection:
                raise ValueError("OKX_LIVE_REQUIRE_PROTECTION must remain true")
            if not self.okx_live_require_ip_bound_key:
                raise ValueError("OKX_LIVE_REQUIRE_IP_BOUND_KEY must remain true")
            if not self.okx_live_forbid_withdraw_permission:
                raise ValueError("OKX_LIVE_FORBID_WITHDRAW_PERMISSION must remain true")
            if not self.okx_live_require_flat_start:
                raise ValueError("OKX_LIVE_REQUIRE_FLAT_START must remain true")
            if not self.okx_live_auto_disarm:
                raise ValueError("OKX_LIVE_AUTO_DISARM must remain true")
            if self.okx_live_max_submissions_per_arm != 1:
                raise ValueError("OKX_LIVE_MAX_SUBMISSIONS_PER_ARM must equal 1")
            if self.okx_live_max_open_positions != 1:
                raise ValueError("OKX_LIVE_MAX_OPEN_POSITIONS must equal 1")

        if self.okx_live_auto_reconcile_on_start:
            if self.trading_mode != "live" or not self.okx_live_enabled:
                raise ValueError(
                    "OKX_LIVE_AUTO_RECONCILE_ON_START requires enabled live mode"
                )
            if not self.okx_live_credentials_configured:
                raise ValueError(
                    "OKX_LIVE_AUTO_RECONCILE_ON_START requires OKX Live credentials"
                )

        if self.okx_live_auto_execution:
            if not self.okx_live_allow_order_writes or not self.live_trading:
                raise ValueError(
                    "OKX_LIVE_AUTO_EXECUTION requires explicitly enabled live writes"
                )
            if not self.okx_ws_enabled:
                raise ValueError("OKX_LIVE_AUTO_EXECUTION requires OKX_WS_ENABLED=true")
            if self.okx_live_automation_leverage > self.okx_live_max_leverage:
                raise ValueError(
                    "OKX_LIVE_AUTOMATION_LEVERAGE cannot exceed OKX_LIVE_MAX_LEVERAGE"
                )
            if not set(self.okx_live_scan_symbol_list).issubset(
                set(self.okx_live_allowed_symbol_list)
            ):
                raise ValueError(
                    "OKX_LIVE_SCAN_SYMBOLS must be a subset of OKX_LIVE_ALLOWED_SYMBOLS"
                )

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

        if self.okx_demo_score_risk_enabled:
            if not (
                self.strategy_min_score
                < self.okx_demo_score_medium_min
                < self.okx_demo_score_high_min
                <= 100
            ):
                raise ValueError(
                    "Demo score tiers must satisfy STRATEGY_MIN_SCORE < "
                    "OKX_DEMO_SCORE_MEDIUM_MIN < OKX_DEMO_SCORE_HIGH_MIN <= 100"
                )
            if not (
                self.okx_demo_score_low_risk_pct
                <= self.okx_demo_score_medium_risk_pct
                <= self.okx_demo_score_high_risk_pct
                <= self.okx_demo_portfolio_max_risk_pct
            ):
                raise ValueError(
                    "Demo score risk tiers must be nondecreasing and cannot exceed "
                    "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT"
                )
            if not (
                self.okx_demo_score_low_leverage
                <= self.okx_demo_score_medium_leverage
                <= self.okx_demo_score_high_leverage
                <= self.okx_demo_max_leverage
            ):
                raise ValueError(
                    "Demo score leverage tiers must be nondecreasing and cannot exceed "
                    "OKX_DEMO_MAX_LEVERAGE"
                )
            if not (
                self.okx_demo_score_low_margin_pct
                <= self.okx_demo_score_medium_margin_pct
                <= self.okx_demo_score_high_margin_pct
                <= self.okx_demo_portfolio_max_margin_pct
            ):
                raise ValueError(
                    "Demo score margin tiers must be nondecreasing and cannot exceed "
                    "OKX_DEMO_PORTFOLIO_MAX_MARGIN_PCT"
                )
            if self.okx_demo_max_open_positions < 2:
                raise ValueError(
                    "OKX_DEMO_SCORE_RISK_ENABLED requires "
                    "OKX_DEMO_MAX_OPEN_POSITIONS >= 2"
                )
            if (
                not self.okx_demo_continuous_session_enabled
                and self.okx_demo_portfolio_max_risk_pct
                > self.okx_demo_daily_loss_limit_pct
            ):
                raise ValueError(
                    "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT cannot exceed "
                    "OKX_DEMO_DAILY_LOSS_LIMIT_PCT"
                )

        if (
            self.okx_demo_capital_bucket_enabled
            and not self.okx_demo_score_risk_enabled
        ):
            raise ValueError(
                "OKX_DEMO_CAPITAL_BUCKET_ENABLED requires "
                "OKX_DEMO_SCORE_RISK_ENABLED=true"
            )

        if self.okx_demo_structural_dynamic_leverage_enabled:
            if not self.okx_demo_score_risk_enabled:
                raise ValueError(
                    "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED requires "
                    "OKX_DEMO_SCORE_RISK_ENABLED=true"
                )
            if not self.okx_demo_capital_bucket_enabled:
                raise ValueError(
                    "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED requires "
                    "OKX_DEMO_CAPITAL_BUCKET_ENABLED=true"
                )
            if not self.okx_demo_continuous_session_enabled:
                raise ValueError(
                    "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED requires "
                    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED=true"
                )
            if not self.okx_demo_require_protection:
                raise ValueError(
                    "structural dynamic leverage requires exchange protection"
                )
            if not (
                self.strategy_min_score
                < self.okx_demo_score_medium_min
                < self.okx_demo_score_high_min
                < self.okx_demo_structural_score_elite_min
                < self.okx_demo_structural_score_extreme_min
                <= 100
            ):
                raise ValueError("structural Demo score tiers must be strictly increasing")
            if (
                self.strategy_min_score < 72
                or self.okx_demo_score_medium_min < 80
                or self.okx_demo_score_high_min < 90
                or self.okx_demo_structural_score_elite_min < 95
                or self.okx_demo_structural_score_extreme_min < 98
            ):
                raise ValueError(
                    "structural Demo score thresholds cannot be relaxed"
                )
            structural_risks = (
                self.okx_demo_structural_low_risk_pct,
                self.okx_demo_structural_medium_risk_pct,
                self.okx_demo_structural_high_risk_pct,
                self.okx_demo_structural_elite_risk_pct,
                self.okx_demo_structural_extreme_risk_pct,
            )
            if tuple(sorted(structural_risks)) != structural_risks:
                raise ValueError("structural Demo risk tiers must be nondecreasing")
            risk_ceilings = (
                Decimal("0.015"),
                Decimal("0.025"),
                Decimal("0.03"),
                Decimal("0.04"),
                Decimal("0.06"),
            )
            if any(
                configured > ceiling
                for configured, ceiling in zip(structural_risks, risk_ceilings)
            ):
                raise ValueError("structural Demo risk ceilings cannot be increased")
            if structural_risks[-1] > self.okx_demo_portfolio_max_risk_pct:
                raise ValueError(
                    "structural extreme risk cannot exceed portfolio stop-risk limit"
                )
            if structural_risks[-1] > Decimal(str(self.max_weekly_loss_pct)):
                raise ValueError(
                    "structural extreme risk cannot exceed the weekly-loss backstop"
                )
            structural_leverage = (
                self.okx_demo_structural_low_leverage_cap,
                self.okx_demo_structural_medium_leverage_cap,
                self.okx_demo_structural_high_leverage_cap,
                self.okx_demo_structural_elite_leverage_cap,
                self.okx_demo_structural_extreme_leverage_cap,
            )
            if tuple(sorted(structural_leverage)) != structural_leverage:
                raise ValueError("structural Demo leverage caps must be nondecreasing")
            leverage_ceilings = (3, 5, 8, 10, 20)
            if any(
                configured > ceiling
                for configured, ceiling in zip(
                    structural_leverage, leverage_ceilings
                )
            ):
                raise ValueError(
                    "structural Demo leverage ceilings cannot be increased"
                )
            if structural_leverage[-1] != 20 or self.okx_demo_max_leverage < 20:
                raise ValueError(
                    "structural dynamic leverage requires an explicit 20x Demo ceiling"
                )
            total_cost_bps = (
                self.okx_demo_structural_round_trip_fee_bps
                + self.okx_demo_structural_round_trip_slippage_bps
                + self.okx_demo_structural_funding_buffer_bps
            )
            if total_cost_bps < Decimal("16"):
                raise ValueError(
                    "structural Demo cost buffer cannot be below 16 bps"
                )
            if self.okx_demo_structural_min_net_risk_reward < Decimal("2"):
                raise ValueError("structural Demo net RR floor cannot be relaxed")
            if (
                self.okx_demo_structural_20x_min_confidence < Decimal("0.65")
                or self.okx_demo_structural_20x_min_reliability
                < Decimal("0.65")
                or self.okx_demo_structural_20x_max_instability
                > Decimal("0.20")
            ):
                raise ValueError(
                    "structural Demo 20x quality thresholds cannot be relaxed"
                )

        if self.okx_demo_continuous_session_enabled:
            if not self.okx_demo_score_risk_enabled:
                raise ValueError(
                    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
                    "OKX_DEMO_SCORE_RISK_ENABLED=true"
                )
            if not self.okx_demo_capital_bucket_enabled:
                raise ValueError(
                    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
                    "OKX_DEMO_CAPITAL_BUCKET_ENABLED=true"
                )
            if not self.okx_demo_require_protection:
                raise ValueError(
                    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
                    "OKX_DEMO_REQUIRE_PROTECTION=true"
                )
            if self.okx_demo_trade_cooldown_seconds != 0:
                raise ValueError(
                    "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
                    "OKX_DEMO_TRADE_COOLDOWN_SECONDS=0"
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
