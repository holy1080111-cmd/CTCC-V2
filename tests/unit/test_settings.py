from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.exchange.okx.symbols import (
    LIVE_BOUNDARY_INSTRUMENT_IDS,
    REVIEWED_DEMO_INSTRUMENT_IDS,
)


def test_safe_defaults(monkeypatch) -> None:
    for variable in (
        "TRADING_MODE",
        "AUTO_TRADE",
        "LIVE_TRADING",
        "OKX_WS_SYMBOLS",
        "OKX_LIVE_REST_BASE_URL",
        "OKX_LIVE_TIMEOUT_SECONDS",
        "OKX_LIVE_READ_MAX_RETRIES",
        "OKX_LIVE_API_KEY",
        "OKX_LIVE_API_SECRET",
        "OKX_LIVE_API_PASSPHRASE",
        "OKX_LIVE_ENABLED",
        "OKX_LIVE_ALLOW_ORDER_WRITES",
        "OKX_LIVE_ALLOWED_SYMBOLS",
        "OKX_LIVE_MAX_ORDER_SIZE_CONTRACTS",
        "OKX_LIVE_MAX_NOTIONAL_USDT",
        "OKX_LIVE_MAX_OPEN_POSITIONS",
        "OKX_LIVE_MAX_LEVERAGE",
        "OKX_LIVE_REQUIRE_PROTECTION",
        "OKX_LIVE_REQUIRE_IP_BOUND_KEY",
        "OKX_LIVE_FORBID_WITHDRAW_PERMISSION",
        "OKX_LIVE_AUTO_RECONCILE_ON_START",
        "OKX_LIVE_ARM_TTL_SECONDS",
        "OKX_LIVE_MAX_SUBMISSIONS_PER_ARM",
        "OKX_LIVE_SESSION_LOSS_LIMIT_PCT",
        "OKX_LIVE_CANCEL_ALL_AFTER_SECONDS",
        "OKX_LIVE_REQUIRE_FLAT_START",
        "OKX_LIVE_AUTO_DISARM",
        "OKX_LIVE_AUTO_EXECUTION",
        "OKX_LIVE_SCAN_SYMBOLS",
        "OKX_LIVE_AUTOMATION_LEVERAGE",
        "PAPER_AUTO_EXECUTION",
        "PAPER_SCAN_SYMBOLS",
        "OKX_DEMO_ENABLED",
        "OKX_DEMO_ALLOW_ORDER_WRITES",
        "OKX_DEMO_ALLOWED_SYMBOLS",
        "OKX_DEMO_API_KEY",
        "OKX_DEMO_API_SECRET",
        "OKX_DEMO_API_PASSPHRASE",
        "OKX_DEMO_AUTO_RECONCILE_ON_START",
        "OKX_DEMO_AUTO_EXECUTION",
        "OKX_DEMO_SCAN_SYMBOLS",
        "OKX_DEMO_TRADE_RECONCILE_GRACE_SECONDS",
        "OKX_DEMO_AUTOMATION_MAX_CONSECUTIVE_LOSSES",
        "OKX_DEMO_CONTINUOUS_SESSION_ENABLED",
        "OKX_DEMO_SCORE_RISK_ENABLED",
        "OKX_DEMO_SCORE_MEDIUM_MIN",
        "OKX_DEMO_SCORE_HIGH_MIN",
        "OKX_DEMO_SCORE_LOW_RISK_PCT",
        "OKX_DEMO_SCORE_MEDIUM_RISK_PCT",
        "OKX_DEMO_SCORE_HIGH_RISK_PCT",
        "OKX_DEMO_SCORE_LOW_LEVERAGE",
        "OKX_DEMO_SCORE_MEDIUM_LEVERAGE",
        "OKX_DEMO_SCORE_HIGH_LEVERAGE",
        "OKX_DEMO_SCORE_LOW_MARGIN_PCT",
        "OKX_DEMO_SCORE_MEDIUM_MARGIN_PCT",
        "OKX_DEMO_SCORE_HIGH_MARGIN_PCT",
        "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT",
        "OKX_DEMO_PORTFOLIO_MAX_MARGIN_PCT",
        "OKX_DEMO_CAPITAL_BUCKET_ENABLED",
        "OKX_DEMO_POSITION_MARGIN_BUCKET_USDT",
        "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED",
        "OKX_DEMO_STRUCTURAL_SCORE_ELITE_MIN",
        "OKX_DEMO_STRUCTURAL_SCORE_EXTREME_MIN",
        "OKX_DEMO_STRUCTURAL_LOW_RISK_PCT",
        "OKX_DEMO_STRUCTURAL_MEDIUM_RISK_PCT",
        "OKX_DEMO_STRUCTURAL_HIGH_RISK_PCT",
        "OKX_DEMO_STRUCTURAL_ELITE_RISK_PCT",
        "OKX_DEMO_STRUCTURAL_EXTREME_RISK_PCT",
        "OKX_DEMO_STRUCTURAL_LOW_LEVERAGE_CAP",
        "OKX_DEMO_STRUCTURAL_MEDIUM_LEVERAGE_CAP",
        "OKX_DEMO_STRUCTURAL_HIGH_LEVERAGE_CAP",
        "OKX_DEMO_STRUCTURAL_ELITE_LEVERAGE_CAP",
        "OKX_DEMO_STRUCTURAL_EXTREME_LEVERAGE_CAP",
        "OKX_DEMO_STRUCTURAL_ROUND_TRIP_FEE_BPS",
        "OKX_DEMO_STRUCTURAL_ROUND_TRIP_SLIPPAGE_BPS",
        "OKX_DEMO_STRUCTURAL_FUNDING_BUFFER_BPS",
        "OKX_DEMO_STRUCTURAL_MIN_NET_RISK_REWARD",
        "OKX_DEMO_STRUCTURAL_20X_MIN_CONFIDENCE",
        "OKX_DEMO_STRUCTURAL_20X_MIN_RELIABILITY",
        "OKX_DEMO_STRUCTURAL_20X_MAX_INSTABILITY",
        "OKX_DEMO_SOAK_ALLOW_EXECUTE",
        "OKX_DEMO_SOAK_ENABLED",
        "OKX_DEMO_EXECUTION_SOAK_MAX_SUBMISSIONS",
        "OKX_DEMO_EXECUTION_SOAK_LOSS_LIMIT_PCT",
        "OKX_DEMO_EXECUTION_SOAK_REQUIRE_FLAT_START",
        "OKX_DEMO_EXECUTION_SOAK_REQUIRE_PROTECTION",
        "OKX_DEMO_EXECUTION_SOAK_AUTO_DISARM",
        "OKX_DEMO_PERFORMANCE_WINDOW_DAYS",
        "OKX_DEMO_PERFORMANCE_SNAPSHOT_RETENTION_DAYS",
        "OKX_DEMO_PERFORMANCE_SNAPSHOT_QUERY_LIMIT",
        "OKX_DEMO_PERFORMANCE_ORDER_QUERY_LIMIT",
        "OKX_DEMO_PERFORMANCE_MIN_ACTIVE_DAYS",
        "OKX_DEMO_PERFORMANCE_MIN_REALIZED_TRADES",
        "OKX_DEMO_PERFORMANCE_MAX_AVERAGE_SLIPPAGE_BPS",
        "OKX_DEMO_PERFORMANCE_MIN_PROFIT_FACTOR",
        "OKX_DEMO_PERFORMANCE_MAX_DRAWDOWN_PCT",
        "OKX_DEMO_STRATEGY_REVIEW_MIN_TRADES",
        "OKX_DEMO_STRATEGY_REVIEW_MIN_WIN_RATE",
        "OKX_DEMO_STRATEGY_AUTO_DISABLE",
    ):
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)
    assert settings.trading_mode == "analysis_only"
    assert settings.auto_trade is False
    assert settings.live_trading is False
    assert settings.okx_live_credentials_configured is False
    assert settings.okx_live_enabled is False
    assert settings.okx_live_allow_order_writes is False
    assert settings.okx_live_auto_execution is False
    assert settings.okx_live_max_submissions_per_arm == 1
    assert settings.okx_live_require_protection is True
    assert settings.okx_live_require_ip_bound_key is True
    assert settings.okx_live_forbid_withdraw_permission is True
    assert settings.okx_live_require_flat_start is True
    assert settings.okx_live_auto_disarm is True
    assert settings.okx_live_rest_base_url == "https://openapi.okx.com"
    assert settings.okx_live_timeout_seconds == 10
    assert settings.okx_live_read_max_retries == 2
    assert settings.paper_auto_execution is False
    assert settings.okx_demo_enabled is False
    assert settings.okx_demo_allow_order_writes is False
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_continuous_session_enabled is False
    assert settings.okx_demo_structural_dynamic_leverage_enabled is False
    assert settings.okx_demo_credentials_configured is False
    assert settings.okx_demo_rest_base_url == "https://openapi.okx.com"
    assert settings.okx_demo_observability_enabled is True
    assert settings.okx_demo_soak_enabled is True
    assert settings.okx_demo_soak_allow_execute is False
    assert settings.okx_demo_execution_soak_max_submissions == 1
    assert settings.okx_demo_execution_soak_require_flat_start is True
    assert settings.okx_demo_execution_soak_require_protection is True
    assert settings.okx_demo_execution_soak_auto_disarm is True
    assert tuple(settings.okx_ws_symbol_list) == REVIEWED_DEMO_INSTRUMENT_IDS
    assert tuple(settings.paper_scan_symbol_list) == REVIEWED_DEMO_INSTRUMENT_IDS
    assert tuple(settings.okx_demo_allowed_symbol_list) == (
        REVIEWED_DEMO_INSTRUMENT_IDS
    )
    assert tuple(settings.okx_demo_scan_symbol_list) == (
        REVIEWED_DEMO_INSTRUMENT_IDS
    )
    assert tuple(settings.okx_live_allowed_symbol_list) == (
        LIVE_BOUNDARY_INSTRUMENT_IDS
    )
    assert tuple(settings.okx_live_scan_symbol_list) == (
        LIVE_BOUNDARY_INSTRUMENT_IDS
    )


