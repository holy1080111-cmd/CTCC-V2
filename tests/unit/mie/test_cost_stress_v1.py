"""Cost stress replay fixtures. No protected data or external sources."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, localcontext

import pytest

from app.mie.validation import cost_stress_v1
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.contracts import CostModel
from app.mie.validation.cost_stress_v1 import (
    CostStressObservationV1,
    CostStressPathV1,
    CostStressPlanV1,
    evaluate_cost_stress_v1,
    verify_cost_stress_v1,
)
from app.mie.validation.costs import evaluate_costed_return_path

D = Decimal
START = datetime(2026, 9, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


@pytest.fixture
def stress_case():
    plan = CostStressPlanV1(
        candidate_sha256="a" * 64,
        preregistered_at=START - MINUTE,
        cost_model=CostModel(
            model_id="offline:fixture",
            version="v1",
            fee_bps=D("2"),
            funding_bps=D("3"),
            spread_bps=D("4"),
            slippage_bps=D("5"),
            funding_interval_seconds=60,
        ),
        observation_interval_seconds=60,
        cvar_confidence_level=D("0.9"),
        spread_multiplier=D("3"),
        slippage_multiplier=D("4"),
        funding_multiplier=D("5"),
    )
    path = CostStressPathV1(
        plan_sha256=plan.canonical_sha256(),
        candidate_sha256=plan.candidate_sha256,
        source_replay_sha256="b" * 64,
        started_at=START,
        ended_at=START + 3 * MINUTE,
        evaluated_at=START + 4 * MINUTE,
        observations=tuple(
            CostStressObservationV1(
                closed_at=START + (index + 1) * MINUTE,
                decision_at=START + index * MINUTE,
                return_available_at=START + (index + 1) * MINUTE + timedelta(seconds=1),
                asset_return=asset_return,
                exposure=exposure,
            )
            for index, (asset_return, exposure) in enumerate(
                ((D("0.02"), D(1)), (D("-0.01"), D(1)), (D("0.03"), D("-0.5")))
            )
        ),
    )
    return plan, path


def arguments(plan, path):
    return {
        "plan_bytes": plan.canonical_json_bytes(),
        "path_bytes": path.canonical_json_bytes(),
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_path_sha256": path.canonical_sha256(),
    }


def test_fixed_path_six_scenarios_and_readback(stress_case):
    plan, path = stress_case
    inputs = arguments(plan, path)
    actual = evaluate_cost_stress_v1(**inputs)
    report = actual.contract
    base, one_half, double, spread, slippage, funding = report.scenarios
    assert tuple(item.scenario for item in report.scenarios) == (
        "cost_1x",
        "cost_1_5x",
        "cost_2x",
        "spread_widening",
        "slippage_stress",
        "funding_stress",
    )
    independent = evaluate_costed_return_path(
        [item.asset_return for item in path.observations],
        [item.exposure for item in path.observations],
        cost_model=plan.cost_model,
        observation_interval_seconds=60,
        cvar_confidence_level=plan.cvar_confidence_level,
    )
    assert base.net_compound_return == independent.net_compound_return
    assert base.maximum_drawdown == independent.maximum_drawdown
    assert base.cvar_loss == independent.cvar_loss
    assert all(item.turnover == base.turnover for item in report.scenarios)
    assert all(
        item.gross_compound_return == base.gross_compound_return
        for item in report.scenarios
    )
    assert one_half.total_cost == base.total_cost * D("1.5")
    assert double.total_cost == base.total_cost * 2
    assert (
        base.net_compound_return
        > one_half.net_compound_return
        > double.net_compound_return
    )
    for actual_case, field, multiplier in (
        (spread, "spread_cost", 3),
        (slippage, "slippage_cost", 4),
        (funding, "funding_cost", 5),
    ):
        assert getattr(actual_case, field) == getattr(base, field) * multiplier
        assert actual_case.total_cost == base.total_cost + getattr(base, field) * (
            multiplier - 1
        )
    assert not report.source_authenticity_verified
    assert not report.economic_oos_accepted and not report.execution_authority
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        assert (
            verify_cost_stress_v1(
                actual.payload, expected_report_sha256=actual.sha256, **inputs
            ).payload
            == actual.payload
        )


@pytest.mark.parametrize("mutation", ["late_decision", "late_return", "gap", "reorder"])
def test_stress_refuses_incomplete_or_noncausal_path(stress_case, mutation):
    plan, path = stress_case
    first, second, third = path.observations
    match mutation:
        case "late_decision":
            first = first.model_copy(
                update={"decision_at": START + timedelta(seconds=1)}
            )
        case "late_return":
            first = first.model_copy(
                update={"return_available_at": path.evaluated_at + MINUTE}
            )
        case "gap":
            second = third
        case "reorder":
            first, second = second, first
    changed = path.model_copy(update={"observations": (first, second, third)})
    with pytest.raises(ValueError, match="missing, late, or out of order"):
        evaluate_cost_stress_v1(**arguments(plan, changed))


def test_stress_requires_prewindow_plan_and_candidate_match(stress_case):
    plan, path = stress_case
    changed = plan.model_copy(update={"preregistered_at": START})
    joined = path.model_copy(update={"plan_sha256": changed.canonical_sha256()})
    with pytest.raises(ValueError, match="plan, candidate, or window mismatch"):
        evaluate_cost_stress_v1(**arguments(changed, joined))
    with pytest.raises(ValueError, match="plan, candidate, or window mismatch"):
        evaluate_cost_stress_v1(
            **arguments(plan, path.model_copy(update={"candidate_sha256": "c" * 64}))
        )


def test_stress_rejects_self_hashed_changed_report(stress_case):
    plan, path = stress_case
    inputs = arguments(plan, path)
    result = evaluate_cost_stress_v1(**inputs)
    first, *remaining = result.contract.scenarios
    forged = result.contract.model_copy(
        update={
            "scenarios": (
                first.model_copy(update={"net_compound_return": D("0")}),
                *remaining,
            )
        }
    )
    with pytest.raises(ArtifactVerificationError, match="does not match replay"):
        verify_cost_stress_v1(
            forged.canonical_json_bytes(),
            expected_report_sha256=forged.canonical_sha256(),
            **inputs,
        )
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        evaluate_cost_stress_v1(**(inputs | {"expected_path_sha256": "f" * 64}))


def test_report_must_fit_its_own_readback_limit(stress_case, monkeypatch):
    inputs = arguments(*stress_case)
    report = evaluate_cost_stress_v1(**inputs)
    monkeypatch.setattr(cost_stress_v1, "_MAX_REPORT_BYTES", len(report.payload) - 1)
    with pytest.raises(ArtifactVerificationError, match="exceeds retained size limit"):
        evaluate_cost_stress_v1(**inputs)
