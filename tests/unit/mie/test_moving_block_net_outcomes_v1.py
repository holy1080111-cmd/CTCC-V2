"""Synthetic controls for the offline moving-block research diagnostic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, localcontext

import pytest

from app.mie.validation import moving_block_net_outcomes_v1 as subject
from app.mie.validation.contracts import CostModel
from app.mie.validation.cost_stress_v1 import (
    CostStressObservationV1,
    CostStressPathV1,
    CostStressPlanV1,
)

D = Decimal
START = datetime(2026, 9, 1, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


def _inputs():
    cost_plan = CostStressPlanV1(
        candidate_sha256="a" * 64,
        preregistered_at=START - 2 * MINUTE,
        cost_model=CostModel(
            model_id="offline:synthetic",
            version="v1",
            fee_bps=D("2"),
            funding_bps=D("3"),
            spread_bps=D("4"),
            slippage_bps=D("5"),
            funding_interval_seconds=60,
        ),
        observation_interval_seconds=60,
        cvar_confidence_level=D("0.95"),
        spread_multiplier=D("2"),
        slippage_multiplier=D("2"),
        funding_multiplier=D("2"),
    )
    path = CostStressPathV1(
        plan_sha256=cost_plan.canonical_sha256(),
        candidate_sha256=cost_plan.candidate_sha256,
        source_replay_sha256="b" * 64,
        started_at=START,
        ended_at=START + 16 * MINUTE,
        evaluated_at=START + 17 * MINUTE,
        observations=tuple(
            CostStressObservationV1(
                closed_at=START + (index + 1) * MINUTE,
                decision_at=START + index * MINUTE,
                return_available_at=START + (index + 1) * MINUTE + timedelta(seconds=1),
                asset_return=D("0.01") if index % 2 else D("-0.015"),
                exposure=D(1) if index % 3 else D("-0.5"),
            )
            for index in range(16)
        ),
    )
    bootstrap = subject.MovingBlockPlanV1(
        cost_plan_sha256=cost_plan.canonical_sha256(),
        candidate_sha256=cost_plan.candidate_sha256,
        preregistered_at=START - MINUTE,
        source_window_start=START,
        source_window_end=START + 16 * MINUTE,
        observation_count=16,
        seed_sha256="c" * 64,
        replications=8,
        block_length=4,
        lower_tail_probability=D("0.125"),
        maximum_drawdown_limit=D("0.10"),
    )
    return bootstrap, cost_plan, path


def _arguments(bootstrap, cost_plan, path):
    return {
        "bootstrap_plan_bytes": bootstrap.canonical_json_bytes(),
        "cost_plan_bytes": cost_plan.canonical_json_bytes(),
        "cost_path_bytes": path.canonical_json_bytes(),
        "expected_bootstrap_plan_sha256": bootstrap.canonical_sha256(),
        "expected_cost_plan_sha256": cost_plan.canonical_sha256(),
        "expected_cost_path_sha256": path.canonical_sha256(),
    }


def test_replay_is_byte_exact_under_changed_decimal_context():
    bootstrap, cost_plan, path = _inputs()
    args = _arguments(bootstrap, cost_plan, path)
    first = subject.evaluate_moving_block_v1(**args)
    report = first.contract
    assert report.observation_count == 16 and report.block_length == 4
    assert len(report.replications) == 8
    assert tuple(rep.index for rep in report.replications) == tuple(range(8))
    assert not report.independent_preregistration_verified
    assert not report.source_authenticity_verified
    assert not report.predictive_oos_eligible
    assert not report.economic_oos_accepted and not report.execution_authority
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        second = subject.verify_moving_block_v1(
            first.payload, expected_report_sha256=first.sha256, **args
        )
    assert second.payload == first.payload and second.sha256 == first.sha256


@pytest.mark.parametrize("length", [16, 17])
def test_blocks_preserve_within_block_order_without_wraparound(length):
    positions = subject._sample_indices(
        seed_sha256="c" * 64, replication=0, length=length, block_length=4
    )
    assert len(positions) == length
    assert all(0 <= index < length for index in positions)
    assert all(
        positions[offset : offset + 4]
        == tuple(range(positions[offset], positions[offset] + min(4, length - offset)))
        and positions[offset] <= length - 4
        for offset in range(0, length, 4)
    )
    assert positions == subject._sample_indices(
        seed_sha256="c" * 64, replication=0, length=length, block_length=4
    )


def test_late_or_changed_plan_and_path_refuse_even_with_new_self_hash():
    bootstrap, cost_plan, path = _inputs()
    for invalid in (START, cost_plan.preregistered_at - MINUTE):
        invalid_plan = bootstrap.model_copy(update={"preregistered_at": invalid})
        with pytest.raises(ValueError, match="declared plan or source pin invalid"):
            subject.evaluate_moving_block_v1(
                **_arguments(invalid_plan, cost_plan, path)
            )
    changed = path.model_copy(
        update={
            "observations": (
                path.observations[1],
                path.observations[0],
                *path.observations[2:],
            )
        }
    )
    with pytest.raises(ValueError, match="missing, late, or out of order"):
        subject.evaluate_moving_block_v1(**_arguments(bootstrap, cost_plan, changed))


def test_predeclared_window_or_candidate_cannot_follow_future_path():
    bootstrap, cost_plan, path = _inputs()
    for update in (
        {"source_window_end": START + 17 * MINUTE},
        {"candidate_sha256": "d" * 64},
        {"observation_count": 17},
    ):
        changed = bootstrap.model_copy(update=update)
        with pytest.raises(ValueError, match="declared plan or source pin invalid"):
            subject.evaluate_moving_block_v1(**_arguments(changed, cost_plan, path))


def test_pin_tamper_and_work_bound_refuse():
    bootstrap, cost_plan, path = _inputs()
    args = _arguments(bootstrap, cost_plan, path)
    with pytest.raises(subject.ArtifactVerificationError):
        subject.evaluate_moving_block_v1(
            **{**args, "expected_cost_path_sha256": "d" * 64}
        )
    oversized_block = bootstrap.model_copy(update={"block_length": 9})
    with pytest.raises(ValueError, match="population or work bound invalid"):
        subject.evaluate_moving_block_v1(**_arguments(oversized_block, cost_plan, path))
