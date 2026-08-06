import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_safe_defaults(monkeypatch) -> None:
    for variable in (
        "TRADING_MODE",
        "AUTO_TRADE",
        "LIVE_TRADING",
        "PAPER_AUTO_EXECUTION",
        "OKX_DEMO_ENABLED",
        "OKX_DEMO_ALLOW_ORDER_WRITES",
        "OKX_DEMO_API_KEY",
        "OKX_DEMO_API_SECRET",
        "OKX_DEMO_API_PASSPHRASE",
        "OKX_DEMO_AUTO_RECONCILE_ON_START",
        "OKX_DEMO_AUTO_EXECUTION",
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
    assert settings.paper_auto_execution is False
    assert settings.okx_demo_enabled is False
    assert settings.okx_demo_allow_order_writes is False
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_credentials_configured is False
    assert settings.okx_demo_rest_base_url == "https://openapi.okx.com"
    assert settings.okx_demo_observability_enabled is True
    assert settings.okx_demo_soak_enabled is True
    assert settings.okx_demo_soak_allow_execute is False
    assert settings.okx_demo_execution_soak_max_submissions == 1
    assert settings.okx_demo_execution_soak_require_flat_start is True
    assert settings.okx_demo_execution_soak_require_protection is True
    assert settings.okx_demo_execution_soak_auto_disarm is True


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


def test_demo_automation_defaults_to_disabled() -> None:
    settings = Settings(_env_file=None, okx_demo_auto_execution=False)
    assert settings.okx_demo_auto_execution is False
    assert settings.okx_demo_automation_leverage == 1
    assert settings.okx_demo_max_trades_per_day == 3


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
