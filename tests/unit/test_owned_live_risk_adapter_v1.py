"""Synthetic DATA adaptation; no source enrollment, control or order permit."""

from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.database.repositories import native_live_financial_epoch_v1 as facts_module
from app.domain.source_primitives import canonical
from app.okx_live import final_recheck_v1 as final
from app.okx_live import owned_risk_adapter_v1 as subject
from app.trade_qualification import portfolio
from tests.unit import test_qualification_portfolio as f


def fixture(*, cashflows=None):
    now = f.NOW
    facts = facts_module.LiveFinancialWindowFactsV1(
        "a" * 64,
        "123456",
        "123450",
        "b" * 64,
        "c" * 64,
        1,
        now - timedelta(days=1),
        now - timedelta(seconds=1),
        now - timedelta(microseconds=100),
        now - timedelta(seconds=2),
        now - timedelta(days=8),
        now - timedelta(seconds=1),
        Fraction(),
        Fraction(),
        0,
        Fraction(1000),
        Fraction(1000),
        now - timedelta(days=1),
        Fraction(1000),
        (),
        () if cashflows is None else cashflows,
        (),
        canonical({"synthetic": True}),
    )
    instrument = subject.LiveInstrumentDataV1(
        f.instrument(
            observed_at=now - timedelta(seconds=1),
            received_at=now - timedelta(microseconds=100),
        ),
        Decimal("0.1"),
        Decimal("0.25"),
        1,
        "net",
    )
    policy = f.policy(
        max_age_seconds=5,
        drawdown_window_started_at=facts.epoch_started_at,
        risk_per_trade_pct=Decimal("0.005"),
        max_portfolio_risk_pct=Decimal("0.01"),
        max_portfolio_margin_pct=Decimal("0.60"),
        max_weekly_loss_pct=Decimal("0.02"),
        max_daily_loss_pct=Decimal("0.01"),
    )
    policy = SimpleNamespace(portfolio=policy)
    row = {
        "environment": "live",
        "account_uid": facts.account_uid,
        "main_uid": facts.main_uid,
        "credential_session_sha256": facts.credential_session_sha256,
        "source_revision_sha256": facts.source_revision_sha256,
        "armed": True,
        "emergency_stop": False,
        "arm_expires_at": now + timedelta(seconds=30),
        "arm_id": "d" * 64,
    }
    local = portfolio.LiveEvidenceStamp(
        environment="live",
        account_id=facts.account_uid,
        complete=True,
        source_sha256="e" * 64,
        observed_at=now - timedelta(microseconds=100),
        received_at=now - timedelta(microseconds=50),
    )
    return {
        "facts": facts,
        "instrument": instrument,
        "policy": policy,
        "control_row": row,
        "evaluated_at": now,
        "deadline": now + timedelta(seconds=4),
        "local_stamp": local,
        "local_active_count": 0,
    }


def test_financial_adapter_preserves_exact_original_sample_scope_and_no_fake_seed():
    args = fixture()
    result = subject.live_finite_inputs_data_v1(**args)
    assert result.claims.account.original_start_seed is None
    assert result.claims.financial.actual_continuous_peak is None
    assert result.claims.financial.measured_current_equity.numerator == "1000"
    assert result.claims.financial.source_as_of_at == args["facts"].observed_at
    assert result.claims.financial.available_at == args["facts"].available_at
    assert (
        result.claims.authority.environment == "live"
        and result.claims.authority.simulated_trading_header is None
    )


def test_clock_advance_rewindows_positive_credit_expiry_and_loss_limit_without_changing_sources():
    now = f.NOW
    credit = facts_module.LiveRiskCashflowDataV1(
        now - timedelta(days=7) + timedelta(microseconds=500),
        Fraction(50),
        "synthetic_signed_fill",
        "1" * 64,
    )
    loss = facts_module.LiveRiskCashflowDataV1(
        now - timedelta(days=1), Fraction(-25), "synthetic_signed_fill", "2" * 64
    )
    args = fixture(cashflows=(credit, loss))
    before = subject.live_finite_inputs_data_v1(**args)
    after = subject.live_finite_inputs_data_v1(
        **(args | {"evaluated_at": now + timedelta(milliseconds=1)})
    )
    assert before.claims.financial.weekly_net_pnl.numerator == "25"
    assert after.claims.financial.weekly_net_pnl.numerator == "-25"
    assert (
        before.claims.financial.source_revision_sha256
        == after.claims.financial.source_revision_sha256
    )
    assert final.next_cash_window_change_v1(
        args["facts"], evaluated_at=now
    ) == credit.at + timedelta(days=7, microseconds=1)
    from app.okx_live.finite_portfolio_v1 import evaluate_live_finite_portfolio_v1

    def evaluate(value, at):
        return evaluate_live_finite_portfolio_v1(
            value,
            report_id="synthetic-final-clock",
            instrument_id=f.INSTRUMENT,
            direction="long",
            candidate_entry=Decimal(100),
            stop_loss=Decimal(95),
            round_trip_cost_per_base=Decimal("0.1"),
            current_time=at,
        )

    assert evaluate(before, now).passed
    failed = evaluate(after, now + timedelta(milliseconds=1))
    assert not failed.passed and "weekly_loss_limit" in " ".join(failed.causes)


@pytest.mark.parametrize(
    "change",
    [
        {"local_active_count": 1},
        {"local_active_count": False},
        {"evaluated_at": f.NOW + timedelta(days=2)},
        {"deadline": f.NOW},
    ],
)
def test_unknown_local_exposure_expiry_and_uncovered_history_are_refused(change):
    with pytest.raises(ValueError):
        subject.live_finite_inputs_data_v1(**(fixture() | change))


def test_nonterminating_ratio_cannot_be_rounded_into_source_equity():
    with pytest.raises(ValueError):
        subject._exact_decimal(Fraction(1, 3))
    assert subject._exact_decimal(Fraction(1, 8)) == Decimal("0.125")
