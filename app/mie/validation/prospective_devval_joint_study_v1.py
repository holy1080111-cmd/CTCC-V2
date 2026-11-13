"""Replay a complete two-symbol D/V cohort at its measured joint decisions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.batch_replay import TARGETS
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS, _ns
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _MAX_BATCH_BYTES,
    DevvalCampaignBatchV1,
)
from app.mie.validation.prospective_devval_campaign_plan_v1 import (
    freeze_prospective_devval_campaign_v1,
)
from app.mie.validation.prospective_devval_campaign_study_v1 import (
    _MAX_STUDY_BYTES,
    DevvalCampaignStudyFoldV1,
    DevvalCampaignStudyPlanV1,
    _verify_research_boundaries,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
    _require_distinct_evaluation_rows,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.mie.validation.prospective_devval_joint_companion_coverage_v1 import (
    _MAX_REPORT_BYTES,
    DevvalJointCompanionCoverageV1,
    JointCompanionSlotCoverageV1,
    _current,
    _series,
)
from app.mie.validation.prospective_devval_research_blueprint_v1 import (
    DevvalResearchBlueprintV1,
    _verify_study_policy,
    compile_research_campaign,
)
from app.mie.validation.replay import (
    ForwardDirectionLabelV2,
    PointInTimeReplaySnapshot,
    forward_direction_label_v2,
    replay_features_at,
)
from app.mie.validation.splits import (
    assert_no_grouped_temporal_leakage,
    grouped_purged_walk_forward_folds,
    replay_plan_bound_walk_forward_folds,
)

_MAX_POLICY_BYTES = 128 * 1024
_MAX_SLOTS = 4096


class DevvalJointStudyError(ValueError):
    """Fixed refusal without source data, evidence paths or credentials."""


class DevvalJointStudyRowV1(Gate3Contract):
    feature_history_start_at: datetime
    feature_replay_sha256: Sha256
    # Retain the original feature wire so outer Gate3 decimal normalization
    # cannot change a scaled Decimal's existing replay digest.
    feature_replay_json: str = Field(min_length=1, max_length=16 * 1024)
    label: ForwardDirectionLabelV2

    @field_validator("feature_history_start_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return require_utc(value, "feature_history_start_at")

    @property
    def feature(self) -> PointInTimeReplaySnapshot:
        return PointInTimeReplaySnapshot.model_validate_json(self.feature_replay_json)

    @model_validator(mode="after")
    def feature_wire_matches_label(self) -> DevvalJointStudyRowV1:
        feature = self.feature
        if (
            feature.model_dump_json() != self.feature_replay_json
            or feature.replay_sha256 != self.feature_replay_sha256
            or self.label.feature_replay_sha256 != self.feature_replay_sha256
            or self.label.feature_source_rows_sha256 != feature.source_rows_sha256
            or self.label.instrument_id != feature.instrument_id
            or self.label.decision_at != feature.as_of
            or self.label.base_bar_closed_at != feature.data_cutoff
            or self.feature_history_start_at >= feature.data_cutoff
        ):
            raise ValueError("joint study exact feature replay differs from its label")
        return self


class DevvalJointStudySlotV1(Gate3Contract):
    coverage: JointCompanionSlotCoverageV1
    status: Literal["coverage_incomplete", "replay_refused", "replayed_computational"]
    rows: tuple[DevvalJointStudyRowV1, DevvalJointStudyRowV1] | None

    @model_validator(mode="after")
    def status_matches_rows(self) -> DevvalJointStudySlotV1:
        coverage_missing = self.coverage.status == "incomplete"
        if coverage_missing != (self.status == "coverage_incomplete") or (
            self.rows is not None
        ) != (self.status == "replayed_computational"):
            raise ValueError("joint study slot status differs")
        if self.rows is not None:
            decision = self.coverage.joint_decision_at
            close = self.coverage.event_closed_at
            if decision is None or any(
                row.label.instrument_id != side.aggregate.instrument_id
                or row.label.decision_at != decision
                or row.label.base_bar_closed_at != close
                or row.label.base_row_sha256 != side.aggregate.source_row_sha256
                or row.label.base_available_at != side.aggregate.available_at
                for row, side in zip(self.rows, self.coverage.sides, strict=True)
            ):
                raise ValueError("joint study slot replay binding differs")
        return self


class DevvalJointStudyV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_joint_study.v1"] = (
        "ctcc.mie.devval_joint_study.v1"
    )
    plan: DevvalCampaignStudyPlanV1
    study_plan_sha256: Sha256
    blueprint_sha256: Sha256
    schedule_sha256: Sha256
    coverage_sha256: Sha256
    batch_sha256: Sha256
    completion_sha256: Sha256
    root_binding_sha256: Sha256
    row_inventory_sha256: Sha256
    source_checkpoint: DevvalCandidateSourceV1
    source_archive_sha256: Sha256
    feature_plan_sha256: Sha256
    slots: tuple[DevvalJointStudySlotV1, ...] = Field(
        min_length=2, max_length=_MAX_SLOTS
    )
    status: Literal["incomplete_cohort", "split_refused", "complete_computational"]
    development_event_times_sha256: Sha256 | None
    development_folds: tuple[DevvalCampaignStudyFoldV1, ...]
    label_read_basis: Literal["original_source_availability_as_of"] = (
        "original_source_availability_as_of"
    )
    independent_availability_verified: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def folds_require_full_cohort(self) -> DevvalJointStudyV1:
        incomplete = any(slot.rows is None for slot in self.slots)
        folded = self.status == "complete_computational"
        if (
            incomplete != (self.status == "incomplete_cohort")
            or bool(self.development_folds) != folded
            or (self.development_event_times_sha256 is not None) != folded
            or self.study_plan_sha256 != self.plan.canonical_sha256()
            or self.feature_plan_sha256 != self.plan.feature_plan.canonical_sha256
            or any(
                slot.coverage.ordinal != index for index, slot in enumerate(self.slots)
            )
        ):
            raise ValueError("joint study cohort or fold status differs")
        return self


def _read(payload, pin, contract, maximum):
    if (
        type(payload) is not bytes
        or not 0 < len(payload) <= maximum
        or type(pin) is not str
    ):
        raise DevvalJointStudyError("joint_study_input_invalid")
    return _verify(payload, expected_sha256=pin, contract_type=contract)


def _campaign_geometry(blueprint, schedule, batch, study_plan):
    campaign = compile_research_campaign(blueprint)
    feature = study_plan.feature_plan
    horizon = feature.bar_horizon
    history = feature.history_bars
    delay = timedelta(seconds=schedule.decision_delay_seconds)
    width = timedelta(seconds=horizon.seconds)
    lead = feature.outcome_horizon_seconds // horizon.seconds
    windows = (campaign.development, campaign.validation)
    if (
        campaign.origin != schedule.rest_origin
        or campaign.instrument_ids != schedule.instrument_ids
        or campaign.campaign_id != schedule.campaign_id
        or campaign.canonical_sha256() != schedule.campaign_plan_sha256
        or campaign.research_blueprint_sha256 != schedule.research_blueprint_sha256
        or campaign.planned_at != schedule.planned_at
        or (
            campaign.declared_source_commit,
            campaign.declared_source_tree,
            campaign.manifest_sha256,
        )
        != (
            schedule.source_checkpoint.commit,
            schedule.source_checkpoint.tree,
            schedule.source_checkpoint.manifest_sha256,
        )
        or blueprint.legacy_score_source_sha256 != schedule.legacy_scorer_config_sha256
        or (horizon.label, horizon.seconds) not in TARGETS
        or horizon.seconds != schedule.horizon_seconds
        or history * horizon.seconds + schedule.decision_delay_seconds
        > study_plan.feature_dependency_seconds
        or tuple(
            (series.aggregation.plan.window, series.aggregation.plan.instrument_id)
            for series in batch.series
        )
        != tuple((window, symbol) for window in windows for symbol in INSTRUMENTS)
    ):
        raise DevvalJointStudyError("joint_study_campaign_geometry_changed")

    expected_slots = []
    for window in windows:
        bars, remainder = divmod(window.end_at - window.start_at, width)
        if remainder or bars < history + lead:
            raise DevvalJointStudyError("joint_study_campaign_geometry_changed")
        for offset in range(history, bars - lead + 1):
            close = window.start_at + offset * width
            expected_slots.append((window.partition, close, close + delay))
    if tuple(expected_slots) != tuple(
        (slot.partition, slot.event_closed_at, slot.decision_cutoff_at)
        for slot in schedule.slots
    ):
        raise DevvalJointStudyError("joint_study_schedule_grid_changed")

    dev_cutoff = batch.series[0].cutoff_utc_ns
    val_cutoff = batch.series[2].cutoff_utc_ns
    if (
        dev_cutoff != batch.series[1].cutoff_utc_ns
        or val_cutoff != batch.series[3].cutoff_utc_ns
        or not _ns(campaign.development.end_at)
        <= dev_cutoff
        < _ns(campaign.validation.start_at)
        or val_cutoff < _ns(campaign.validation.end_at)
    ):
        raise DevvalJointStudyError("joint_study_batch_cutoffs_changed")
    if freeze_prospective_devval_campaign_v1(campaign).sha256 != batch.campaign_sha256:
        raise DevvalJointStudyError("joint_study_campaign_manifest_changed")
    return {
        DatasetPartition.DEVELOPMENT: dev_cutoff,
        DatasetPartition.VALIDATION: val_cutoff,
    }


def _join_coverage(schedule, coverage, lookup, horizon_label):
    if len(schedule.slots) != len(coverage.slots):
        raise DevvalJointStudyError("joint_study_coverage_grid_changed")
    pins_seen = [set(), set(), set()]
    complete_count = 0
    for ordinal, (expected, observed) in enumerate(
        zip(schedule.slots, coverage.slots, strict=True)
    ):
        close = expected.event_closed_at
        if (
            observed.ordinal != ordinal
            or observed.partition != expected.partition
            or observed.event_closed_at != close
            or observed.decision_cutoff_at != expected.decision_cutoff_at
        ):
            raise DevvalJointStudyError("joint_study_coverage_grid_changed")
        for symbol, side in zip(INSTRUMENTS, observed.sides, strict=True):
            if side.aggregate != _current(
                lookup, expected.partition, symbol, horizon_label, close
            ):
                raise DevvalJointStudyError("joint_study_aggregate_binding_changed")
            pins = (
                side.publication_sha256,
                side.readback_sha256,
                side.replay_receipt_sha256,
            )
            for pin, seen in zip(pins, pins_seen, strict=True):
                if pin is None:
                    continue
                if pin in seen:
                    raise DevvalJointStudyError("joint_study_companion_pin_reused")
                seen.add(pin)
            if side.status == "covered_computational" and (
                any(pin is None for pin in pins)
                or side.postpublication_readback_at is None
                or side.postpublication_readback_at.utcoffset() != timedelta(0)
                or not close
                <= side.postpublication_readback_at
                <= expected.decision_cutoff_at
            ):
                raise DevvalJointStudyError("joint_study_readback_scope_changed")

        complete = all(
            side.status == "covered_computational" for side in observed.sides
        )
        if complete != (observed.status == "covered_computational"):
            raise DevvalJointStudyError("joint_study_coverage_state_changed")
        if complete:
            decision = max(
                max(side.aggregate.available_at, side.postpublication_readback_at)
                for side in observed.sides
            )
            if (
                observed.joint_decision_at is None
                or observed.joint_decision_at.utcoffset() != timedelta(0)
                or observed.joint_decision_at != decision
                or not close <= decision <= expected.decision_cutoff_at
                or decision >= close + timedelta(seconds=schedule.horizon_seconds)
            ):
                raise DevvalJointStudyError("joint_study_decision_changed")
            complete_count += 1
        elif observed.joint_decision_at is not None:
            raise DevvalJointStudyError("joint_study_incomplete_decision_claimed")
    if (
        coverage.complete_computational_slots != complete_count
        or coverage.incomplete_slots != len(schedule.slots) - complete_count
    ):
        raise DevvalJointStudyError("joint_study_coverage_counts_changed")


def _replay_slot(slot, lookup, plan, cutoff_ns):
    feature = plan.feature_plan
    horizon = feature.bar_horizon
    width = timedelta(seconds=horizon.seconds)
    history = feature.history_bars
    lead = feature.outcome_horizon_seconds // horizon.seconds
    decision = slot.joint_decision_at
    close = slot.event_closed_at
    source_windows = tuple(
        tuple(
            lookup[(slot.partition, symbol, horizon.label, close + offset * width)].row
            for offset in range(1 - history, lead + 1)
        )
        for symbol in INSTRUMENTS
    )
    label_as_of = max(item.available_at for rows in source_windows for item in rows)
    if not decision < label_as_of or _ns(label_as_of) > cutoff_ns:
        raise DevvalJointStudyError("joint_study_label_availability_changed")
    replayed = []
    for rows in source_windows:
        snapshot = replay_features_at(
            rows[:history],
            as_of=decision,
            bar_horizon=horizon,
            history_bars=history,
            signal_alpha=feature.signal_alpha,
            dynamics_window=feature.dynamics_window,
            momentum_fast_bars=feature.momentum_fast_bars,
            momentum_slow_bars=feature.momentum_slow_bars,
            pivot_left_bars=feature.pivot_left_bars,
            pivot_right_bars=feature.pivot_right_bars,
        )
        label = forward_direction_label_v2(
            rows,
            replay_snapshot=snapshot,
            feature_plan=feature,
            expected_feature_plan_sha256=feature.canonical_sha256,
            decision_at=decision,
            read_at=label_as_of,
            bar_horizon=horizon,
            outcome_horizon_seconds=feature.outcome_horizon_seconds,
            positive_threshold=feature.positive_threshold,
        )
        if snapshot.data_cutoff != close or label.base_bar_closed_at != close:
            raise DevvalJointStudyError("joint_study_replay_close_changed")
        replayed.append(
            DevvalJointStudyRowV1(
                feature_history_start_at=rows[0].bar.closed_at - width,
                feature_replay_sha256=snapshot.replay_sha256,
                feature_replay_json=snapshot.model_dump_json(),
                label=label,
            )
        )
    pair = (replayed[0], replayed[1])
    _verify_research_boundaries(pair, plan)
    return pair


def _folds(slots, plan, minimum_folds):
    development_slots = tuple(
        slot
        for slot in slots
        if slot.coverage.partition == DatasetPartition.DEVELOPMENT
    )
    validation_slots = tuple(
        slot for slot in slots if slot.coverage.partition == DatasetPartition.VALIDATION
    )
    development = tuple(row for slot in development_slots for row in slot.rows)
    validation = tuple(row for slot in validation_slots for row in slot.rows)
    if len(validation_slots) < plan.minimum_final_validation_events or max(
        row.label.read_at for row in development
    ) >= min(row.feature_history_start_at for row in validation):
        raise DevvalJointStudyError("joint_study_final_validation_overlaps")
    development_times = tuple(
        slot.coverage.joint_decision_at for slot in development_slots
    )
    feature = plan.feature_plan
    split = {
        "feature_dependency_seconds": plan.feature_dependency_seconds,
        "label_dependency_seconds": plan.label_dependency_seconds,
        "purge_seconds": plan.purge_seconds,
        "embargo_seconds": plan.embargo_seconds,
        "maximum_folds": plan.maximum_folds,
    }
    bound = replay_plan_bound_walk_forward_folds(
        development_times,
        feature_plan=feature,
        expected_feature_plan_sha256=feature.canonical_sha256,
        minimum_training_observations=plan.minimum_training_events,
        validation_observations=plan.fold_validation_events,
        step_observations=plan.step_events,
        **split,
    )
    observations = tuple(
        (row.label.decision_at, row.label.instrument_id) for row in development
    )
    grouped = {
        "symbols": INSTRUMENTS,
        "minimum_training_events": plan.minimum_training_events,
        "validation_events": plan.fold_validation_events,
        "step_events": plan.step_events,
        **split,
    }
    folds = grouped_purged_walk_forward_folds(observations, **grouped)
    assert_no_grouped_temporal_leakage(observations, folds, **grouped)
    if (
        len(folds) < minimum_folds
        or tuple(fold.event_fold for fold in folds) != bound.folds
    ):
        raise DevvalJointStudyError("joint_study_fold_membership_changed")
    _require_distinct_evaluation_rows(development, folds)
    for fold in folds:
        latest_label = max(
            development[index].label.read_at for index in fold.training_indices
        )
        earliest_history = min(
            development[index].feature_history_start_at
            for index in fold.validation_indices
        )
        if latest_label >= earliest_history:
            raise DevvalJointStudyError("joint_study_fold_availability_overlaps")
    return bound.source_timestamps_sha256, tuple(
        DevvalCampaignStudyFoldV1(
            training_indices=fold.training_indices,
            validation_indices=fold.validation_indices,
            purged_indices=fold.purged_indices,
            prior_embargoed_indices=fold.prior_embargoed_indices,
            embargoed_indices=fold.embargoed_indices,
        )
        for fold in folds
    )


def build_devval_joint_study_v1(
    *,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    batch_payload: bytes,
    expected_batch_sha256: str,
    blueprint_payload: bytes,
    expected_blueprint_sha256: str,
    study_plan_payload: bytes,
    expected_study_plan_sha256: str,
) -> FrozenGate3Artifact[DevvalJointStudyV1]:
    """Rebuild every scheduled event using retained bytes and exact external pins."""
    try:
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
        batch = _read(
            batch_payload,
            expected_batch_sha256,
            DevvalCampaignBatchV1,
            _MAX_BATCH_BYTES,
        )
        blueprint = _read(
            blueprint_payload,
            expected_blueprint_sha256,
            DevvalResearchBlueprintV1,
            _MAX_POLICY_BYTES,
        )
        plan = _read(
            study_plan_payload,
            expected_study_plan_sha256,
            DevvalCampaignStudyPlanV1,
            _MAX_POLICY_BYTES,
        )
        if (
            coverage.schedule_sha256 != expected_schedule_sha256
            or coverage.batch_sha256 != expected_batch_sha256
            or coverage.campaign_plan_sha256 != schedule.campaign_plan_sha256
            or coverage.research_blueprint_sha256 != expected_blueprint_sha256
            or schedule.research_blueprint_sha256 != expected_blueprint_sha256
        ):
            raise DevvalJointStudyError("joint_study_parent_pins_changed")
        _verify_study_policy(blueprint, batch.campaign_sha256, plan)
        cutoffs = _campaign_geometry(blueprint, schedule, batch, plan)
        lookup = _series(batch, schedule)
        _join_coverage(schedule, coverage, lookup, plan.feature_plan.bar_horizon.label)

        slots = []
        for slot in coverage.slots:
            slot_status, rows = "coverage_incomplete", None
            if slot.status == "covered_computational":
                try:
                    rows = _replay_slot(slot, lookup, plan, cutoffs[slot.partition])
                    slot_status = "replayed_computational"
                except (ValueError, ArithmeticError):
                    slot_status = "replay_refused"
            slots.append(
                DevvalJointStudySlotV1(coverage=slot, status=slot_status, rows=rows)
            )

        cohort_status, timeline, folds = "incomplete_cohort", None, ()
        if all(slot.rows is not None for slot in slots):
            try:
                timeline, folds = _folds(
                    slots, plan, blueprint.minimum_development_folds
                )
                cohort_status = "complete_computational"
            except ValueError:
                cohort_status = "split_refused"

        result = _freeze(
            DevvalJointStudyV1(
                plan=plan,
                study_plan_sha256=expected_study_plan_sha256,
                blueprint_sha256=expected_blueprint_sha256,
                schedule_sha256=expected_schedule_sha256,
                coverage_sha256=expected_coverage_sha256,
                batch_sha256=expected_batch_sha256,
                completion_sha256=batch.completion_sha256,
                root_binding_sha256=batch.root_binding_sha256,
                row_inventory_sha256=batch.row_inventory_sha256,
                source_checkpoint=schedule.source_checkpoint,
                source_archive_sha256=schedule.source_archive_sha256,
                feature_plan_sha256=plan.feature_plan.canonical_sha256,
                slots=tuple(slots),
                status=cohort_status,
                development_event_times_sha256=timeline,
                development_folds=folds,
            ),
            contract_type=DevvalJointStudyV1,
        )
        if len(result.payload) > _MAX_STUDY_BYTES:
            raise DevvalJointStudyError("joint_study_artifact_size_invalid")
        return result
    except DevvalJointStudyError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, ArithmeticError):
        raise DevvalJointStudyError("joint_study_replay_refused") from None


def verify_devval_joint_study_v1(
    payload: bytes,
    *,
    expected_joint_study_sha256: str,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    coverage_payload: bytes,
    expected_coverage_sha256: str,
    batch_payload: bytes,
    expected_batch_sha256: str,
    blueprint_payload: bytes,
    expected_blueprint_sha256: str,
    study_plan_payload: bytes,
    expected_study_plan_sha256: str,
) -> DevvalJointStudyV1:
    """Read exact bytes and reproduce all rows and folds from the same parents."""
    try:
        retained = _read(
            payload,
            expected_joint_study_sha256,
            DevvalJointStudyV1,
            _MAX_STUDY_BYTES,
        )
    except ValueError:
        raise DevvalJointStudyError("joint_study_output_readback_refused") from None
    rebuilt = build_devval_joint_study_v1(
        schedule_payload=schedule_payload,
        expected_schedule_sha256=expected_schedule_sha256,
        coverage_payload=coverage_payload,
        expected_coverage_sha256=expected_coverage_sha256,
        batch_payload=batch_payload,
        expected_batch_sha256=expected_batch_sha256,
        blueprint_payload=blueprint_payload,
        expected_blueprint_sha256=expected_blueprint_sha256,
        study_plan_payload=study_plan_payload,
        expected_study_plan_sha256=expected_study_plan_sha256,
    )
    if rebuilt.payload != payload:
        raise DevvalJointStudyError("joint_study_output_readback_changed")
    return retained
