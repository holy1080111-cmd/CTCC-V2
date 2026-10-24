"""Original synthetic source replay and exact algebra, never native proof."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from fractions import Fraction

import pytest

from app.trade_qualification import account_risk_windows_v1 as windows
from app.trade_qualification import portfolio
from tests.unit import test_account_history_risk_v8 as original
from tests.unit import test_qualification_portfolio as claims


def fill(identity, at, gross, fee):
    gross, fee = Fraction(gross), Fraction(fee)
    return {
        "identity_sha256": identity,
        "execution_at": at.isoformat(),
        # Different source-generation dates must not affect membership.
        "source_ts_at": (at + timedelta(days=1)).isoformat(),
        "gross_fill_pnl": windows._ratio(gross),
        "signed_fee": windows._ratio(fee),
        "observed_fill_cashflow": windows._ratio(gross + fee),
    }


def test_cross_midnight_run_net_is_not_assigned_wholesale_to_close_day():
    day = datetime(2026, 10, 24, tzinfo=UTC)
    rows = [
        fill("open", day - timedelta(minutes=10), 0, -1),
        fill("partial", day - timedelta(minutes=1), 100, -1),
        fill("close", day + timedelta(minutes=1), -50, -1),
    ]
    daily, weekly = windows._fill_window_totals(
        rows,
        day_start=day,
        week_start=day - timedelta(days=7),
        ended_at=day + timedelta(minutes=2),
    )
    assert daily == -51 and weekly == 47
    assert max(Fraction(0), -daily) == 51
    assert max(Fraction(0), -weekly) == 0


def test_daily_includes_midnight_weekly_excludes_exact_left_boundary_and_both_include_end():
    day = datetime(2026, 10, 24, tzinfo=UTC)
    left, end = day - timedelta(days=7), day + timedelta(hours=1)
    rows = [
        fill("week-left", left, -100, 0),
        fill("week-after", left + timedelta(milliseconds=1), -2, 0),
        fill("day-before", day - timedelta(milliseconds=1), -3, 0),
        fill("day-start", day, -5, 0),
        fill("end", end, -7, 0),
        fill("after-end", end + timedelta(milliseconds=1), -1000, 0),
    ]
    assert windows._fill_window_totals(
        rows,
        day_start=day,
        week_start=left,
        ended_at=end,
    ) == (-12, -17)


def test_signed_rebate_and_net_offset_are_exact_under_host_decimal_context():
    day = datetime(2026, 10, 24, tzinfo=UTC)
    rows = [
        fill("cost", day, "-0.00000000000000000001", "-0.2"),
        fill("rebate", day, "0.2", "0.1"),
    ]
    with localcontext(Context(prec=2)):
        daily, weekly = windows._fill_window_totals(
            rows,
            day_start=day,
            week_start=day - timedelta(days=7),
            ended_at=day,
        )
    assert daily == weekly == Fraction("0.09999999999999999999")


def test_duplicate_identity_or_forged_fill_component_cannot_be_summed():
    day = datetime(2026, 10, 24, tzinfo=UTC)
    row = fill("same", day, -1, -1)
    args = {"day_start": day, "week_start": day - timedelta(days=7), "ended_at": day}
    with pytest.raises(windows.AccountRiskWindowsError, match="duplicate_fill"):
        windows._fill_window_totals([row, row], **args)
    row["observed_fill_cashflow"] = windows._ratio(Fraction(0))
    with pytest.raises(windows.AccountRiskWindowsError, match="component_conflict"):
        windows._fill_window_totals([row], **args)


def test_completed_lifecycle_zero_preserves_streak_and_unknown_never_becomes_seed_zero():
    assert windows._advance_loss_streak(2, [Fraction(0)]) == 2
    assert windows._advance_loss_streak(2, [Fraction(-1), Fraction(0)]) == 3
    assert windows._advance_loss_streak(2, [Fraction(1), Fraction(-1)]) == 1
    assert windows._advance_loss_streak(None, [Fraction(1)]) is None
    assert windows._advance_loss_streak(2, [None]) is None
    with pytest.raises(windows.AccountRiskWindowsError, match="seed_invalid"):
        windows._advance_loss_streak(False, [])


async def original_windows(monkeypatch, *, checked_at=None, end=None):
    sources, arguments = await original.inputs(monkeypatch)
    at = original.NOW + timedelta(seconds=2000) if checked_at is None else checked_at
    arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"position_source", "funding_sources"}
    }
    ended_at = original.NOW + timedelta(seconds=1200) if end is None else end
    arguments["window"] = windows.observed.ExecutionWindow(
        min(at - timedelta(days=7), ended_at - timedelta(days=1)),
        ended_at,
    )
    return windows.derive_account_risk_windows_v1(
        sources,
        **arguments,
        checked_at=at,
    )


@pytest.mark.asyncio
async def test_real_original_replay_retains_mirrors_and_unknown_financial_obligations(
    monkeypatch,
):
    result = await original_windows(monkeypatch)
    data = json.loads(result.receipt_json)
    assert data["original_fill_cashflows"]
    assert data["retained_bills"]
    assert data["retained_source_index"]
    assert (
        data["daily_net_pnl"] is data["weekly_net_pnl"] is data["loss_streak"] is None
    )
    assert {
        "account_funding_accrual_attribution_unproven",
        "historical_native_prefix_unproven",
        "loss_streak_seed_unknown",
        "historical_native_hwm_unknown",
    } <= set(data["missing_obligations"])
    assert (
        data["account_complete"]
        is data["account_revision_published"]
        is data["execution_authority"]
        is False
    )
    assert data["admission"] == "DENY"
    with pytest.raises(windows.AccountRiskWindowsError, match="not_constructible"):
        windows.AccountRiskWindowsV1()
    with pytest.raises(windows.AccountRiskWindowsError, match="replay_required"):
        windows._checked_result(object.__new__(windows.AccountRiskWindowsV1))
    with pytest.raises(windows.AccountRiskWindowsError, match="current_source_invalid"):
        portfolio.evaluate_native_account_risk_windows_v1(
            result, current_account_operands=None
        )


@pytest.mark.asyncio
async def test_history_ending_before_new_day_is_unknown_not_known_empty_zero(
    monkeypatch,
):
    checked = (original.NOW + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await original_windows(monkeypatch, checked_at=checked)
    assert json.loads(result.receipt_json)["daily_fill_pnl"] is None


@pytest.mark.asyncio
async def test_history_ending_before_rolling_left_boundary_has_no_known_weekly_zero(
    monkeypatch,
):
    result = await original_windows(
        monkeypatch,
        checked_at=original.NOW + timedelta(days=8),
    )
    data = json.loads(result.receipt_json)
    assert data["daily_fill_pnl"] is data["weekly_fill_pnl"] is None


@pytest.mark.asyncio
async def test_original_source_unavailable_at_decision_cutoff_cannot_be_retroactively_used(
    monkeypatch,
):
    with pytest.raises(windows.AccountRiskWindowsError, match="unavailable_at_cutoff"):
        await original_windows(
            monkeypatch,
            checked_at=original.NOW + timedelta(seconds=1000),
            end=original.NOW + timedelta(seconds=900),
        )


def portfolio_arguments(result):
    facts = windows._checked_result(result)
    at, end, uid = facts.checked_at, facts.ended_at, facts.scope.account_id
    stamp = claims.stamp(account_id=uid, observed_at=at, received_at=at)
    account = claims.account(
        account_id=uid,
        balance_stamp=stamp,
        positions_stamp=stamp,
        reservations_stamp=stamp,
        history_stamp=claims.stamp(account_id=uid, observed_at=end, received_at=at),
        history_start=facts.started_at,
        history_end=end,
        peak_window_started_at=at - timedelta(days=30),
        peak_observed_at=facts.started_at,
        loss_streak_at_history_start=2048,
    )
    return {
        "report_id": claims.REPORT,
        "instrument_id": claims.INSTRUMENT,
        "direction": "long",
        "candidate_entry": Decimal(100),
        "stop_loss": Decimal(95),
        "round_trip_cost_per_base": Decimal("0.1"),
        "requested_contracts": Decimal(1000),
        "requested_leverage": 10,
        "instrument": claims.instrument(observed_at=at, received_at=at),
        "account": account,
        "policy": claims.policy(
            max_age_seconds=86400, drawdown_window_started_at=at - timedelta(days=30)
        ),
        "authority": claims.authority(stamp=stamp),
        "current_time": at,
    }


@pytest.mark.asyncio
async def test_additive_adopter_runs_shared_candidate_caps_without_borrowing_claim_streak(
    monkeypatch,
):
    result = await original_windows(monkeypatch)
    arguments = portfolio_arguments(result)
    decision = portfolio.evaluate_portfolio_with_risk_windows_v2(
        financial_windows=result,
        **arguments,
    )
    assert decision.passed is decision.execution_authority is False
    assert "trade_risk_limit_exceeded" in decision.causes
    assert "financial_net_loss_window_unknown" in decision.causes
    assert "financial_lifecycle_streak_unknown" in decision.causes
    assert "consecutive_loss_limit_reached" not in decision.causes
    assert decision.risk_pct == Decimal("0.051")
    assert decision.daily_loss_pct is decision.weekly_loss_pct is None
    legacy = portfolio.evaluate_portfolio(**arguments)
    assert "consecutive_loss_limit_reached" in legacy.causes


def test_legacy_entry_point_has_no_financial_keyword_override():
    with pytest.raises(TypeError):
        portfolio.evaluate_portfolio(financial_windows=object())