def test_symbol_lists_are_normalized_without_expanding_live_boundary() -> None:
    settings = Settings(
        _env_file=None,
        okx_demo_allowed_symbols=" btc-usdt-swap, sol-usdt-swap ",
        okx_demo_scan_symbols=" sol-usdt-swap ",
        okx_live_allowed_symbols=" btc-usdt-swap ",
        okx_live_scan_symbols=" btc-usdt-swap ",
    )

    assert settings.okx_demo_allowed_symbol_list == [
        "BTC-USDT-SWAP",
        "SOL-USDT-SWAP",
    ]
    assert settings.okx_demo_scan_symbol_list == ["SOL-USDT-SWAP"]
    assert settings.okx_live_allowed_symbol_list == ["BTC-USDT-SWAP"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("okx_demo_allowed_symbols", "BTC-USDT-SWAP,BNB-USDT-SWAP"),
        ("okx_demo_scan_symbols", "BTC-USDT-SWAP,BTC-USDT-SWAP"),
        ("okx_live_allowed_symbols", "BTC-USDT-SWAP,SOL-USDT-SWAP"),
        ("okx_ws_symbols", "BTC-USDT-SWAP,BNB-USDT-SWAP"),
    ],
)
def test_symbol_scope_rejects_unreviewed_or_duplicate_instruments(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_demo_scan_must_always_be_inside_demo_allowlist() -> None:
    with pytest.raises(ValidationError, match="subset"):
        Settings(
            _env_file=None,
            okx_demo_allowed_symbols="BTC-USDT-SWAP",
            okx_demo_scan_symbols="SOL-USDT-SWAP",
        )


def _structural_dynamic_settings(**updates) -> Settings:
    values = {
        "okx_demo_score_risk_enabled": True,
        "okx_demo_capital_bucket_enabled": True,
        "okx_demo_continuous_session_enabled": True,
        "okx_demo_trade_cooldown_seconds": 0,
        "okx_demo_structural_dynamic_leverage_enabled": True,
        "okx_demo_max_open_positions": 3,
        "okx_demo_max_leverage": 20,
        "okx_demo_portfolio_max_risk_pct": Decimal("0.10"),
        "max_weekly_loss_pct": 0.10,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_structural_dynamic_risk_accepts_explicit_safe_dependency_set() -> None:
    settings = _structural_dynamic_settings()

    assert settings.okx_demo_structural_extreme_risk_pct == Decimal("0.06")
    assert settings.okx_demo_structural_extreme_leverage_cap == 20
    assert settings.okx_demo_position_margin_bucket_usdt == Decimal("2000")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("okx_demo_score_risk_enabled", False),
        ("okx_demo_capital_bucket_enabled", False),
        ("okx_demo_continuous_session_enabled", False),
        ("okx_demo_require_protection", False),
        ("okx_demo_max_leverage", 10),
        ("max_weekly_loss_pct", 0.05),
    ],
)
def test_structural_dynamic_risk_rejects_missing_hard_dependency(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _structural_dynamic_settings(**{field: value})


def test_structural_dynamic_risk_rejects_portfolio_limit_below_extreme_tier() -> None:
    with pytest.raises(ValidationError, match="portfolio stop-risk"):
        _structural_dynamic_settings(
            okx_demo_portfolio_max_risk_pct=Decimal("0.05")
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy_min_score", 71),
        ("okx_demo_score_medium_min", 79),
        ("okx_demo_score_high_min", 89),
        ("okx_demo_structural_score_elite_min", 94),
        ("okx_demo_structural_score_extreme_min", 97),
        ("okx_demo_structural_low_risk_pct", Decimal("0.016")),
        ("okx_demo_structural_medium_risk_pct", Decimal("0.026")),
        ("okx_demo_structural_high_risk_pct", Decimal("0.031")),
        ("okx_demo_structural_elite_risk_pct", Decimal("0.041")),
        ("okx_demo_structural_extreme_risk_pct", Decimal("0.061")),
        ("okx_demo_structural_low_leverage_cap", 4),
        ("okx_demo_structural_medium_leverage_cap", 6),
        ("okx_demo_structural_high_leverage_cap", 9),
        ("okx_demo_structural_elite_leverage_cap", 11),
        ("okx_demo_structural_round_trip_fee_bps", Decimal("9")),
        ("okx_demo_structural_min_net_risk_reward", Decimal("1.9")),
        ("okx_demo_structural_20x_min_confidence", Decimal("0.64")),
        ("okx_demo_structural_20x_min_reliability", Decimal("0.64")),
        ("okx_demo_structural_20x_max_instability", Decimal("0.21")),
    ],
)
def test_structural_dynamic_profile_can_only_be_made_stricter(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        _structural_dynamic_settings(**{field: value})


def test_rejects_auto_trade() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auto_trade=True)


