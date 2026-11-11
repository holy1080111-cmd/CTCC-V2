"""Prepare candidate research rows from authenticated campaign aggregates."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from app.domain.source_primitives import sha
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze
from app.mie.validation.contracts import DatasetPartition, Gate3Contract, Sha256
from app.mie.validation.prospective_development_validation_plan import INSTRUMENTS
from app.mie.validation.prospective_devval_campaign_batch_v1 import (
    _EPOCH,
    DevvalCampaignBatchV1,
)
from app.mie.validation.replay import (
    ForwardDirectionLabelV2,
    FrozenFeatureReplayPlanV2,
    PointInTimeReplaySnapshot,
    forward_direction_label_v2,
    replay_features_at,
)
from app.mie.validation.splits import (
    assert_no_grouped_temporal_leakage,
    grouped_purged_walk_forward_folds,
    replay_plan_bound_walk_forward_folds,
)

_MAX_STUDY_BYTES = 64 * 1024 * 1024


class DevvalCampaignStudyError(ValueError):
    """Fixed refusal without private evidence details or source prices."""


class DevvalCampaignStudyPlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_campaign_study_plan.v1"] = (
        "ctcc.mie.devval_campaign_study_plan.v1"
    )
    campaign_sha256: Sha256
    feature_plan: FrozenFeatureReplayPlanV2
    minimum_training_events: int = Field(gt=0)
    fold_validation_events: int = Field(gt=0)
    minimum_final_validation_events: int = Field(gt=0)
    feature_dependency_seconds: int = Field(gt=0)
    label_dependency_seconds: int = Field(gt=0)
    purge_seconds: int = Field(gt=0)
    embargo_seconds: int = Field(gt=0)
    step_events: int | None = Field(default=None, gt=0)
    maximum_folds: int | None = Field(default=None, gt=0)


class DevvalCampaignStudyRowV1(Gate3Contract):
    feature_history_start_at: datetime
    feature: PointInTimeReplaySnapshot
    label: ForwardDirectionLabelV2


class DevvalCampaignStudyFoldV1(Gate3Contract):
    training_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]
    prior_embargoed_indices: tuple[int, ...]
    embargoed_indices: tuple[int, ...]


class DevvalCampaignStudyV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.devval_campaign_study.v1"] = (
        "ctcc.mie.devval_campaign_study.v1"
    )
    plan: DevvalCampaignStudyPlanV1
    study_plan_sha256: Sha256
    batch_sha256: Sha256
    completion_sha256: Sha256
    row_inventory_sha256: Sha256
    feature_plan_sha256: Sha256
    development_event_times_sha256: Sha256
    development: tuple[DevvalCampaignStudyRowV1, ...]
    validation: tuple[DevvalCampaignStudyRowV1, ...]
    development_folds: tuple[DevvalCampaignStudyFoldV1, ...]
    label_read_basis: Literal["original_source_availability_as_of"] = (
        "original_source_availability_as_of"
    )
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _partition_rows(series, plan):
    horizon = plan.bar_horizon
    width = timedelta(seconds=horizon.seconds)
    lead = plan.outcome_horizon_seconds // horizon.seconds
    history = plan.history_bars
    plan_pin = plan.canonical_sha256
    records = tuple(
        tuple(
            item.row
            for frame in member.aggregation.timeframes
            if frame.horizon == horizon
            for item in frame.bars
        )
        for member in series
    )
    cutoff = _EPOCH + timedelta(microseconds=series[0].cutoff_utc_ns // 1000)
    if (
        len(records[0]) != len(records[1])
        or len(records[0]) < history + lead
        or series[0].cutoff_utc_ns != series[1].cutoff_utc_ns
    ):
        raise DevvalCampaignStudyError("devval_campaign_study_window_insufficient")

    events = []
    rows = []
    for index in range(history - 1, len(records[0]) - lead):
        current = tuple(items[index] for items in records)
        close = current[0].bar.closed_at
        decision = max(item.available_at for item in current)
        if (
            close != current[1].bar.closed_at
            or not close <= decision < close + width
            or (events and events[-1] >= decision)
        ):
            raise DevvalCampaignStudyError("devval_campaign_study_event_changed")
        left = index - history + 1
        through = index + lead + 1
        source_windows = tuple(items[left:through] for items in records)
        # This is the earliest demonstrable source-availability boundary for
        # both labels. The artifact does not attest when an evaluator first read.
        label_as_of = max(
            item.available_at for items in source_windows for item in items
        )
        if label_as_of > cutoff:
            raise DevvalCampaignStudyError("devval_campaign_study_outcome_late")
        events.append(decision)
        for items in source_windows:
            snapshot = replay_features_at(
                items[:history],
                as_of=decision,
                bar_horizon=horizon,
                history_bars=history,
                signal_alpha=plan.signal_alpha,
                dynamics_window=plan.dynamics_window,
                momentum_fast_bars=plan.momentum_fast_bars,
                momentum_slow_bars=plan.momentum_slow_bars,
                pivot_left_bars=plan.pivot_left_bars,
                pivot_right_bars=plan.pivot_right_bars,
            )
            label = forward_direction_label_v2(
                items,
                replay_snapshot=snapshot,
                feature_plan=plan,
                expected_feature_plan_sha256=plan_pin,
                decision_at=decision,
                read_at=label_as_of,
                bar_horizon=horizon,
                outcome_horizon_seconds=plan.outcome_horizon_seconds,
                positive_threshold=plan.positive_threshold,
            )
            rows.append(
                DevvalCampaignStudyRowV1(
                    feature_history_start_at=items[0].bar.closed_at - width,
                    feature=snapshot,
                    label=label,
                )
            )
    return tuple(events), tuple(rows)


def _verify_research_boundaries(rows, plan):
    feature_limit = timedelta(seconds=plan.feature_dependency_seconds)
    label_limit = timedelta(seconds=plan.label_dependency_seconds)
    if any(
        row.label.decision_at - row.feature_history_start_at > feature_limit
        or row.label.read_at - row.label.decision_at > label_limit
        for row in rows
    ):
        raise DevvalCampaignStudyError("devval_campaign_study_dependency_understated")


def _prepare_study(
    batch: FrozenGate3Artifact[DevvalCampaignBatchV1],
    *,
    plan: DevvalCampaignStudyPlanV1,
    expected_plan_sha256: str,
) -> FrozenGate3Artifact[DevvalCampaignStudyV1]:
    """Use the batch returned by the current authenticated replay invocation."""
    if (
        type(batch) is not FrozenGate3Artifact
        or type(batch.contract) is not DevvalCampaignBatchV1
        or sha(batch.payload) != batch.sha256
        or type(plan) is not DevvalCampaignStudyPlanV1
    ):
        raise DevvalCampaignStudyError("devval_campaign_study_inputs_invalid")
    plan = DevvalCampaignStudyPlanV1.model_validate(plan.model_dump(mode="python"))
    original = batch.contract
    if (
        plan.canonical_sha256() != expected_plan_sha256
        or plan.campaign_sha256 != original.campaign_sha256
        or original.canonical_sha256() != batch.sha256
    ):
        raise DevvalCampaignStudyError("devval_campaign_study_pins_changed")
    symbols = INSTRUMENTS
    expected_series = tuple(
        (partition, symbol)
        for partition in (DatasetPartition.DEVELOPMENT, DatasetPartition.VALIDATION)
        for symbol in symbols
    )
    if tuple(
        (
            member.aggregation.plan.window.partition,
            member.aggregation.plan.instrument_id,
        )
        for member in original.series
    ) != expected_series or any(
        original.series[index].aggregation.plan.window
        != original.series[index + 1].aggregation.plan.window
        for index in (0, 2)
    ):
        raise DevvalCampaignStudyError("devval_campaign_study_series_changed")
    feature = plan.feature_plan
    dev_times, development = _partition_rows(original.series[:2], feature)
    val_times, validation = _partition_rows(original.series[2:], feature)
    if len(val_times) < plan.minimum_final_validation_events or max(
        row.label.read_at for row in development
    ) >= min(row.feature_history_start_at for row in validation):
        raise DevvalCampaignStudyError("devval_campaign_study_validation_not_admitted")
    _verify_research_boundaries(development + validation, plan)

    split = {
        "feature_dependency_seconds": plan.feature_dependency_seconds,
        "label_dependency_seconds": plan.label_dependency_seconds,
        "purge_seconds": plan.purge_seconds,
        "embargo_seconds": plan.embargo_seconds,
        "maximum_folds": plan.maximum_folds,
    }
    bound = replay_plan_bound_walk_forward_folds(
        dev_times,
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
        "symbols": symbols,
        "minimum_training_events": plan.minimum_training_events,
        "validation_events": plan.fold_validation_events,
        "step_events": plan.step_events,
        **split,
    }
    folds = grouped_purged_walk_forward_folds(observations, **grouped)
    assert_no_grouped_temporal_leakage(observations, folds, **grouped)
    if tuple(fold.event_fold for fold in folds) != bound.folds:
        raise DevvalCampaignStudyError("devval_campaign_study_fold_changed")
    for fold in folds:
        latest_label = max(
            development[index].label.read_at for index in fold.training_indices
        )
        earliest_history = min(
            development[index].feature_history_start_at
            for index in fold.validation_indices
        )
        if latest_label >= earliest_history:
            raise DevvalCampaignStudyError("devval_campaign_study_fold_overlaps")

    frozen = _freeze(
        DevvalCampaignStudyV1(
            plan=plan,
            study_plan_sha256=expected_plan_sha256,
            batch_sha256=batch.sha256,
            completion_sha256=original.completion_sha256,
            row_inventory_sha256=original.row_inventory_sha256,
            feature_plan_sha256=feature.canonical_sha256,
            development_event_times_sha256=bound.source_timestamps_sha256,
            development=development,
            validation=validation,
            development_folds=tuple(
                DevvalCampaignStudyFoldV1(
                    training_indices=fold.training_indices,
                    validation_indices=fold.validation_indices,
                    purged_indices=fold.purged_indices,
                    prior_embargoed_indices=fold.prior_embargoed_indices,
                    embargoed_indices=fold.embargoed_indices,
                )
                for fold in folds
            ),
        ),
        contract_type=DevvalCampaignStudyV1,
    )
    if len(frozen.payload) > _MAX_STUDY_BYTES:
        raise DevvalCampaignStudyError("devval_campaign_study_size")
    return frozen
