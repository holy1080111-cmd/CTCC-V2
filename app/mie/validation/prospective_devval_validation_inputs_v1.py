"""Project one exact outer Validation event into label-free evaluator inputs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.batch_replay import TARGETS
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    DevvalCampaignStudyPlanV1,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    _MAX_REPORT_BYTES,
    DevvalJointCompanionCoverageV1,
)
from app.mie.validation.prospective_devval_joint_raw_score_v1 import (
    _MAX_EVENT_BYTES,
    JointDevvalRawScoreEventV1,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    _verify_study_policy,
    compile_research_campaign,
)
from app.mie.validation.replay import (
    PointInTimeBar,
    PointInTimeReplaySnapshot,
    replay_features_at,
)

_MAX_POLICY_BYTES = 128 * 1024
_MAX_PREFIX_BYTES = 16 * 1024 * 1024
_MAX_PROJECTION_BYTES = 64 * 1024
_Instrument = Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
_RowWire = Annotated[str, Field(min_length=1, max_length=8 * 1024)]


class DevvalValidationInputError(ValueError):
    """The label-free event or its independent evidence pin differs."""


class ValidationEventIdentityV1(Gate3Contract):
    schedule_sha256: Sha256
    coverage_sha256: Sha256
    batch_sha256: Sha256
    slot_ordinal: int = Field(ge=0)
    partition: Literal[DatasetPartition.VALIDATION] = DatasetPartition.VALIDATION
    event_closed_at: datetime
    decision_at: datetime | None
    decision_cutoff_at: datetime

    @model_validator(mode="after")
    def decision_window(self) -> ValidationEventIdentityV1:
        close = require_utc(self.event_closed_at, "event close")
        cutoff = require_utc(self.decision_cutoff_at, "event decision cutoff")
        if close >= cutoff or (
            self.decision_at is not None
            and not close <= require_utc(self.decision_at, "event decision") <= cutoff
        ):
            raise ValueError("Validation decision is outside its slot")
        return self


class ValidationEventPrefixV1(Gate3Contract):
    """A separately approved read-only input containing only one event's history."""

    schema_version: Literal["ctcc.mie.devval_validation_event_prefix.v1"] = (
        "ctcc.mie.devval_validation_event_prefix.v1"
    )
    identity: ValidationEventIdentityV1
    bitcoin: tuple[_RowWire, ...] = Field(max_length=10_000)
    ether: tuple[_RowWire, ...] = Field(max_length=10_000)


class ValidationFeatureSideV1(Gate3Contract):
    instrument_id: _Instrument
    aggregate_sha256: Sha256
    publication_sha256: Sha256
    readback_sha256: Sha256
    market_replay_receipt_sha256: Sha256
    feature_replay_sha256: Sha256
    feature_replay_json: str = Field(min_length=1, max_length=16 * 1024)

    @property
    def feature(self) -> PointInTimeReplaySnapshot:
        return PointInTimeReplaySnapshot.model_validate_json(self.feature_replay_json)


class ValidationFeatureProjectionV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_validation_feature_projection.v1"] = (
        "ctcc.mie.devval_validation_feature_projection.v1"
    )
    identity: ValidationEventIdentityV1
    prefix_sha256: Sha256
    blueprint_sha256: Sha256
    study_plan_sha256: Sha256
    feature_plan_sha256: Sha256
    scorer_config_sha256: Sha256
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_manifest_sha256: Sha256
    source_archive_sha256: Sha256
    status: Literal["incomplete", "complete_computational"]
    sides: tuple[ValidationFeatureSideV1, ValidationFeatureSideV1] | None
    independent_availability_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_features(self) -> ValidationFeatureProjectionV1:
        complete = self.status == "complete_computational"
        if (self.sides is not None) != complete or (
            self.identity.decision_at is not None
        ) != complete:
            raise ValueError("Validation feature status differs")
        if self.sides is None:
            return self
        for symbol, side in zip(INSTRUMENTS, self.sides, strict=True):
            feature = side.feature
            if (
                side.instrument_id != symbol
                or feature.instrument_id != symbol
                or feature.as_of != self.identity.decision_at
                or feature.data_cutoff != self.identity.event_closed_at
                or feature.model_dump_json() != side.feature_replay_json
                or feature.replay_sha256 != side.feature_replay_sha256
            ):
                raise ValueError("Validation feature wire or identity differs")
        return self