def test_rejects_live_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trading_mode="live")


def test_auto_paper_requires_paper_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            paper_auto_execution=True,
            trading_mode="analysis_only",
            okx_ws_enabled=True,
            paper_auto_ticks=True,
        )


def test_auto_paper_requires_realtime_ticks() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            paper_auto_execution=True,
            trading_mode="paper",
            okx_ws_enabled=False,
            paper_auto_ticks=True,
        )


def test_auto_paper_requires_persistence() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            paper_auto_execution=True,
            trading_mode="paper",
            okx_ws_enabled=True,
            paper_auto_ticks=True,
            paper_persistence_enabled=False,
        )


def test_demo_writes_require_demo_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            trading_mode="paper",
            okx_demo_enabled=True,
            okx_demo_allow_order_writes=True,
            okx_demo_api_key="key",
            okx_demo_api_secret="secret",
            okx_demo_api_passphrase="pass",
        )


def test_demo_writes_require_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            trading_mode="okx_demo",
            okx_demo_enabled=True,
            okx_demo_allow_order_writes=True,
            okx_demo_api_key="",
            okx_demo_api_secret="",
            okx_demo_api_passphrase="",
        )


def test_demo_base_url_must_be_approved_okx_origin() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, okx_demo_rest_base_url="https://example.com")


