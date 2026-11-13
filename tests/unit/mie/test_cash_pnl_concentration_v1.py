"""Synthetic cash-PnL concentration scenarios with no OOS claim."""

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import ROUND_UP, Decimal, localcontext

import pytest

from app.mie.validation import cash_pnl_concentration_v1 as subject
from tests.unit.mie.test_economic_descriptive_metrics import T0, closed_trade, outcomes

D = Decimal


def _cohort(plan, trades):
    return subject.ConcentrationCohortV1(
        plan_sha256=plan.canonical_sha256(),
        candidate_sha256=plan.candidate_sha256,
        source_replay_sha256="b" * 64,
        trades=tuple(subject.ConcentrationTradeV1(**asdict(row)) for row in trades),
    )


def _args(plan, cohort):
    return {
        "plan_bytes": plan.canonical_json_bytes(),
        "cohort_bytes": cohort.canonical_json_bytes(),
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_cohort_sha256": cohort.canonical_sha256(),
    }


@pytest.fixture
def plan():
    return subject.ConcentrationPlanV1(
        candidate_sha256="a" * 64,
        preregistered_at=T0 - timedelta(days=1),
        window_start=T0,
        window_end=T0 + timedelta(hours=5),
        as_of=T0 + timedelta(hours=6),
        currency="USDT",
        symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"),
        regimes=("bear", "bull", "range"),
        top_k=1,
    )


def test_top_winner_and_group_exclusions_recompute_exact_cash_metrics(plan):
    report = subject.evaluate_cash_pnl_concentration_v1(
        **_args(plan, _cohort(plan, outcomes()))
    ).contract
    assert report.all_trades.net_expectancy_per_trade == D("2.5")
    assert report.all_trades.profit_factor == D("3")
    assert report.removed_winner_ids == ("trade-1",)
    assert report.without_top_winners is not None
    assert report.without_top_winners.sample_count == 3
    assert report.without_top_winners.net_profit == D("-2")
    assert report.without_top_winners.profit_factor == D("0.6")
    assert report.by_direction[0].summary.average_win == D("7.5")
    assert report.by_direction[1].summary.average_loss_magnitude == D("5")
    symbol = {item.label: item for item in report.without_each_symbol}
    regime = {item.label: item for item in report.without_each_regime}
    assert symbol["BTC-USDT-SWAP"].removed_count == 2
    assert symbol["BTC-USDT-SWAP"].summary.net_profit == D("3")
    assert symbol["BTC-USDT-SWAP"].summary.profit_factor is None
    assert symbol["SOL-USDT-SWAP"].removed_count == 0
    assert symbol["SOL-USDT-SWAP"].summary is report.by_symbol[2].summary is None
    assert regime["bull"].summary.net_profit == D("-2")
    assert report.independent_preregistration_verified is False
    assert report.source_authenticity_verified is False
    assert report.economic_oos_accepted is report.execution_authority is False


def test_ties_and_empty_slices_are_explicit(plan):
    first = closed_trade(
        1, D("12"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
    )
    second = closed_trade(
        2, D("12"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
    )
    report = subject.evaluate_cash_pnl_concentration_v1(
        **_args(plan, _cohort(plan, (first, second)))
    ).contract
    assert report.removed_winner_ids == ("trade-1",)
    assert report.without_each_symbol[0].removed_count == 2
    assert report.without_each_symbol[0].summary is None
    assert report.all_trades.profit_factor is None
    assert report.by_direction[1].summary is None
    one = subject.evaluate_cash_pnl_concentration_v1(
        **_args(plan, _cohort(plan, (first,)))
    ).contract
    assert one.without_top_winners is None
    loss = outcomes()[1]
    no_winner = subject.evaluate_cash_pnl_concentration_v1(
        **_args(plan, _cohort(plan, (loss,)))
    ).contract
    assert no_winner.removed_winner_ids == ()
    assert no_winner.without_top_winners == no_winner.all_trades
    assert no_winner.all_trades.average_win is None


def test_replay_is_canonical_under_caller_context_and_rehashed_report_refused(plan):
    args = _args(plan, _cohort(plan, outcomes()))
    result = subject.evaluate_cash_pnl_concentration_v1(**args)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        replay = subject.verify_cash_pnl_concentration_v1(
            result.payload, expected_report_sha256=result.sha256, **args
        )
    assert replay == result
    wrong = result.contract.model_copy(update={"source_replay_sha256": "c" * 64})
    with pytest.raises(
        subject.ArtifactVerificationError, match="report differs from replay"
    ):
        subject.verify_cash_pnl_concentration_v1(
            wrong.canonical_json_bytes(),
            expected_report_sha256=wrong.canonical_sha256(),
            **args,
        )


@pytest.mark.parametrize(
    "change",
    [
        "availability",
        "window",
        "symbol",
        "regime",
        "currency",
        "reordered",
        "duplicate",
    ],
)
def test_changed_cohort_refused_even_with_new_cohort_digest(plan, change):
    first, second, *rest = outcomes()
    changes = {
        "availability": {"available_at": plan.as_of + timedelta(microseconds=1)},
        "window": {"closed_at": plan.window_start - timedelta(seconds=1)},
        "symbol": {"symbol": "OUTSIDE"},
        "regime": {"regime": "OUTSIDE"},
        "currency": {"currency": "EUR"},
        "duplicate": {"trade_id": second.trade_id},
    }
    if change == "reordered":
        rows = (second, first, *rest)
    else:
        rows = (replace(first, **changes[change]), second, *rest)
    with pytest.raises(ValueError):
        subject.evaluate_cash_pnl_concentration_v1(**_args(plan, _cohort(plan, rows)))


def test_top_k_is_pinned_and_chronology_is_validated(plan):
    cohort = _cohort(plan, outcomes())
    changed = plan.model_copy(update={"top_k": 2})
    with pytest.raises(ValueError, match="plan or candidate pin differs"):
        subject.evaluate_cash_pnl_concentration_v1(**_args(changed, cohort))
    late = plan.model_copy(update={"preregistered_at": plan.window_start})
    with pytest.raises(ValueError, match="plan chronology differs"):
        subject.ConcentrationPlanV1.model_validate(late)