def _read(payload, pin, contract, limit):
    if (
        type(payload) is not bytes
        or not 0 < len(payload) <= limit
        or type(pin) is not str
    ):
        raise DevvalValidationInputError("validation_input_wire_invalid")
    return _verify(payload, expected_sha256=pin, contract_type=contract)


def _read_prefix_rows(wires):
    rows = tuple(PointInTimeBar.model_validate_json(wire) for wire in wires)
    if any(
        row.model_dump_json() != wire for row, wire in zip(rows, wires, strict=True)
    ):
        raise DevvalValidationInputError("validation_input_prefix_wire_changed")
    return rows


def build_validation_feature_projection_v1(
    *,
    prefix_payload: bytes,
    expected_prefix_sha256: str,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    blueprint_payload: bytes,
    expected_blueprint_sha256: str,
    study_plan_payload: bytes,
    expected_study_plan_sha256: str,
    expected_batch_sha256: str,
) -> FrozenGate3Artifact[ValidationFeatureProjectionV1]:
    """Replay only the two pinned causal prefixes. No batch bytes are accepted."""
    try:
        prefix = _read(
            prefix_payload,
            expected_prefix_sha256,
            ValidationEventPrefixV1,
            _MAX_PREFIX_BYTES,
        )
        schedule = _read(
            schedule_payload,
            expected_schedule_sha256,
            GlobalDevvalCompanionScheduleV1,
            _MAX_SCHEDULE_BYTES,
        )
        coverage = _read(
            coverage_payload,
            expected_coverage_sha256,
            DevvalJointCompanionCoverageV1,
            _MAX_REPORT_BYTES,
        )
        blueprint = _read(
            blueprint_payload,
            expected_blueprint_sha256,
            DevvalResearchBlueprintV1,
            _MAX_POLICY_BYTES,
        )
        study = _read(
            study_plan_payload,
            expected_study_plan_sha256,
            DevvalCampaignStudyPlanV1,
            _MAX_POLICY_BYTES,
        )
        identity = prefix.identity
        campaign = compile_research_campaign(blueprint)
        source = schedule.source_checkpoint
        _verify_study_policy(
            blueprint, freeze_prospective_devval_campaign_v1(campaign).sha256, study
        )
        if (
            identity.schedule_sha256 != expected_schedule_sha256
            or identity.coverage_sha256 != expected_coverage_sha256
            or identity.batch_sha256 != expected_batch_sha256
            or coverage.schedule_sha256 != expected_schedule_sha256
            or coverage.batch_sha256 != expected_batch_sha256
            or coverage.campaign_plan_sha256 != schedule.campaign_plan_sha256
            or coverage.research_blueprint_sha256 != expected_blueprint_sha256
            or schedule.research_blueprint_sha256 != expected_blueprint_sha256
            or schedule.legacy_scorer_config_sha256
            != blueprint.legacy_score_source_sha256
            or campaign.origin != schedule.rest_origin
            or campaign.instrument_ids != schedule.instrument_ids
            or campaign.planned_at != schedule.planned_at
            or campaign.canonical_sha256() != schedule.campaign_plan_sha256
            or campaign.campaign_id != schedule.campaign_id
            or campaign.declared_source_commit != source.commit
            or campaign.declared_source_tree != source.tree
            or campaign.manifest_sha256 != source.manifest_sha256
            or identity.slot_ordinal >= len(schedule.slots)
            or len(schedule.slots) != len(coverage.slots)
        ):
            raise DevvalValidationInputError("validation_input_parent_pins_changed")
        planned = schedule.slots[identity.slot_ordinal]
        covered = coverage.slots[identity.slot_ordinal]
        plan = study.feature_plan
        horizon = plan.bar_horizon
        width = timedelta(seconds=horizon.seconds)
        close = identity.event_closed_at
        decision = identity.decision_at
        earliest = close - width * plan.history_bars
        outcome_close = close + timedelta(seconds=plan.outcome_horizon_seconds)
        complete = covered.status == "covered_computational"
        if (
            (horizon.label, horizon.seconds) not in TARGETS
            or horizon.seconds != schedule.horizon_seconds
            or earliest < campaign.validation.start_at
            or outcome_close > campaign.validation.end_at
            or (close - campaign.validation.start_at) % width != timedelta(0)
            or (
                decision is not None
                and (
                    decision >= close + width
                    or decision - earliest
                    > timedelta(seconds=study.feature_dependency_seconds)
                )
            )
            or covered.ordinal != identity.slot_ordinal
            or planned.partition != DatasetPartition.VALIDATION
            or covered.partition != planned.partition
            or close != planned.event_closed_at
            or close != covered.event_closed_at
            or identity.decision_cutoff_at != planned.decision_cutoff_at
            or identity.decision_cutoff_at != covered.decision_cutoff_at
            or decision != covered.joint_decision_at
            or (decision is not None) != complete
            or complete
            != all(side.status == "covered_computational" for side in covered.sides)
            or (not complete and (prefix.bitcoin or prefix.ether))
        ):
            raise DevvalValidationInputError("validation_input_event_changed")

        sides = []
        readiness = []
        prefixes = (prefix.bitcoin, prefix.ether) if complete else ()
        for symbol, wires, side in zip(INSTRUMENTS, prefixes, covered.sides):
            if len(wires) != plan.history_bars:
                raise DevvalValidationInputError(
                    "validation_input_causal_prefix_changed"
                )
            rows = _read_prefix_rows(wires)
            aggregate = side.aggregate
            last = rows[-1]
            measured = side.postpublication_readback_at
            if (
                measured is None
                or not close <= require_utc(measured, "companion readback") <= decision
                or side.publication_sha256 is None
                or side.readback_sha256 is None
                or side.replay_receipt_sha256 is None
                or aggregate.instrument_id != symbol
                or aggregate.bar_closed_at != close
                or aggregate.source_row_id != last.source_row_id
                or aggregate.source_row_sha256 != last.source_row_sha256
                or aggregate.constituent_sha256 != last.source_row_sha256
                or aggregate.available_at != last.available_at
                or any(
                    row.instrument_id != symbol
                    or row.bar.closed_at != earliest + index * width
                    or row.available_at > decision
                    for index, row in enumerate(rows, 1)
                )
            ):
                raise DevvalValidationInputError(
                    "validation_input_causal_prefix_changed"
                )
            readiness.extend((measured, last.available_at))
            replay = replay_features_at(
                rows,
                as_of=decision,
                bar_horizon=horizon,
                history_bars=plan.history_bars,
                signal_alpha=plan.signal_alpha,
                dynamics_window=plan.dynamics_window,
                momentum_fast_bars=plan.momentum_fast_bars,
                momentum_slow_bars=plan.momentum_slow_bars,
                pivot_left_bars=plan.pivot_left_bars,
                pivot_right_bars=plan.pivot_right_bars,
            )
            sides.append(
                ValidationFeatureSideV1(
                    instrument_id=symbol,
                    aggregate_sha256=aggregate.canonical_sha256(),
                    publication_sha256=side.publication_sha256,
                    readback_sha256=side.readback_sha256,
                    market_replay_receipt_sha256=side.replay_receipt_sha256,
                    feature_replay_sha256=replay.replay_sha256,
                    feature_replay_json=replay.model_dump_json(),
                )
            )
        if complete and (
            max(readiness) != decision
            or any(
                getattr(sides[0], field) == getattr(sides[1], field)
                for field in (
                    "publication_sha256",
                    "readback_sha256",
                    "market_replay_receipt_sha256",
                )
            )
        ):
            raise DevvalValidationInputError("validation_input_joint_readiness_changed")
        result = _freeze(
            ValidationFeatureProjectionV1(
                identity=identity,
                prefix_sha256=expected_prefix_sha256,
                blueprint_sha256=expected_blueprint_sha256,
                study_plan_sha256=expected_study_plan_sha256,
                feature_plan_sha256=plan.canonical_sha256,
                scorer_config_sha256=schedule.legacy_scorer_config_sha256,
                source_commit=source.commit,
                source_tree=source.tree,
                source_manifest_sha256=source.manifest_sha256,
                source_archive_sha256=schedule.source_archive_sha256,
                status="complete_computational" if complete else "incomplete",
                sides=(sides[0], sides[1]) if complete else None,
            ),
            contract_type=ValidationFeatureProjectionV1,
        )
        if len(result.payload) > _MAX_PROJECTION_BYTES:
            raise DevvalValidationInputError("validation_input_projection_too_large")
        return result
    except DevvalValidationInputError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
        raise DevvalValidationInputError(
            "validation_input_projection_refused"
        ) from None