def test_demo_credentials_are_masked_in_repr() -> None:
    settings = Settings(
        _env_file=None,
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_api_key="demo-key",
        okx_demo_api_secret="demo-secret",
        okx_demo_api_passphrase="demo-pass",
    )
    rendered = repr(settings)
    assert "demo-secret" not in rendered
    assert "demo-pass" not in rendered


def test_api_token_is_masked_in_repr() -> None:
    settings = Settings(_env_file=None, api_token="local-token-" + "x" * 32)
    assert "local-token" not in repr(settings)
    assert settings.api_token_is_safe is True


def test_public_example_api_token_is_never_accepted_for_live_writes() -> None:
    with pytest.raises(ValidationError, match="API token"):
        Settings(
            _env_file=None,
            environment="production",
            trading_mode="live",
            live_trading=True,
            okx_live_enabled=True,
            okx_live_allow_order_writes=True,
            okx_live_api_key="key",
            okx_live_api_secret="secret",
            okx_live_api_passphrase="pass",
            api_token="replace_with_at_least_32_random_characters",
        )


def test_demo_automation_requires_websocket() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            trading_mode="okx_demo",
            okx_demo_enabled=True,
            okx_demo_allow_order_writes=True,
            okx_demo_api_key="key",
            okx_demo_api_secret="secret",
            okx_demo_api_passphrase="pass",
            okx_demo_auto_execution=True,
            okx_ws_enabled=False,
        )


