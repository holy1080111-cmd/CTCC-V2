"""Synthetic frozen-model and label-free outer Validation inference tests."""

from datetime import timedelta

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import prospective_devval_validation_prediction_v1 as subject
from app.mie.validation.artifact import FrozenGate3Artifact
from app.mie.validation.prospective_devval_development_fit_v1 import (
    DevelopmentLegacyBinV1,
    DevelopmentLegacyFitV1,
    DevelopmentLegacyFoldFitV1,
    DevelopmentLegacyHeldoutV1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationSupportV1,
    legacy_calibration_estimate_v1,
)
from tests.unit.mie.test_devval_joint_companion_coverage_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- register the shared synthetic fixture.
)
from tests.unit.mie.test_devval_joint_study_v1 import (
    study_input as study_input,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_legacy_calibration_policy_v1 import (
    policy_input as policy_input,  # noqa: PLC0414 -- register the shared fixture.
)
from tests.unit.mie.test_devval_validation_inputs_v1 import _evaluator_args
from tests.unit.mie.test_devval_validation_inputs_v1 import (
    validation_case as validation_case,  # noqa: PLC0414 -- register the shared fixture.
)


def _fit_pins(model):
    return {
        "development_fit_payload": model.canonical_json_bytes(),
        "expected_development_fit_sha256": model.canonical_sha256(),
    }


def _with_raw_score(side, **changes):
    if side.raw_result is None:
        return side
    result = side.raw_result.model_copy(update=changes)
    return side.model_copy(
        update={
            "status": result.status,
            "raw_result": result,
            "raw_result_sha256": result.canonical_sha256(),
        }
    )


@pytest.fixture
def prediction_input(validation_case, policy_input):
    _, _, projection_artifact, raw = validation_case
    policy, pins = policy_input
    config_pin = policy.scorer_config_sha256
    projection = projection_artifact.contract.model_copy(
        update={"scorer_config_sha256": config_pin}
    )
    projection_artifact = FrozenGate3Artifact(
        payload=projection.canonical_json_bytes(),
        sha256=projection.canonical_sha256(),
        contract=projection,
    )
    raw = raw.model_copy(
        update={
            "config_sha256": config_pin,
            "sides": tuple(
                _with_raw_score(side, config_sha256=config_pin) for side in raw.sides
            ),
        }
    )
    feature_plan = pins["feature_plan"]
    history_start = projection.identity.event_closed_at - timedelta(
        seconds=feature_plan.history_bars * feature_plan.bar_horizon.seconds
    )
    bins = []
    for direction in ("long", "short"):
        for index in range(len(policy.score_cuts) + 1):
            # Only long score 37's [20, 80) bin meets support. Exact mean 4/9.
            count, positive, events = (
                (4, 2, 2) if (direction, index) == ("long", 1) else (0, 0, 0)
            )
            support = LegacyCalibrationSupportV1(
                directional_symbol_rows=count,
                positive_symbol_rows=positive,
                distinct_joint_events=events,
            )
            bins.append(
                DevelopmentLegacyBinV1(
                    direction=direction,
                    index=index,
                    estimate=legacy_calibration_estimate_v1(policy, support=support),
                )
            )
    model = DevelopmentLegacyFitV1(
        input_sha256=sha(b"synthetic Development input"),
        policy_sha256=pins["expected_policy_sha256"],
        study_plan_sha256=projection.study_plan_sha256,
        schedule_sha256=projection.identity.schedule_sha256,
        coverage_sha256=projection.identity.coverage_sha256,
        batch_sha256=projection.identity.batch_sha256,
        latest_development_label_read_at=history_start - timedelta(seconds=1),
        fold_fits=(
            DevelopmentLegacyFoldFitV1(
                fold_index=0,
                training_rows_sha256=sha(b"synthetic Development fold"),
                bins=tuple(bins),
                heldout=tuple(
                    DevelopmentLegacyHeldoutV1(
                        ordinal=2,
                        instrument_id=side.instrument_id,
                        status="computed",
                        bin_index=1,
                        probability=bins[1].estimate.probability,
                    )
                    for side in raw.sides
                ),
            ),
        ),
        final_development_rows_sha256=sha(b"synthetic Development rows"),
        final_development_bins=tuple(bins),
    )
    arguments = (
        _evaluator_args(projection_artifact, raw)
        | _fit_pins(model)
        | {
            "expected_development_fit_input_sha256": model.input_sha256,
            "policy_payload": policy.canonical_json_bytes(),
            "expected_policy_sha256": pins["expected_policy_sha256"],
            "scorer_config_json": pins["scorer_config_json"],
            "feature_plan": feature_plan,
        }
    )
    return arguments, model, projection, raw, history_start


def test_validation_prediction_replays_exact_bytes_and_retains_missing(
    prediction_input,
):
    arguments, model, projection, _, history_start = prediction_input
    first = subject.predict_devval_validation_event_v1(**arguments)
    reread = subject.verify_devval_validation_prediction_v1(
        first.payload, expected_prediction_sha256=first.sha256, **arguments
    )
    assert reread == first.contract
    assert reread.canonical_json_bytes() == first.payload
    assert reread.identity == projection.identity
    assert reread.development_fit_sha256 == model.canonical_sha256()
    assert reread.validation_feature_history_start_at == history_start
    assert reread.latest_development_label_read_at < history_start
    assert reread.evaluator_first_access_verified is reread.execution_authority is False
    if projection.identity.decision_at is None:
        assert reread.status == "incomplete"
        assert all(
            side.status == "missing" and side.probability is None
            for side in reread.sides
        )
    else:
        assert reread.status == "complete_computational"
        for side in reread.sides:
            assert side.status == "computed" and side.bin_index == 1
            assert side.probability is not None
            assert (side.probability.numerator, side.probability.denominator) == (4, 9)


def test_neutral_and_unsupported_are_not_probabilities(prediction_input):
    arguments, _, projection, raw, _ = prediction_input
    if projection.identity.decision_at is None:
        return
    bitcoin, ether = raw.sides
    amended = raw.model_copy(
        update={
            "sides": (
                _with_raw_score(
                    bitcoin, raw_direction="neutral", status="neutral", raw_score=None
                ),
                _with_raw_score(ether, raw_direction="short"),
            )
        }
    )
    prediction = subject.predict_devval_validation_event_v1(
        **(
            arguments
            | {
                "raw_event_payload": amended.canonical_json_bytes(),
                "expected_raw_event_sha256": amended.canonical_sha256(),
            }
        )
    ).contract
    neutral, unsupported = prediction.sides
    assert neutral.status == "neutral" and neutral.bin_index is None
    assert unsupported.status == "unsupported" and unsupported.bin_index == 1
    assert neutral.probability is unsupported.probability is None


@pytest.mark.parametrize("defect", ("foreign_batch", "overlap"))
def test_repin_cannot_replace_campaign_or_development_chronology(
    prediction_input, defect
):
    arguments, model, _, _, history_start = prediction_input
    if defect == "foreign_batch":
        changed = model.model_copy(update={"batch_sha256": sha(b"foreign batch")})
        refusal = "validation_prediction_model_pins_changed"
    else:
        changed = model.model_copy(
            update={"latest_development_label_read_at": history_start}
        )
        refusal = "validation_prediction_development_label_overlap"
    with pytest.raises(subject.DevvalValidationPredictionError, match=refusal):
        subject.predict_devval_validation_event_v1(**(arguments | _fit_pins(changed)))
