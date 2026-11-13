"""Apply the frozen Development-only legacy calibration to one Validation event."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.artifact import SHA256_RE, FrozenGate3Artifact, _freeze
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_development_fit_v1 import (
    DevelopmentLegacyFitV1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationRationalV1,
    legacy_calibration_estimate_v1,
    legacy_calibration_score_bin_v1,
    verify_legacy_calibration_policy_v1,
)
from app.mie.validation.prospective_devval_validation_inputs_v1 import (
    ValidationEventIdentityV1,
    _read,
    verify_label_free_validation_inputs_v1,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2

_MAX_MODEL_BYTES = 8 * 1024 * 1024
_MAX_PREDICTION_BYTES = 16 * 1024
_DIRECTIONS = ("long", "short")


class DevvalValidationPredictionError(ValueError):
    """A frozen Validation inference input or retained prediction differs."""


class ValidationPredictionSideV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    status: Literal["missing", "neutral", "unsupported", "computed"]
    feature_replay_sha256: Sha256 | None
    raw_result_sha256: Sha256 | None
    raw_direction: Literal["long", "short", "neutral"] | None
    bin_index: int | None = Field(ge=0, le=100)
    probability: LegacyCalibrationRationalV1 | None

    @model_validator(mode="after")
    def exact_disposition(self) -> ValidationPredictionSideV1:
        missing = self.status == "missing"
        directional = self.status in ("unsupported", "computed")
        if (
            (self.feature_replay_sha256 is None) != missing
            or (self.raw_result_sha256 is None) != missing
            or (self.raw_direction is None) != missing
            or (self.raw_direction == "neutral") != (self.status == "neutral")
            or (self.raw_direction in _DIRECTIONS) != directional
            or (self.bin_index is not None) != directional
            or (self.probability is not None) != (self.status == "computed")
        ):
            raise ValueError("Validation prediction side disposition differs")
        return self


class DevvalValidationPredictionV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_validation_prediction.v1"] = (
        "ctcc.mie.devval_validation_prediction.v1"
    )
    identity: ValidationEventIdentityV1
    feature_projection_sha256: Sha256
    raw_event_sha256: Sha256
    policy_sha256: Sha256
    development_fit_sha256: Sha256
    development_fit_input_sha256: Sha256
    final_development_rows_sha256: Sha256
    latest_development_label_read_at: datetime
    validation_feature_history_start_at: datetime
    feature_plan_sha256: Sha256
    scorer_config_sha256: Sha256
    status: Literal["incomplete", "complete_computational"]
    sides: tuple[ValidationPredictionSideV1, ValidationPredictionSideV1]
    independent_availability_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_pair(self) -> DevvalValidationPredictionV1:
        incomplete = self.status == "incomplete"
        latest_label = require_utc(
            self.latest_development_label_read_at, "Development latest label"
        )
        history_start = require_utc(
            self.validation_feature_history_start_at, "Validation feature start"
        )
        if (
            tuple(side.instrument_id for side in self.sides) != INSTRUMENTS
            or (self.identity.decision_at is None) != incomplete
            or any((side.status == "missing") != incomplete for side in self.sides)
            or not latest_label < history_start < self.identity.event_closed_at
        ):
            raise ValueError("Validation prediction pair disposition differs")
        return self


def _final_bins(model, policy):
    expected = tuple(
        (direction, index)
        for direction in _DIRECTIONS
        for index in range(len(policy.score_cuts) + 1)
    )
    bins = model.final_development_bins
    if tuple((item.direction, item.index) for item in bins) != expected:
        raise DevvalValidationPredictionError(
            "validation_prediction_final_bins_changed"
        )
    if any(
        item.estimate
        != legacy_calibration_estimate_v1(policy, support=item.estimate.support)
        for item in bins
    ):
        raise DevvalValidationPredictionError("validation_prediction_estimate_changed")
    return {(item.direction, item.index): item.estimate for item in bins}


def _side_prediction(feature, side, bins, policy):
    result = side.raw_result
    status, direction, index, probability = "missing", None, None, None
    if result is not None:
        status, direction = "neutral", result.raw_direction
        if result.status == "directional":
            index = legacy_calibration_score_bin_v1(
                policy, direction=direction, raw_score=result.raw_score
            ).index
            probability = bins[(direction, index)].probability
            status = "computed" if probability is not None else "unsupported"
    return ValidationPredictionSideV1(
        instrument_id=side.instrument_id,
        status=status,
        feature_replay_sha256=feature.feature_replay_sha256 if feature else None,
        raw_result_sha256=side.raw_result_sha256,
        raw_direction=direction,
        bin_index=index,
        probability=probability,
    )


def predict_devval_validation_event_v1(
    *,
    projection_payload: bytes,
    expected_projection_sha256: str,
    raw_event_payload: bytes,
    expected_raw_event_sha256: str,
    development_fit_payload: bytes,
    expected_development_fit_sha256: str,
    expected_development_fit_input_sha256: str,
    policy_payload: bytes,
    expected_policy_sha256: str,
    scorer_config_json: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
) -> FrozenGate3Artifact[DevvalValidationPredictionV1]:
    """Predict using only final Development bins and label-free Validation bytes."""
    try:
        projection, raw = verify_label_free_validation_inputs_v1(
            projection_payload=projection_payload,
            expected_projection_sha256=expected_projection_sha256,
            raw_event_payload=raw_event_payload,
            expected_raw_event_sha256=expected_raw_event_sha256,
        )
        model = _read(
            development_fit_payload,
            expected_development_fit_sha256,
            DevelopmentLegacyFitV1,
            _MAX_MODEL_BYTES,
        )
        if (
            type(expected_development_fit_input_sha256) is not str
            or SHA256_RE.fullmatch(expected_development_fit_input_sha256) is None
            or model.input_sha256 != expected_development_fit_input_sha256
            or model.policy_sha256 != expected_policy_sha256
            or model.study_plan_sha256 != projection.study_plan_sha256
            or model.schedule_sha256 != projection.identity.schedule_sha256
            or model.coverage_sha256 != projection.identity.coverage_sha256
            or model.batch_sha256 != projection.identity.batch_sha256
        ):
            raise DevvalValidationPredictionError(
                "validation_prediction_model_pins_changed"
            )
        policy = verify_legacy_calibration_policy_v1(
            policy_payload,
            expected_policy_sha256=expected_policy_sha256,
            scorer_config_json=scorer_config_json,
            feature_plan=feature_plan,
            expected_source_checkpoint=DevvalCandidateSourceV1(
                commit=projection.source_commit,
                tree=projection.source_tree,
                manifest_sha256=projection.source_manifest_sha256,
            ),
            expected_source_archive_sha256=projection.source_archive_sha256,
        )
        if (
            policy.scorer_config_sha256 != projection.scorer_config_sha256
            or policy.joint_feature_plan_sha256 != projection.feature_plan_sha256
        ):
            raise DevvalValidationPredictionError(
                "validation_prediction_policy_changed"
            )
        feature_history_start = projection.identity.event_closed_at - timedelta(
            seconds=feature_plan.history_bars * feature_plan.bar_horizon.seconds
        )
        if (
            require_utc(
                model.latest_development_label_read_at, "Development latest label"
            )
            >= feature_history_start
        ):
            raise DevvalValidationPredictionError(
                "validation_prediction_development_label_overlap"
            )
        if projection.sides is not None:
            for side in projection.sides:
                replay = side.feature
                if (
                    replay.source_row_count != feature_plan.history_bars
                    or replay.feature_snapshot.horizon != feature_plan.bar_horizon
                    or replay.feature_snapshot.feature_version
                    != feature_plan.feature_version
                ):
                    raise DevvalValidationPredictionError(
                        "validation_prediction_feature_plan_changed"
                    )
        bins = _final_bins(model, policy)
        features = projection.sides if projection.sides is not None else (None, None)
        bitcoin, ether = (
            _side_prediction(feature, side, bins, policy)
            for feature, side in zip(features, raw.sides, strict=True)
        )
        frozen = _freeze(
            DevvalValidationPredictionV1(
                identity=projection.identity,
                feature_projection_sha256=expected_projection_sha256,
                raw_event_sha256=expected_raw_event_sha256,
                policy_sha256=expected_policy_sha256,
                development_fit_sha256=expected_development_fit_sha256,
                development_fit_input_sha256=expected_development_fit_input_sha256,
                final_development_rows_sha256=model.final_development_rows_sha256,
                latest_development_label_read_at=model.latest_development_label_read_at,
                validation_feature_history_start_at=feature_history_start,
                feature_plan_sha256=projection.feature_plan_sha256,
                scorer_config_sha256=projection.scorer_config_sha256,
                status=projection.status,
                sides=(bitcoin, ether),
            ),
            contract_type=DevvalValidationPredictionV1,
        )
        if len(frozen.payload) > _MAX_PREDICTION_BYTES:
            raise DevvalValidationPredictionError("validation_prediction_size_invalid")
        return frozen
    except DevvalValidationPredictionError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
        raise DevvalValidationPredictionError("validation_prediction_refused") from None


def verify_devval_validation_prediction_v1(
    payload: bytes,
    *,
    expected_prediction_sha256: str,
    projection_payload: bytes,
    expected_projection_sha256: str,
    raw_event_payload: bytes,
    expected_raw_event_sha256: str,
    development_fit_payload: bytes,
    expected_development_fit_sha256: str,
    expected_development_fit_input_sha256: str,
    policy_payload: bytes,
    expected_policy_sha256: str,
    scorer_config_json: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
) -> DevvalValidationPredictionV1:
    """Read exact bytes and reproduce one inference from the same pinned inputs."""
    try:
        retained = _read(
            payload,
            expected_prediction_sha256,
            DevvalValidationPredictionV1,
            _MAX_PREDICTION_BYTES,
        )
    except ValueError:
        raise DevvalValidationPredictionError(
            "validation_prediction_readback_refused"
        ) from None
    rebuilt = predict_devval_validation_event_v1(
        projection_payload=projection_payload,
        expected_projection_sha256=expected_projection_sha256,
        raw_event_payload=raw_event_payload,
        expected_raw_event_sha256=expected_raw_event_sha256,
        development_fit_payload=development_fit_payload,
        expected_development_fit_sha256=expected_development_fit_sha256,
        expected_development_fit_input_sha256=expected_development_fit_input_sha256,
        policy_payload=policy_payload,
        expected_policy_sha256=expected_policy_sha256,
        scorer_config_json=scorer_config_json,
        feature_plan=feature_plan,
    )
    if rebuilt.payload != payload:
        raise DevvalValidationPredictionError("validation_prediction_readback_changed")
    return retained