def test_demo_automation_scan_requires_matching_websocket_subscription() -> None:
    with pytest.raises(ValidationError, match="OKX_WS_SYMBOLS"):
        Settings(
            _env_file=None,
            trading_mode="okx_demo",
            okx_demo_enabled=True,
            okx_demo_allow_order_writes=True,
            okx_demo_api_key="key",
            okx_demo_api_secret="secret",
            okx_demo_api_passphrase="pass",
            okx_demo_auto_execution=True,
            okx_ws_enabled=True,
            okx_ws_symbols="BTC-USDT-SWAP",
            okx_demo_allowed_symbols="BTC-USDT-SWAP,SOL-USDT-SWAP",
            okx_demo_scan_symbols="SOL-USDT-SWAP",
        )


def test_paper_automation_scan_requires_matching_websocket_subscription() -> None:
    with pytest.raises(ValidationError, match="OKX_WS_SYMBOLS"):
        Settings(
            _env_file=None,
            trading_mode="paper",
            paper_auto_execution=True,
            okx_ws_enabled=True,
            okx_ws_symbols="BTC-USDT-SWAP",
            paper_scan_symbols="SOL-USDT-SWAP",
        )


def test_demo_automation_defaults_to_disabled(monkeypatch) -> None:
    for variable in (
        "OKX_DEMO_AUTO_EXECUTION",
        "OKX_DEMO_AUTOMATION_LEVERAGE",
        "OKX_DEMO_MAX_TRADES_PER_DAY",
    ):
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_automation_leverage == 1
    assert settings.okx_demo_max_trades_per_day == 3
    assert settings.okx_demo_continuous_session_enabled is False
    assert settings.okx_demo_execution_max_adverse_slippage_bps == Decimal("5")


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("50.01")])
def test_demo_execution_adverse_slippage_boundary_is_bounded(
    value: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_execution_max_adverse_slippage_bps=value,
        )


