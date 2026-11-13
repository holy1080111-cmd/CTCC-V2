"""Development-only legacy probability fit; never accepts Validation rows."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Literal

from pydantic import Field, model_validator

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import Gate3Contract, Sha256, require_utc
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyFoldV1,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
)
from app.mie.validation.prospective_devval_legacy_calibration_policy_v1 import (
    LegacyCalibrationEstimateV1,
    LegacyCalibrationRationalV1,
    LegacyCalibrationSupportV1,
    legacy_calibration_estimate_v1,
    legacy_calibration_score_bin_v1,
    verify_legacy_calibration_policy_v1,
)
from app.mie.validation.replay import ForwardDirectionLabelV2, FrozenFeatureReplayPlanV2

_MAX_INPUT_BYTES = 8 * 1024 * 1024
_MAX_POLICY_BYTES = 8 * 1024
_SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
_DIRECTIONS = ("long", "short")


class DevelopmentLegacyFitError(ValueError):
    """A fixed-code computational refusal without input rows in the message."""


class DevelopmentLegacySideV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    feature_history_start_at: datetime
    feature_replay_sha256: Sha256
    label: ForwardDirectionLabelV2
    status: Literal["missing", "neutral", "directional"]
    direction: Literal["neutral", "long", "short"]
    raw_score: int | None = Field(default=None, ge=0, le=100)
    raw_result_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def exact_disposition(self) -> DevelopmentLegacySideV1:
        if (
            require_utc(self.feature_history_start_at, "Development feature history")
            >= self.label.base_bar_closed_at
            or self.label.instrument_id != self.instrument_id
            or self.label.feature_replay_sha256 != self.feature_replay_sha256
            or (self.status == "directional")
            != (self.direction in _DIRECTIONS and self.raw_score is not None)
            or (self.status == "missing") != (self.raw_result_sha256 is None)
            or (self.status != "directional" and self.raw_score is not None)
            or (self.status != "directional" and self.direction != "neutral")
        ):
            raise ValueError("Development raw score disposition changed")
        return self


class DevelopmentLegacySlotV1(Gate3Contract):
    ordinal: int = Field(ge=0, le=4095)
    event_closed_at: datetime
    decision_at: datetime
    sides: tuple[DevelopmentLegacySideV1, DevelopmentLegacySideV1]

    @model_validator(mode="after")
    def same_event(self) -> DevelopmentLegacySlotV1:
        close = require_utc(self.event_closed_at, "Development close")
        decision = require_utc(self.decision_at, "Development decision")
        if (
            close > decision
            or tuple(side.instrument_id for side in self.sides) != _SYMBOLS
        ):
            raise ValueError("Development event geometry changed")
        if any(
            side.label.decision_at != decision or side.label.base_bar_closed_at != close
            for side in self.sides
        ):
            raise ValueError("Development label geometry changed")
        return self


class DevelopmentLegacyFitInputV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.development_legacy_fit_input.v1"] = (
        "ctcc.mie.development_legacy_fit_input.v1"
    )
    study_plan_sha256: Sha256
    schedule_sha256: Sha256
    coverage_sha256: Sha256
    batch_sha256: Sha256
    completion_sha256: Sha256
    root_binding_sha256: Sha256
    row_inventory_sha256: Sha256
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    feature_plan_sha256: Sha256
    scorer_config_sha256: Sha256
    development_event_times_sha256: Sha256
    slots: tuple[DevelopmentLegacySlotV1, ...] = Field(min_length=2, max_length=4096)
    development_folds: tuple[DevvalCampaignStudyFoldV1, ...] = Field(
        min_length=1, max_length=64
    )
    validation_rows_present: Literal[False] = False
    independent_availability_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_development_roster(self) -> DevelopmentLegacyFitInputV1:
        if tuple(slot.ordinal for slot in self.slots) != tuple(
            range(self.slots[0].ordinal, self.slots[0].ordinal + len(self.slots))
        ) or any(
            before.decision_at >= after.decision_at
            for before, after in zip(self.slots, self.slots[1:])
        ):
            raise ValueError("Development roster changed")
        _check_folds(self.slots, self.development_folds)
        return self


class DevelopmentLegacyBinV1(Gate3Contract):
    direction: Literal["long", "short"]
    index: int = Field(ge=0, le=100)
    estimate: LegacyCalibrationEstimateV1


class DevelopmentLegacyHeldoutV1(Gate3Contract):
    ordinal: int = Field(ge=0)
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    status: Literal["missing", "neutral", "unsupported", "computed"]
    bin_index: int | None
    probability: LegacyCalibrationRationalV1 | None

    @model_validator(mode="after")
    def exact_status(self) -> DevelopmentLegacyHeldoutV1:
        if (self.status == "computed") != (self.probability is not None) or (
            self.status in ("missing", "neutral")
        ) != (self.bin_index is None):
            raise ValueError("Development heldout disposition changed")
        return self


class DevelopmentLegacyFoldFitV1(Gate3Contract):
    fold_index: int = Field(ge=0)
    training_rows_sha256: Sha256
    bins: tuple[DevelopmentLegacyBinV1, ...]
    heldout: tuple[DevelopmentLegacyHeldoutV1, ...]


class DevelopmentLegacyFitV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.development_legacy_fit.v1"] = (
        "ctcc.mie.development_legacy_fit.v1"
    )
    input_sha256: Sha256
    policy_sha256: Sha256
    fold_fits: tuple[DevelopmentLegacyFoldFitV1, ...]
    study_plan_sha256: Sha256
    schedule_sha256: Sha256
    coverage_sha256: Sha256
    batch_sha256: Sha256
    latest_development_label_read_at: datetime
    final_development_rows_sha256: Sha256
    final_development_bins: tuple[DevelopmentLegacyBinV1, ...]
    status: Literal["development_computational_only"] = "development_computational_only"
    validation_evaluated: Literal[False] = False
    independent_preregistration_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _check_folds(slots, folds) -> None:
    rows = tuple(side for slot in slots for side in slot.sides)
    prior_evaluation: set[int] = set()
    for fold in folds:
        training, evaluation = set(fold.training_indices), set(fold.validation_indices)
        excluded = (
            set(fold.purged_indices)
            | set(fold.prior_embargoed_indices)
            | set(fold.embargoed_indices)
        )
        if (
            not training
            or not evaluation
            or len(training) != len(fold.training_indices)
            or len(evaluation) != len(fold.validation_indices)
            or max(training | evaluation) >= len(rows)
            or min(training | evaluation) < 0
            or training & evaluation
            or prior_evaluation & evaluation
            or excluded & (training | evaluation)
            or max(training) >= min(evaluation)
        ):
            raise ValueError("Development fold membership changed")
        for membership in (training, evaluation):
            if any((index ^ 1) not in membership for index in membership):
                raise ValueError("Development fold splits a joint event")
        if max(slots[index // 2].decision_at for index in training) >= min(
            slots[index // 2].decision_at for index in evaluation
        ) or max(rows[index].label.read_at for index in training) >= min(
            rows[index].feature_history_start_at for index in evaluation
        ):
            raise ValueError("Development fold chronology changed")
        prior_evaluation.update(evaluation)


def _rows_sha(rows) -> str:
    return sha256(
        b"".join(bytes.fromhex(row.canonical_sha256()) for row in rows)
    ).hexdigest()


def _bins(rows, policy):
    counts = {}
    for ordinal, side in rows:
        if side.status != "directional":
            continue
        index = legacy_calibration_score_bin_v1(
            policy, direction=side.direction, raw_score=side.raw_score
        ).index
        key = (side.direction, index)
        bucket = counts.setdefault(key, [0, 0, set()])
        bucket[0] += 1
        bucket[1] += int(side.label.positive)
        bucket[2].add(ordinal)
    result = []
    for direction in _DIRECTIONS:
        for index in range(len(policy.score_cuts) + 1):
            count, positive, events = counts.get((direction, index), [0, 0, set()])
            support = LegacyCalibrationSupportV1(
                directional_symbol_rows=count,
                positive_symbol_rows=positive,
                distinct_joint_events=len(events),
            )
            result.append(
                DevelopmentLegacyBinV1(
                    direction=direction,
                    index=index,
                    estimate=legacy_calibration_estimate_v1(policy, support=support),
                )
            )
    return tuple(result)


def _heldout(slot, side, bins, policy):
    if side.status != "directional":
        return DevelopmentLegacyHeldoutV1(
            ordinal=slot.ordinal,
            instrument_id=side.instrument_id,
            status=side.status,
            bin_index=None,
            probability=None,
        )
    index = legacy_calibration_score_bin_v1(
        policy, direction=side.direction, raw_score=side.raw_score
    ).index
    estimate = next(
        item.estimate
        for item in bins
        if item.direction == side.direction and item.index == index
    )
    probability = estimate.probability
    return DevelopmentLegacyHeldoutV1(
        ordinal=slot.ordinal,
        instrument_id=side.instrument_id,
        status="unsupported" if probability is None else "computed",
        bin_index=index,
        probability=probability,
    )


def fit_development_legacy_calibration_v1(
    input_payload: bytes,
    *,
    expected_input_sha256: str,
    policy_payload: bytes,
    expected_policy_sha256: str,
    scorer_config_json: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
) -> FrozenGate3Artifact[DevelopmentLegacyFitV1]:
    """Fit Development folds and final Development only; no Validation argument."""
    try:
        if (
            type(input_payload) is not bytes
            or not 0 < len(input_payload) <= _MAX_INPUT_BYTES
            or type(policy_payload) is not bytes
            or not 0 < len(policy_payload) <= _MAX_POLICY_BYTES
        ):
            raise ValueError("input size")
        original = _verify(
            input_payload,
            expected_sha256=expected_input_sha256,
            contract_type=DevelopmentLegacyFitInputV1,
        )
        policy = verify_legacy_calibration_policy_v1(
            policy_payload,
            expected_policy_sha256=expected_policy_sha256,
            scorer_config_json=scorer_config_json,
            feature_plan=feature_plan,
            expected_source_checkpoint=original.source_checkpoint,
            expected_source_archive_sha256=original.source_archive_sha256,
        )
        if (
            policy.scorer_config_sha256 != original.scorer_config_sha256
            or policy.joint_feature_plan_sha256 != original.feature_plan_sha256
            or any(
                side.label.horizon_seconds != policy.outcome_horizon_seconds
                or side.label.positive_threshold != policy.positive_threshold
                for slot in original.slots
                for side in slot.sides
            )
        ):
            raise ValueError("policy input changed")
        flattened = tuple(
            (slot, side) for slot in original.slots for side in slot.sides
        )
        folds = []
        for number, fold in enumerate(original.development_folds):
            training = tuple(flattened[i] for i in fold.training_indices)
            bins = _bins(((slot.ordinal, side) for slot, side in training), policy)
            heldout = tuple(
                _heldout(slot, side, bins, policy)
                for slot, side in (flattened[i] for i in fold.validation_indices)
            )
            folds.append(
                DevelopmentLegacyFoldFitV1(
                    fold_index=number,
                    training_rows_sha256=_rows_sha(side for _, side in training),
                    bins=bins,
                    heldout=heldout,
                )
            )
        final_bins = _bins(((slot.ordinal, side) for slot, side in flattened), policy)
        return _freeze(
            DevelopmentLegacyFitV1(
                input_sha256=expected_input_sha256,
                policy_sha256=expected_policy_sha256,
                fold_fits=tuple(folds),
                study_plan_sha256=original.study_plan_sha256,
                schedule_sha256=original.schedule_sha256,
                coverage_sha256=original.coverage_sha256,
                batch_sha256=original.batch_sha256,
                latest_development_label_read_at=max(
                    side.label.read_at for _, side in flattened
                ),
                final_development_rows_sha256=_rows_sha(side for _, side in flattened),
                final_development_bins=final_bins,
            ),
            contract_type=DevelopmentLegacyFitV1,
        )
    except (TypeError, ValueError, ArithmeticError):
        raise DevelopmentLegacyFitError("development_legacy_fit_refused") from None
