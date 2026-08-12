import os
from pathlib import Path

import pytest

from app.config.settings import Settings
from scripts.hermetic_pytest import (
    PRESERVED_SETTING_ENVIRONMENT_NAMES,
    SETTING_ENVIRONMENT_NAMES,
    build_hermetic_environment,
    enabled_execution_authority,
)


ROOT = Path(__file__).resolve().parents[2]


def test_hermetic_environment_preserves_only_test_infrastructure_settings() -> None:
    source = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "DATABASE_URL": "postgresql+asyncpg://test:secret@postgres/test",
        "redis_url": "redis://redis:6379/9",
        "ENVIRONMENT": "production",
        "TRADING_MODE": "okx_demo",
        "LIVE_TRADING": "true",
        "OKX_DEMO_ALLOW_ORDER_WRITES": "true",
        "OKX_DEMO_STRUCTURAL_DYNAMIC_LEVERAGE_ENABLED": "true",
        "OKX_DEMO_PORTFOLIO_MAX_RISK_PCT": "0.10",
        "MAX_WEEKLY_LOSS_PCT": "0.10",
        "PYTEST_ADDOPTS": "--lf",
    }

    result = build_hermetic_environment(source)

    assert result["PATH"] == source["PATH"]
    assert result["DATABASE_URL"] == source["DATABASE_URL"]
    assert result["REDIS_URL"] == source["redis_url"]
    assert result["ENVIRONMENT"] == "test"
    assert result["TRADING_MODE"] == "analysis_only"
    assert "PYTEST_ADDOPTS" not in result

    retained_setting_names = {
        name.upper()
        for name in result
        if name.upper() in SETTING_ENVIRONMENT_NAMES
    }
    assert retained_setting_names == (
        PRESERVED_SETTING_ENVIRONMENT_NAMES | {"ENVIRONMENT", "TRADING_MODE"}
    )


def test_safe_defaults_have_no_execution_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(os.environ):
        if name.upper() in SETTING_ENVIRONMENT_NAMES:
            monkeypatch.delenv(name, raising=False)

    settings = Settings(
        _env_file=None,
        environment="test",
        trading_mode="analysis_only",
    )

    assert enabled_execution_authority(settings) == ()


@pytest.mark.parametrize(
    "script_name",
    [
        "verify_v168_live_boundary.ps1",
        "verify_mie_gate1.ps1",
        "verify_mie_gate2.ps1",
    ],
)
def test_docker_verifiers_use_hermetic_pytest(script_name: str) -> None:
    source = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "python scripts/hermetic_pytest.py" in source
    assert "@testEnvironment" not in source


def test_mie_gate2_checks_host_authority_before_container_start() -> None:
    source = (ROOT / "scripts" / "verify_mie_gate2.ps1").read_text(
        encoding="utf-8"
    )

    host_preflight = source.index(
        "Host Compose execution-authority preflight"
    )
    container_start = source.index("Docker build and start")

    assert host_preflight < container_start
    assert "python -c" not in source
    assert "$authorityProbe | docker compose exec -T api python -" in source
    assert "$revisionProbe | docker compose exec -T api python -" in source
    for setting_name in (
        "AUTO_TRADE",
        "PAPER_AUTO_EXECUTION",
        "LIVE_TRADING",
        "OKX_LIVE_ALLOW_ORDER_WRITES",
        "OKX_LIVE_AUTO_EXECUTION",
        "OKX_DEMO_ALLOW_ORDER_WRITES",
        "OKX_DEMO_AUTO_EXECUTION",
        "OKX_DEMO_SOAK_ALLOW_EXECUTE",
    ):
        assert setting_name in source