def test_execute_soak_requires_demo_automation() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_soak_allow_execute=True,
            okx_demo_soak_enabled=True,
            okx_demo_auto_execution=False,
        )


def test_soak_default_duration_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_soak_default_duration_minutes=120,
            okx_demo_soak_max_duration_minutes=60,
        )


def _execute_soak_settings(**updates):
    values = dict(
        trading_mode="okx_demo",
        okx_demo_enabled=True,
        okx_demo_allow_order_writes=True,
        okx_demo_api_key="key",
        okx_demo_api_secret="secret",
        okx_demo_api_passphrase="pass",
        okx_demo_auto_execution=True,
        okx_ws_enabled=True,
        okx_demo_soak_enabled=True,
        okx_demo_soak_allow_execute=True,
        okx_demo_soak_interval_seconds=60,
    )
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_execute_soak_submission_limit_cannot_exceed_daily_limit() -> None:
    with pytest.raises(ValidationError):
        _execute_soak_settings(
            okx_demo_max_trades_per_day=1,
            okx_demo_execution_soak_max_submissions=2,
        )


def test_execute_soak_loss_budget_cannot_exceed_daily_loss_budget() -> None:
    with pytest.raises(ValidationError):
        _execute_soak_settings(
            okx_demo_daily_loss_limit_pct="0.005",
            okx_demo_execution_soak_loss_limit_pct="0.006",
        )


@pytest.mark.parametrize(
    "field",
    [
        "okx_demo_execution_soak_require_flat_start",
        "okx_demo_execution_soak_require_protection",
        "okx_demo_execution_soak_auto_disarm",
    ],
)
def test_execute_soak_required_guardrails_cannot_be_disabled(field: str) -> None:
    with pytest.raises(ValidationError):
        _execute_soak_settings(**{field: False})


def test_performance_defaults_are_conservative(monkeypatch) -> None:
    for variable in (
        "OKX_DEMO_PERFORMANCE_WINDOW_DAYS",
        "OKX_DEMO_PERFORMANCE_MIN_ACTIVE_DAYS",
        "OKX_DEMO_PERFORMANCE_MIN_REALIZED_TRADES",
        "OKX_DEMO_STRATEGY_AUTO_DISABLE",
    ):
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)
    assert settings.okx_demo_performance_window_days == 30
    assert settings.okx_demo_performance_min_active_days == 7
    assert settings.okx_demo_performance_min_realized_trades == 20
    assert settings.okx_demo_strategy_auto_disable is False


def test_performance_window_cannot_exceed_retention() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_performance_window_days=100,
            okx_demo_performance_snapshot_retention_days=90,
        )


def test_strategy_auto_disable_is_forbidden() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, okx_demo_strategy_auto_disable=True)


def test_live_and_demo_credentials_are_isolated() -> None:
    live = Settings(
        _env_file=None,
        okx_live_api_key="live-key",
        okx_live_api_secret="live-secret",
        okx_live_api_passphrase="live-pass",
        okx_demo_api_key="",
        okx_demo_api_secret="",
        okx_demo_api_passphrase="",
    )
    assert live.okx_live_credentials_configured is True
    assert live.okx_demo_credentials_configured is False

    demo = Settings(
        _env_file=None,
        okx_demo_api_key="demo-key",
        okx_demo_api_secret="demo-secret",
        okx_demo_api_passphrase="demo-pass",
        okx_live_api_key="",
        okx_live_api_secret="",
        okx_live_api_passphrase="",
    )
    assert demo.okx_demo_credentials_configured is True
    assert demo.okx_live_credentials_configured is False


def test_live_credentials_are_masked_in_repr() -> None:
    settings = Settings(
        _env_file=None,
        okx_live_api_key="live-key",
        okx_live_api_secret="live-secret",
        okx_live_api_passphrase="live-pass",
    )
    rendered = repr(settings)
    assert "live-secret" not in rendered
    assert "live-pass" not in rendered