def verify_label_free_validation_inputs_v1(
    *,
    projection_payload: bytes,
    expected_projection_sha256: str,
    raw_event_payload: bytes,
    expected_raw_event_sha256: str,
) -> tuple[ValidationFeatureProjectionV1, JointDevvalRawScoreEventV1]:
    """Evaluator ingress accepts only label-free schema bytes and exact pins."""
    try:
        projection = _read(
            projection_payload,
            expected_projection_sha256,
            ValidationFeatureProjectionV1,
            _MAX_PROJECTION_BYTES,
        )
        raw = _read(
            raw_event_payload,
            expected_raw_event_sha256,
            JointDevvalRawScoreEventV1,
            _MAX_EVENT_BYTES,
        )
        identity = projection.identity
        if (
            raw.schedule_sha256 != identity.schedule_sha256
            or raw.coverage_sha256 != identity.coverage_sha256
            or raw.batch_sha256 != identity.batch_sha256
            or raw.config_sha256 != projection.scorer_config_sha256
            or raw.slot_ordinal != identity.slot_ordinal
            or raw.partition != identity.partition
            or raw.event_closed_at != identity.event_closed_at
            or raw.joint_decision_at != identity.decision_at
            or raw.decision_cutoff_at != identity.decision_cutoff_at
            or raw.status != projection.status
        ):
            raise DevvalValidationInputError("validation_input_raw_event_changed")
        if projection.sides is None:
            if any(
                side.instrument_id != symbol
                or side.aggregate.instrument_id != symbol
                or side.aggregate.bar_closed_at != identity.event_closed_at
                or side.status != "missing"
                or side.raw_result is not None
                or side.raw_result_sha256 is not None
                for symbol, side in zip(INSTRUMENTS, raw.sides, strict=True)
            ):
                raise DevvalValidationInputError("validation_input_raw_side_changed")
            return projection, raw
        for feature, side in zip(projection.sides, raw.sides, strict=True):
            result = side.raw_result
            if (
                result is None
                or side.coverage_status != "covered_computational"
                or side.status != result.status
                or side.instrument_id != feature.instrument_id
                or side.aggregate.canonical_sha256() != feature.aggregate_sha256
                or side.publication_sha256 != feature.publication_sha256
                or side.readback_sha256 != feature.readback_sha256
                or side.market_replay_receipt_sha256
                != feature.market_replay_receipt_sha256
                or side.raw_result_sha256 != result.canonical_sha256()
                or result.market_replay_receipt_sha256
                != feature.market_replay_receipt_sha256
                or result.config_sha256 != raw.config_sha256
                or result.instrument_id != feature.instrument_id
                or result.decision_at != identity.decision_at
                or result.source_checkpoint.commit != projection.source_commit
                or result.source_checkpoint.tree != projection.source_tree
                or result.source_checkpoint.manifest_sha256
                != projection.source_manifest_sha256
                or result.source_archive_sha256 != projection.source_archive_sha256
            ):
                raise DevvalValidationInputError("validation_input_raw_side_changed")
        return projection, raw
    except DevvalValidationInputError:
        raise
    except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
        raise DevvalValidationInputError("validation_input_readback_refused") from None
