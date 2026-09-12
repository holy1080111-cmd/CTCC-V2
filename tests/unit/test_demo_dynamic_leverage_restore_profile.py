"""Offline pending configuration only, never applied to a service or position."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    score_risk_tier,
)
from app.demo_automation.structural_risk import select_structural_leverage
from tests.unit.test_demo_structural_risk import finalized_candidate

PROFILE = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "demo_dynamic_leverage_conservative.env.example"
)
D = Decimal


def profile():
    pairs = [
        line.split("=", 1)
        for line in PROFILE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(pairs) == len({key for key, _ in pairs}) == 13
    return {key.lower(): value for key, value in pairs}


def settings():
    return Settings(
        _env_file=None,
        okx_demo_score_risk_enabled=True,
        okx_demo_capital_bucket_enabled=True,
        okx_demo_continuous_session_enabled=True,
        okx_demo_trade_cooldown_seconds=0,
        okx_demo_max_open_positions=8,
        okx_demo_position_margin_bucket_usdt=D(300),
        okx_demo_score_low_risk_pct=D("0.005"),
        okx_demo_score_medium_risk_pct=D("0.005"),
        okx_demo_score_high_risk_pct=D("0.005"),
        okx_demo_score_low_leverage=1,
        okx_demo_score_medium_leverage=1,
        okx_demo_score_high_leverage=1,
        **profile(),
    )


def test_pending_overlay_has_no_permission_credentials_or_execution_controls():
    values = profile()
    assert set(values) == {
        "okx_demo_max_leverage",
        "okx_demo_structural_dynamic_leverage_enabled",
        "okx_demo_portfolio_max_risk_pct",
        *(
            f"okx_demo_structural_{tier}_{suffix}"
            for tier in ("low", "medium", "high", "elite", "extreme")
            for suffix in ("leverage_cap", "risk_pct")
        ),
    }
    checked = settings()
    assert not checked.live_trading
    assert not checked.okx_live_allow_order_writes
    assert not checked.okx_live_auto_execution
    assert not checked.okx_demo_allow_order_writes
    assert not checked.okx_demo_auto_execution


@pytest.mark.parametrize("score,cap", [(72, 3), (80, 5), (90, 8), (95, 10), (98, 20)])
def test_restored_dynamic_caps_preserve_half_percent_trade_risk(score, cap):
    checked = settings()
    tier = score_risk_tier(score, checked)
    assert tier.leverage == cap
    assert tier.risk_pct == D("0.005")
    assert checked.okx_demo_portfolio_max_risk_pct == D("0.01")
    assert checked.okx_demo_position_margin_bucket_usdt == 300
    assert checked.okx_demo_portfolio_max_margin_pct == D("0.60")


def test_old_higher_structural_risk_defaults_are_not_restored():
    tiers = configured_score_risk_tiers(settings())
    assert [tier.leverage for tier in tiers] == [3, 5, 8, 10, 20]
    assert {tier.risk_pct for tier in tiers} == {D("0.005")}
    assert settings().okx_demo_require_protection
    assert settings().okx_demo_structural_min_net_risk_reward >= 2


def test_high_score_is_a_cap_not_a_forced_twenty_x_selection():
    checked = settings()
    result = select_structural_leverage(
        finalized_candidate(),
        score_risk_tier(99, checked),
        checked,
        account_equity=D(100),
        position_margin_cap=D(300),
    )
    assert result.leverage_cap == 20
    assert result.selected_leverage == 3


def test_zero_risk_score_cannot_reopen_extreme_leverage_after_restoration():
    checked = settings()
    candidate = finalized_candidate().model_copy(update={"risk_score": 0})
    result = select_structural_leverage(
        candidate,
        score_risk_tier(99, checked),
        checked,
        account_equity=D(5000),
        position_margin_cap=D(300),
    )
    assert not result.twenty_x_eligible
    assert result.leverage_cap == 10
    assert "effective_score_below_20x_threshold" in result.cap_reasons