def test_rejects_live_trading_flag() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, live_trading=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://openapi.okx.com",
        "https://example.com",
        "https://openapi.okx.com/api/v5",
        "https://openapi.okx.com?source=unsafe",
        "https://user@openapi.okx.com",
        "https://openapi.okx.com:8443",
    ],
)
def test_live_base_url_must_be_approved_okx_origin(url: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, okx_live_rest_base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://openapi.okx.com",
        "https://eea.okx.com",
    ],
)
def test_live_base_url_accepts_approved_okx_origin(url: str) -> None:
    settings = Settings(_env_file=None, okx_live_rest_base_url=url)
    assert settings.okx_live_rest_base_url == url


def _live_write_settings(**updates) -> Settings:
    values = {
        "environment": "production",
        "trading_mode": "live",
        "live_trading": True,
        "okx_live_enabled": True,
        "okx_live_allow_order_writes": True,
        "okx_live_api_key": "live-key",
        "okx_live_api_secret": "live-secret",
        "okx_live_api_passphrase": "live-passphrase",
        "api_token": "x" * 40,
        "web_concurrency": 1,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_live_read_mode_can_be_enabled_without_write_authority() -> None:
    settings = Settings(
        _env_file=None,
        trading_mode="live",
        okx_live_enabled=True,
        okx_live_api_key="live-key",
        okx_live_api_secret="live-secret",
        okx_live_api_passphrase="live-passphrase",
    )

    assert settings.live_trading is False
    assert settings.okx_live_allow_order_writes is False


def test_live_write_configuration_requires_every_explicit_gate() -> None:
    settings = _live_write_settings()

    assert settings.live_trading is True
    assert settings.okx_live_allow_order_writes is True
    assert settings.okx_live_max_submissions_per_arm == 1
    assert settings.okx_live_require_protection is True
    assert settings.okx_live_require_flat_start is True
    assert settings.okx_live_auto_disarm is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("okx_live_require_protection", False),
        ("okx_live_require_ip_bound_key", False),
        ("okx_live_forbid_withdraw_permission", False),
        ("okx_live_require_flat_start", False),
        ("okx_live_auto_disarm", False),
        ("okx_live_max_open_positions", 2),
        ("web_concurrency", 2),
        ("environment", "development"),
    ],
)
def test_live_write_required_guardrails_cannot_be_relaxed(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        _live_write_settings(**{field: value})


def test_live_automation_requires_websocket_and_write_authority() -> None:
    with pytest.raises(ValidationError):
        _live_write_settings(okx_live_auto_execution=True, okx_ws_enabled=False)

    settings = _live_write_settings(
        okx_live_auto_execution=True,
        okx_ws_enabled=True,
    )
    assert settings.okx_live_auto_execution is True


def test_demo_adaptive_portfolio_defaults_to_disabled_and_three_stop_limit(
    monkeypatch,
) -> None:
    for variable in (
        "OKX_DEMO_SCORE_RISK_ENABLED",
        "OKX_DEMO_AUTOMATION_MAX_CONSECUTIVE_LOSSES",
        "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT",
        "OKX_DEMO_PORTFOLIO_MAX_MARGIN_PCT",
        "OKX_DEMO_CAPITAL_BUCKET_ENABLED",
        "OKX_DEMO_POSITION_MARGIN_BUCKET_USDT",
    ):
        monkeypatch.delenv(variable, raising=False)
    settings = Settings(_env_file=None)

    assert settings.okx_demo_score_risk_enabled is False
    assert settings.okx_demo_automation_max_consecutive_losses == 3
    assert settings.okx_demo_portfolio_max_risk_pct == Decimal("0.02")
    assert settings.okx_demo_portfolio_max_margin_pct == Decimal("0.60")
    assert settings.okx_demo_capital_bucket_enabled is False
    assert settings.okx_demo_position_margin_bucket_usdt == Decimal("2000")


def test_demo_capital_bucket_requires_score_risk_gate() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_capital_bucket_enabled=True,
            okx_demo_score_risk_enabled=False,
        )

    settings = Settings(
        _env_file=None,
        okx_demo_capital_bucket_enabled=True,
        okx_demo_position_margin_bucket_usdt=Decimal("2000"),
        okx_demo_score_risk_enabled=True,
        okx_demo_max_open_positions=3,
        okx_demo_daily_loss_limit_pct=Decimal("0.03"),
    )
    assert settings.okx_demo_capital_bucket_enabled is True


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {},
            "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
            "OKX_DEMO_SCORE_RISK_ENABLED=true",
        ),
        (
            {
                "okx_demo_score_risk_enabled": True,
                "okx_demo_max_open_positions": 3,
                "okx_demo_daily_loss_limit_pct": Decimal("0.03"),
            },
            "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
            "OKX_DEMO_CAPITAL_BUCKET_ENABLED=true",
        ),
        (
            {
                "okx_demo_score_risk_enabled": True,
                "okx_demo_capital_bucket_enabled": True,
                "okx_demo_max_open_positions": 3,
                "okx_demo_daily_loss_limit_pct": Decimal("0.03"),
                "okx_demo_require_protection": False,
                "okx_demo_trade_cooldown_seconds": 0,
            },
            "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
            "OKX_DEMO_REQUIRE_PROTECTION=true",
        ),
        (
            {
                "okx_demo_score_risk_enabled": True,
                "okx_demo_capital_bucket_enabled": True,
                "okx_demo_max_open_positions": 3,
                "okx_demo_daily_loss_limit_pct": Decimal("0.03"),
            },
            "OKX_DEMO_CONTINUOUS_SESSION_ENABLED requires "
            "OKX_DEMO_TRADE_COOLDOWN_SECONDS=0",
        ),
    ],
)
def test_demo_continuous_session_requires_bounded_risk_gates(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(
            _env_file=None,
            okx_demo_continuous_session_enabled=True,
            **updates,
        )


def test_demo_continuous_session_can_be_explicitly_configured() -> None:
    settings = Settings(
        _env_file=None,
        okx_demo_continuous_session_enabled=True,
        okx_demo_trade_cooldown_seconds=0,
        okx_demo_require_protection=True,
        okx_demo_score_risk_enabled=True,
        okx_demo_capital_bucket_enabled=True,
        okx_demo_max_open_positions=3,
    )

    assert settings.okx_demo_continuous_session_enabled is True
    assert settings.okx_demo_trade_cooldown_seconds == 0
    assert settings.okx_demo_daily_loss_limit_pct == Decimal("0.01")


def test_demo_adaptive_portfolio_requires_multiple_position_capacity() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_score_risk_enabled=True,
            okx_demo_max_open_positions=1,
            okx_demo_daily_loss_limit_pct=Decimal("0.03"),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {
            "okx_demo_score_medium_min": 72,
        },
        {
            "okx_demo_score_medium_min": 91,
            "okx_demo_score_high_min": 90,
        },
        {
            "okx_demo_score_medium_risk_pct": Decimal("0.004"),
        },
        {
            "okx_demo_score_medium_leverage": 4,
            "okx_demo_score_high_leverage": 3,
        },
        {
            "okx_demo_score_high_margin_pct": Decimal("0.70"),
        },
    ],
)
def test_demo_adaptive_portfolio_rejects_non_monotonic_tiers(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_score_risk_enabled=True,
            okx_demo_max_open_positions=3,
            okx_demo_daily_loss_limit_pct=Decimal("0.03"),
            **updates,
        )


def test_demo_portfolio_open_risk_cannot_exceed_daily_loss_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            okx_demo_score_risk_enabled=True,
            okx_demo_max_open_positions=3,
            okx_demo_portfolio_max_risk_pct=Decimal("0.02"),
            okx_demo_daily_loss_limit_pct=Decimal("0.01"),
        )
