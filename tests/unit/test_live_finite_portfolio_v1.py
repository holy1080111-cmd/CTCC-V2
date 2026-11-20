"""Synthetic exact Live finite risk DATA, not account or Micro Live acceptance."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.okx_live import finite_portfolio_v1 as live
from app.trade_qualification import portfolio as risk
from app.trade_qualification.account_ledger_claims_v2 import ExactRatioV1
from tests.unit import test_qualification_portfolio as f

UID = "123456"
EPOCH = f.NOW - timedelta(days=1)


def ratio(value):
    return ExactRatioV1(numerator=str(value), denominator="1")


def stamp(**changes):
    return risk.LiveEvidenceStamp(
        **{
            "environment": "live",
            "account_id": UID,
            "source_sha256": "a" * 64,
            "observed_at": f.NOW,
            "received_at": f.NOW,
            "complete": True,
        }
        | changes
    )


def inputs(
    *, account_changes=None, financial_changes=None, authority_changes=None, **changes
):
    account = live.LiveFinitePortfolioSnapshotV1(
        **{
            "account_id": UID,
            "settlement_currency": "USDT",
            **{name: stamp() for name in f.STAMP_FIELDS},
            "equity": Decimal(1000),
            "available_margin": Decimal(1000),
            "positions": (),
            "pending_reservations": (),
            "position_count": 0,
            "pending_reservation_count": 0,
            "history_start": f.NOW - timedelta(days=8),
            "history_end": f.NOW,
            "original_start_seed": None,
        }
        | (account_changes or {})
    )
    financial = live.LiveFinancialOperandsV1(
        **{
            "account_uid": UID,
            "main_uid": "123455",
            "credential_session_sha256": "b" * 64,
            "source_revision_sha256": "c" * 64,
            "epoch_id": "d" * 64,
            "epoch_policy_sha256": "e" * 64,
            "epoch_started_at": EPOCH,
            "sample_sequence": 1,
            "source_as_of_at": f.NOW,
            "available_at": f.NOW,
            "evaluated_at": f.NOW,
            "daily_window_started_at": f.DAY_START,
            "weekly_window_started_at": f.WEEK_START,
            "daily_net_pnl": ratio(0),
            "weekly_net_pnl": ratio(0),
            "prospective_loss_streak": 0,
            "original_start_seed": None,
            "measured_peak_equity": ratio(1000),
            "measured_current_equity": ratio(1000),
            "measured_peak_observed_at": EPOCH,
            "drawdown_basis": "all_durable_original_samples_in_prospective_live_epoch",
            "actual_continuous_peak": None,
            "position_margin_bucket_usdt": Decimal(300),
            "original_expires_at": f.NOW + timedelta(seconds=5),
            "source_receipt_sha256": "f" * 64,
            "missing_obligations": (),
        }
        | (financial_changes or {})
    )
    authority = risk.LiveRiskAuthority(
        **{
            "stamp": stamp(),
            "environment": "live",
            "live_enabled": True,
            "armed": True,
            "order_writes_allowed": True,
            "one_shot": True,
            "emergency_stop": False,
            "demo_enabled": False,
            "automation_enabled": False,
            "simulated_trading_header": None,
            "credential_session_sha256": "b" * 64,
            "arm_id": "9" * 64,
        }
        | (authority_changes or {})
    )
    claims = live.LiveFinitePortfolioClaimsV1(
        scope=live.LiveFinancialScopeV1(
            environment="live", account_id=UID, settlement_currency="USDT"
        ),
        account=account,
        financial=financial,
        risk_policy=f.policy(drawdown_window_started_at=EPOCH, max_age_seconds=5),
        authority=authority,
    )
    return live.LiveFinitePortfolioInputsV1(
        **{
            "requested_contracts": Decimal(10),
            "requested_leverage": 3,
            "instrument": f.instrument(),
            "claims": claims,
        }
        | changes
    )


def evaluate(value):
    return live.evaluate_live_finite_portfolio_v1(
        value,
        report_id="synthetic-live-finite-risk",
        instrument_id=f.INSTRUMENT,
        direction="long",
        candidate_entry=Decimal(100),
        stop_loss=Decimal(95),
        round_trip_cost_per_base=Decimal("0.1"),
        current_time=f.NOW,
    )


def test_finite_live_risk_preserves_shared_exact_caps_without_a_historical_seed():
    value = inputs()
    result = evaluate(value)
    assert result.passed, result.causes
    assert result.execution_authority is False
    assert value.claims.account.original_start_seed is None
    assert not hasattr(value.claims.account, "loss_history")
    assert not hasattr(value.claims.account, "loss_streak_at_history_start")
    assert result.daily_loss_pct == result.weekly_loss_pct == result.drawdown_pct == 0
    assert result.max_loss_amount == Decimal("0.51")


@pytest.mark.parametrize(
    "field,value,cause",
    (
        ("daily_net_pnl", ratio(-100), "daily_loss_limit_reached"),
        ("weekly_net_pnl", ratio(-200), "weekly_loss_limit_reached"),
        ("prospective_loss_streak", 3, "consecutive_loss_limit_reached"),
        ("daily_net_pnl", None, "financial_net_loss_window_unknown"),
        ("weekly_net_pnl", None, "financial_net_loss_window_unknown"),
        ("prospective_loss_streak", None, "financial_lifecycle_streak_unknown"),
        ("measured_current_equity", ratio(999), "live_finite_measured_hwm_invalid"),
        (
            "missing_obligations",
            ("unresolved_source_history",),
            "unresolved_source_history",
        ),
    ),
)
def test_separate_historical_cash_losses_and_prospective_unknowns_fail_closed(
    field, value, cause
):
    result = evaluate(inputs(financial_changes={field: value}))
    assert not result.passed and cause in result.causes
    assert result.execution_authority is False


def test_structural_risk_cap_is_still_half_percent():
    result = evaluate(inputs(requested_contracts=Decimal(100)))
    assert not result.passed and "trade_risk_limit_exceeded" in result.causes


def test_aggregate_margin_is_still_sixty_percent():
    position = f.position(notional=Decimal(1000), margin=Decimal(600))
    result = evaluate(
        inputs(account_changes={"positions": (position,), "position_count": 1})
    )
    assert not result.passed and "portfolio_margin_limit_exceeded" in result.causes


@pytest.mark.parametrize(
    "field,value",
    (
        ("original_start_seed", 0),
        ("actual_continuous_peak", ratio(1000)),
        ("account_uid", "987654"),
        ("credential_session_sha256", "1" * 64),
        ("epoch_started_at", EPOCH - timedelta(days=1)),
    ),
)
def test_fake_seed_or_changed_account_epoch_or_credential_never_rebinds(field, value):
    with pytest.raises((ValidationError, ValueError)):
        inputs(financial_changes={field: value})


@pytest.mark.parametrize(
    "field,value,cause",
    (
        ("armed", False, "live_authority_denied"),
        ("one_shot", False, "live_authority_denied"),
        ("emergency_stop", True, "emergency_stop_active"),
        ("demo_enabled", True, "live_authority_denied"),
    ),
)
def test_controls_still_fail_closed(field, value, cause):
    result = evaluate(inputs(authority_changes={field: value}))
    assert not result.passed and cause in result.causes


def test_unknown_peak_and_extra_fields_cannot_hide_before_serialization():
    result = evaluate(
        inputs(
            financial_changes={
                "measured_peak_equity": None,
                "measured_peak_observed_at": None,
            }
        )
    )
    assert not result.passed and "live_finite_measured_hwm_invalid" in result.causes
    value = inputs()
    value.claims.financial.__dict__["caller_passed"] = True
    with pytest.raises(ValueError, match="^live_finite_portfolio_claims_invalid$"):
        evaluate(value)


def test_demo_and_legacy_live_snapshots_are_not_accepted():
    with pytest.raises((ValidationError, ValueError)):
        inputs(claims=f.account())
    with pytest.raises((ValidationError, ValueError)):
        inputs(account_changes={"balance_stamp": f.stamp()})
