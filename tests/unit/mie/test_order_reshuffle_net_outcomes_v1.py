"""Synthetic offline checks of permutation stress and exact readback."""

from __future__ import annotations

from decimal import ROUND_UP, Decimal, localcontext

import pytest

from app.mie.validation import order_reshuffle_net_outcomes_v1 as subject
from app.mie.validation.costs import _maximum_drawdown
from tests.unit.mie.test_moving_block_net_outcomes_v1 import MINUTE, START, _inputs

D = Decimal


def _arguments(plan, cost_plan, path):
    return {
        "plan_bytes": plan.canonical_json_bytes(),
        "cost_plan_bytes": cost_plan.canonical_json_bytes(),
        "cost_path_bytes": path.canonical_json_bytes(),
        "expected_plan_sha256": plan.canonical_sha256(),
        "expected_cost_plan_sha256": cost_plan.canonical_sha256(),
        "expected_cost_path_sha256": path.canonical_sha256(),
    }


@pytest.fixture
def inputs():
    prior, cost_plan, path = _inputs()
    plan = subject.OrderReshufflePlanV1(
        **prior.model_dump(
            exclude={"schema_version", "block_length", "lower_tail_probability"}
        ),
        upper_drawdown_quantile=D("0.875"),
    )
    return plan, cost_plan, path


def test_locked_permutations_preserve_each_outcome_and_vary_drawdown(inputs):
    plan, cost_plan, path = inputs
    report = subject.evaluate_order_reshuffle_v1(**_arguments(*inputs)).contract
    costed = subject.evaluate_costed_return_path(
        tuple(row.asset_return for row in path.observations),
        tuple(row.exposure for row in path.observations),
        cost_model=cost_plan.cost_model,
        observation_interval_seconds=cost_plan.observation_interval_seconds,
        cvar_confidence_level=cost_plan.cvar_confidence_level,
    )
    net = tuple(row.net_return for row in costed.observations)
    assert report.locked_net_outcomes_sha256 == subject._locked_net_hash(net)
    assert report.original_maximum_drawdown == costed.maximum_drawdown
    permutations = tuple(
        subject._permutation(bytes.fromhex(plan.seed_sha256), replication, len(net))
        for replication in range(plan.replications)
    )
    assert len(set(permutations)) == plan.replications
    assert permutations[0] == (13, 6, 9, 14, 0, 10, 5, 8, 1, 15, 12, 4, 7, 3, 2, 11)
    assert report.replications[0].permutation_sha256 == (
        "95ca4c66cfb3f295c459d2dfd52fd49eeb18bc3e3fd38fde3af256111c6974dd"
    )
    expected = []
    with localcontext(subject._ARITHMETIC_CONTEXT):
        for positions, item in zip(permutations, report.replications, strict=True):
            assert tuple(sorted(positions)) == tuple(range(len(net)))
            drawdown = _maximum_drawdown(tuple(net[position] for position in positions))
            assert item.maximum_drawdown == drawdown
            expected.append(drawdown)
    assert len(set(expected)) > 1
    assert report.drawdown_limit_breach_count == sum(
        drawdown > plan.maximum_drawdown_limit for drawdown in expected
    )
    assert report.empirical_upper_drawdown == sorted(expected)[6]


def test_exact_readback_with_altered_ambient_decimal_context_and_tamper(inputs):
    args = _arguments(*inputs)
    actual = subject.evaluate_order_reshuffle_v1(**args)
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_UP
        replay = subject.verify_order_reshuffle_v1(
            actual.payload, expected_report_sha256=actual.sha256, **args
        )
    assert actual == replay
    assert all(
        getattr(actual.contract, field) is False
        for field in (
            "independent_preregistration_verified",
            "source_authenticity_verified",
            "predictive_oos_eligible",
            "economic_oos_accepted",
            "execution_authority",
        )
    )
    changed = actual.contract.model_copy(
        update={"locked_net_outcomes_sha256": "d" * 64}
    )
    with pytest.raises(
        subject.ArtifactVerificationError, match="does not match replay"
    ):
        subject.verify_order_reshuffle_v1(
            changed.canonical_json_bytes(),
            expected_report_sha256=changed.canonical_sha256(),
            **args,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preregistered_at", START),
        ("preregistered_at", START - 3 * MINUTE),
        ("candidate_sha256", "f" * 64),
        ("cost_plan_sha256", "f" * 64),
        ("source_window_end", START + 17 * MINUTE),
        ("observation_count", 17),
    ],
)
def test_rehashed_plan_cannot_move_identity_window_or_population(inputs, field, value):
    plan, cost_plan, path = inputs
    changed = plan.model_copy(update={field: value})
    with pytest.raises(ValueError, match="plan, population or source pin differs"):
        subject.evaluate_order_reshuffle_v1(**_arguments(changed, cost_plan, path))


def test_rehashed_path_with_missing_order_or_late_availability_is_refused(inputs):
    plan, cost_plan, path = inputs
    first, second, *rest = path.observations
    late = first.model_copy(update={"return_available_at": path.evaluated_at + MINUTE})
    for rows in ((second, first, *rest), (late, second, *rest)):
        changed = path.model_copy(update={"observations": rows})
        with pytest.raises(ValueError, match="missing, late, or out of order"):
            subject.evaluate_order_reshuffle_v1(**_arguments(plan, cost_plan, changed))
    args = _arguments(*inputs)
    with pytest.raises(subject.ArtifactVerificationError):
        subject.evaluate_order_reshuffle_v1(
            **{**args, "expected_cost_path_sha256": "d" * 64}
        )
