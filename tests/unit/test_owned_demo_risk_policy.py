"""Synthetic risk-config identity checks; no account or exchange access."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from app.config.settings import Settings
from app.trade_qualification import demo_control as control
from app.trade_qualification.owned_demo_risk_policy import (
    OwnedRiskPolicyError,
    freeze_demo_risk_config,
    guard_owned_risk_policy_identity,
)
from tests.unit.test_qualification_portfolio import policy as synthetic_policy


def configured(**changes):
    return Settings(
        _env_file=None,
        okx_demo_score_risk_enabled=True,
        okx_demo_capital_bucket_enabled=True,
        okx_demo_structural_dynamic_leverage_enabled=True,
        okx_demo_continuous_session_enabled=True,
        okx_demo_require_protection=True,
        okx_demo_max_open_positions=2,
        okx_demo_max_leverage=20,
        okx_demo_trade_cooldown_seconds=0,
        **changes,
    )


def pinned(identity):
    return control.acquire(
        None,
        scope=control.ControlScope("demo", "123456789"),
        owner_sha256=control.owner_binding(b"S" * 32),
        pins=control.ControlPins(identity.sha256, "2" * 64, "3" * 64),
        now=datetime(2026, 10, 8, tzinfo=UTC),
    )


def conservative_policy(**changes):
    values = {
        "risk_per_trade_pct": D("0.005"),
        "max_portfolio_risk_pct": D("0.01"),
        "max_portfolio_margin_pct": D("0.60"),
        "max_daily_loss_pct": D("0.01"),
        "max_consecutive_losses": 3,
        "max_open_positions": 2,
        "max_leverage": 20,
    }
    return synthetic_policy(**(values | changes))


def test_allowlisted_identity_is_deterministic_secret_free_and_denied():
    first = configured(
        okx_demo_api_key="SYNTHETIC-KEY-ONLY",
        okx_demo_api_secret="SYNTHETIC-SECRET-ONLY",
        okx_demo_api_passphrase="SYNTHETIC-PASSPHRASE-ONLY",
    )
    second = configured()
    record = freeze_demo_risk_config(first)
    assert record == freeze_demo_risk_config(second)
    assert record.admission == "DENY" and record.execution_authority is False
    assert "SYNTHETIC-" not in record.canonical_json
    assert "123456789" not in record.canonical_json
    assert "api_key" not in record.canonical_json
    assert "secret" not in record.canonical_json
    assert "SYNTHETIC-" not in repr(record)


def test_pin_and_conservative_original_policy_compare_but_grant_no_authority():
    settings = configured()
    identity = freeze_demo_risk_config(settings)
    state = pinned(identity)
    assert state.arm_intent_current(datetime(2026, 10, 8, tzinfo=UTC)) is False
    result = guard_owned_risk_policy_identity(settings, state, conservative_policy())
    assert result == identity
    assert result.admission == "DENY" and result.execution_authority is False


@pytest.mark.parametrize(
    "field,value,reason",
    (
        ("okx_demo_structural_dynamic_leverage_enabled", False, "guard_disabled"),
        ("okx_demo_capital_bucket_enabled", False, "guard_disabled"),
        ("okx_demo_require_protection", False, "guard_disabled"),
        ("okx_demo_structural_low_risk_pct", D("0.0051"), "cap_weakened"),
        ("okx_demo_portfolio_max_risk_pct", D("0.011"), "cap_weakened"),
        ("okx_demo_portfolio_max_margin_pct", D("0.61"), "cap_weakened"),
        ("okx_demo_position_margin_bucket_usdt", D(301), "cap_weakened"),
        ("okx_demo_daily_loss_limit_pct", D("0.011"), "cap_weakened"),
        ("okx_demo_structural_high_leverage_cap", 9, "tier_or_guard"),
        ("okx_demo_structural_score_extreme_min", 97, "tier_or_guard"),
        ("okx_demo_automation_max_consecutive_losses", 4, "tier_or_guard"),
        ("okx_demo_max_open_positions", 11, "tier_or_guard"),
        ("okx_demo_structural_min_net_risk_reward", D("1.9"), "20x_quality"),
        ("okx_demo_structural_round_trip_fee_bps", D(0), "20x_quality"),
        ("okx_demo_structural_20x_min_reliability", D("0.64"), "20x_quality"),
        ("okx_demo_structural_20x_max_instability", D("0.21"), "20x_quality"),
    ),
)
def test_post_validation_config_drift_fails_closed(field, value, reason):
    settings = configured()
    # Settings can be mutated after Pydantic construction; freeze must recheck.
    setattr(settings, field, value)
    with pytest.raises(OwnedRiskPolicyError, match=reason):
        freeze_demo_risk_config(settings)


@pytest.mark.parametrize("field,value", (("missing", None), ("wrong_type", 0.005)))
def test_missing_or_malformed_source_field_is_not_defaulted(field, value):
    settings = configured()
    if field == "missing":
        settings.__dict__.pop("okx_demo_structural_low_risk_pct")
    else:
        settings.okx_demo_structural_low_risk_pct = value
    with pytest.raises(OwnedRiskPolicyError):
        freeze_demo_risk_config(settings)


def test_tighter_config_has_new_digest_and_old_control_pin_rejects_it():
    settings = configured()
    before = freeze_demo_risk_config(settings)
    state = pinned(before)
    settings.okx_demo_position_margin_bucket_usdt = D(250)
    after = freeze_demo_risk_config(settings)
    assert after.sha256 != before.sha256
    with pytest.raises(OwnedRiskPolicyError, match="control_config_conflict"):
        guard_owned_risk_policy_identity(settings, state, conservative_policy())


@pytest.mark.parametrize(
    "field,value",
    (
        ("risk_per_trade_pct", D("0.006")),
        ("max_portfolio_risk_pct", D("0.02")),
        ("max_portfolio_margin_pct", D("0.7")),
        ("max_daily_loss_pct", D("0.02")),
        ("max_consecutive_losses", 4),
        ("max_open_positions", 3),
        ("max_leverage", 21),
    ),
)
def test_original_qualification_policy_cannot_exceed_owned_caps(field, value):
    settings = configured()
    state = pinned(freeze_demo_risk_config(settings))
    with pytest.raises(OwnedRiskPolicyError, match="qualification_cap_conflict"):
        guard_owned_risk_policy_identity(
            settings, state, conservative_policy(**{field: value})
        )


def test_existing_generous_synthetic_policy_cannot_be_promoted():
    settings = configured()
    state = pinned(freeze_demo_risk_config(settings))
    with pytest.raises(OwnedRiskPolicyError, match="qualification_cap_conflict"):
        guard_owned_risk_policy_identity(settings, state, synthetic_policy())
