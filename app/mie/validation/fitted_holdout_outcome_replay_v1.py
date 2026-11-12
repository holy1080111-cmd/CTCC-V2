"""Rebuild computational outcome labels after exact forecast readback.

The caller supplies already obtained child readbacks and independently
retained forecast, inventory, campaign and replay-cutoff pins. The declared
cutoff is a deterministic replay boundary. This module observes no clock,
opens no holdout paths, and establishes neither first access nor publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc
from app.mie.validation.fitted_holdout_feature_inventory_v1 import (
    FittedHoldoutChildReadbackInputV1,
    _FittedHoldoutSourceReplayV1,
    _rebuild_fitted_holdout_source_v1,
)
from app.mie.validation.fitted_holdout_inference_v1 import (
    FittedHoldoutFeatureInventoryV1,
    FittedHoldoutForecastsV1,
    infer_fitted_holdout_v1,
)
from app.mie.validation.fitted_holdout_metrics_v1 import (
    FittedOOSOutcomeBundleV1,
    FittedOOSOutcomeEventV1,
    FittedOOSOutcomeSlotV1,
    _source_child,
)
from app.mie.validation.prospective_campaign_v2 import (
    BAR_SECONDS,
    INSTRUMENTS,
    MAX_CAMPAIGN_CHILDREN,
    MAX_CAMPAIGN_ROWS,
    ROWS_PER_FOUR_HOURS,
)
from app.mie.validation.replay import (
    FrozenFeatureReplayPlanV2,
    PointInTimeReplaySnapshot,
    forward_direction_label_v2,
)

_MAX_EVENTS = MAX_CAMPAIGN_ROWS // ROWS_PER_FOUR_HOURS
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_TimeBasis = Literal["caller_declared_replay_as_of_unverified"]
_Instrument = Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]


class FittedHoldoutOutcomeReplayError(ValueError):
    """An exact source, forecast, replay cutoff or outcome slot differs."""


class FittedOOSLabelReplayCutoffV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_label_replay_cutoff.v1"] = (
        "ctcc.mie.fitted_oos_label_replay_cutoff.v1"
    )
    campaign_sha256: Sha256
    forecast_sha256: Sha256
    feature_inventory_sha256: Sha256
    feature_lineage_sha256: Sha256
    declared_as_of: datetime
    time_basis: _TimeBasis = "caller_declared_replay_as_of_unverified"
    measured_read_time_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("declared_as_of")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return require_utc(value, "declared_as_of")


class FittedOOSOutcomeSlotEvidenceV1(Gate3Contract):
    instrument_id: _Instrument
    base_child_ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    outcome_child_ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    base_aggregation_sha256: Sha256
    outcome_aggregation_sha256: Sha256
    # Retain the original replay serialization, including Decimal encodings.
    base_row_json: str = Field(min_length=1, max_length=16 * 1024)
    outcome_row_json: str = Field(min_length=1, max_length=16 * 1024)
    latest_required_source_availability: datetime
    outcome_window_rows_sha256: Sha256


class FittedOOSOutcomeEventEvidenceV1(Gate3Contract):
    bar_closed_at: datetime
    slots: tuple[FittedOOSOutcomeSlotEvidenceV1, FittedOOSOutcomeSlotEvidenceV1]


class FittedOOSOutcomeReplayEvidenceV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_outcome_replay_evidence.v1"] = (
        "ctcc.mie.fitted_oos_outcome_replay_evidence.v1"
    )
    campaign_sha256: Sha256
    forecast_sha256: Sha256
    feature_inventory_sha256: Sha256
    feature_lineage_sha256: Sha256
    outcome_bundle_sha256: Sha256
    cutoff_sha256: Sha256
    declared_as_of: datetime
    time_basis: _TimeBasis = "caller_declared_replay_as_of_unverified"
    events: tuple[FittedOOSOutcomeEventEvidenceV1, ...] = Field(
        min_length=1, max_length=_MAX_EVENTS
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    measured_read_time_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    forecast_publication_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


@dataclass(frozen=True, slots=True, repr=False)
class FittedHoldoutOutcomeReplayV1:
    outcomes: FrozenGate3Artifact[FittedOOSOutcomeBundleV1]
    evidence: FrozenGate3Artifact[FittedOOSOutcomeReplayEvidenceV1]


def _checked[T: Gate3Contract](
    payload: bytes, pin: str, contract: type[T], limit: int = _MAX_INPUT_BYTES
) -> T:
    if type(payload) is not bytes or not 1 <= len(payload) <= limit:
        raise FittedHoldoutOutcomeReplayError("holdout_label_input_size_invalid")
    return _verify(payload, expected_sha256=pin, contract_type=contract)


def _outcome_events(
    source: _FittedHoldoutSourceReplayV1,
    forecasts: FittedHoldoutForecastsV1,
    inventory: FittedHoldoutFeatureInventoryV1,
    as_of: datetime,
) -> tuple[
    tuple[FittedOOSOutcomeEventV1, ...], tuple[FittedOOSOutcomeEventEvidenceV1, ...]
]:
    campaign, plan = source.campaign, source.plan
    lead = plan.outcome_horizon_seconds // BAR_SECONDS
    bar_count = campaign.expected_rows // ROWS_PER_FOUR_HOURS
    event_count = bar_count - plan.history_bars + 1 - lead
    if not (
        event_count == len(forecasts.events) == len(inventory.events)
        and event_count >= campaign.minimum_candidate_bars
        and all(len(series) == bar_count for series in source.rows)
    ):
        raise FittedHoldoutOutcomeReplayError("holdout_label_grid_incomplete")

    labels = []
    evidence = []
    lineage = source.features.lineage.contract.children
    for offset, (forecast, features) in enumerate(
        zip(forecasts.events, inventory.events, strict=True)
    ):
        index = plan.history_bars - 1 + offset
        close = forecast.bar_closed_at
        target_at = close + timedelta(seconds=plan.outcome_horizon_seconds)
        if features.bar_closed_at != close or target_at > campaign.end_at:
            raise FittedHoldoutOutcomeReplayError("holdout_label_event_changed")
        base_child = _source_child(campaign, close)
        outcome_child = _source_child(campaign, target_at)
        observed_slots = []
        evidence_slots = []
        for instrument, series, predicted, feature in zip(
            INSTRUMENTS, source.rows, forecast.slots, features.slots, strict=True
        ):
            base, outcome = series[index], series[index + lead]
            records = series[offset : index + lead + 1]
            ready_at = max(row.available_at for row in records)
            snapshot = PointInTimeReplaySnapshot.model_validate_json(
                feature.replay_json
            )
            if (
                predicted.instrument_id != instrument
                or base.instrument_id != instrument
                or outcome.instrument_id != instrument
                or base.bar.closed_at != close
                or outcome.bar.closed_at != target_at
                or base.available_at != feature.current_bar_available_at
                or snapshot.source_rows_sha256 != predicted.source_rows_sha256
                or snapshot.replay_sha256 != predicted.replay_sha256
                or feature.replay_sha256 != predicted.replay_sha256
                or ready_at > as_of
            ):
                raise FittedHoldoutOutcomeReplayError(
                    "holdout_label_source_slot_changed"
                )
            label = forward_direction_label_v2(
                records,
                replay_snapshot=snapshot,
                feature_plan=plan,
                expected_feature_plan_sha256=forecasts.feature_plan_sha256,
                decision_at=forecast.decision_at,
                read_at=as_of,
                bar_horizon=plan.bar_horizon,
                outcome_horizon_seconds=plan.outcome_horizon_seconds,
                positive_threshold=plan.positive_threshold,
            )
            observed_slots.append(
                FittedOOSOutcomeSlotV1(instrument_id=instrument, label=label)
            )
            evidence_slots.append(
                FittedOOSOutcomeSlotEvidenceV1(
                    instrument_id=instrument,
                    base_child_ordinal=base_child,
                    outcome_child_ordinal=outcome_child,
                    base_aggregation_sha256=lineage[base_child].aggregation_sha256,
                    outcome_aggregation_sha256=(
                        lineage[outcome_child].aggregation_sha256
                    ),
                    base_row_json=base.model_dump_json(),
                    outcome_row_json=outcome.model_dump_json(),
                    latest_required_source_availability=ready_at,
                    outcome_window_rows_sha256=label.outcome_window_rows_sha256,
                )
            )
        labels.append(
            FittedOOSOutcomeEventV1(
                bar_closed_at=close, slots=(observed_slots[0], observed_slots[1])
            )
        )
        evidence.append(
            FittedOOSOutcomeEventEvidenceV1(
                bar_closed_at=close, slots=(evidence_slots[0], evidence_slots[1])
            )
        )
    return tuple(labels), tuple(evidence)


def build_fitted_holdout_outcomes_v1(
    *,
    forecast_bytes: bytes,
    expected_forecast_sha256: str,
    inventory_bytes: bytes,
    expected_inventory_sha256: str,
    cutoff_bytes: bytes,
    expected_cutoff_sha256: str,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    feature_plan: FrozenFeatureReplayPlanV2,
    children: tuple[FittedHoldoutChildReadbackInputV1, ...],
    model_bytes: bytes,
) -> FittedHoldoutOutcomeReplayV1:
    """Reproduce complete label and provenance bytes from fixed prior inputs."""
    forecasts = _checked(
        forecast_bytes, expected_forecast_sha256, FittedHoldoutForecastsV1
    )
    inventory = _checked(
        inventory_bytes, expected_inventory_sha256, FittedHoldoutFeatureInventoryV1
    )
    cutoff = _checked(
        cutoff_bytes, expected_cutoff_sha256, FittedOOSLabelReplayCutoffV1, 4 * 1024
    )
    if (
        forecasts.campaign_sha256 != expected_campaign_sha256
        or inventory.campaign_sha256 != expected_campaign_sha256
        or cutoff.campaign_sha256 != expected_campaign_sha256
        or forecasts.feature_inventory_sha256 != expected_inventory_sha256
        or cutoff.feature_inventory_sha256 != expected_inventory_sha256
        or cutoff.forecast_sha256 != expected_forecast_sha256
    ):
        raise FittedHoldoutOutcomeReplayError("holdout_label_forecast_lock_changed")
    # No source replay occurs before all previously retained forecast/inventory
    # bytes are verified. These digests do not prove a time of publication.
    source = _rebuild_fitted_holdout_source_v1(
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        feature_plan=feature_plan,
        children=children,
    )
    if (
        source.features.inventory.payload != inventory_bytes
        or source.features.lineage.sha256 != cutoff.feature_lineage_sha256
    ):
        raise FittedHoldoutOutcomeReplayError("holdout_label_feature_source_changed")
    if cutoff.declared_as_of < max(
        source.campaign.first_permitted_access_at,
        *(child.readback.reservation.database_read_at for child in children),
    ):
        raise FittedHoldoutOutcomeReplayError("holdout_label_declared_cutoff_too_early")

    anchor = children[0].candidate_inputs
    replayed = infer_fitted_holdout_v1(
        inventory_bytes=source.features.inventory.payload,
        expected_inventory_sha256=expected_inventory_sha256,
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        binding_bytes=anchor.binding.payload,
        expected_binding_sha256=children[0].expected_binding_sha256,
        seal_bytes=anchor.preregistration.canonical_json_bytes(),
        model_bytes=model_bytes,
        feature_plan=source.plan,
    )
    if replayed.payload != forecast_bytes:
        raise FittedHoldoutOutcomeReplayError("holdout_label_forecast_replay_changed")

    labels, evidence = _outcome_events(
        source, forecasts, inventory, cutoff.declared_as_of
    )
    outcomes = _freeze(
        FittedOOSOutcomeBundleV1(
            campaign_sha256=expected_campaign_sha256,
            forecast_sha256=expected_forecast_sha256,
            events=labels,
        ),
        contract_type=FittedOOSOutcomeBundleV1,
    )
    provenance = _freeze(
        FittedOOSOutcomeReplayEvidenceV1(
            campaign_sha256=expected_campaign_sha256,
            forecast_sha256=expected_forecast_sha256,
            feature_inventory_sha256=expected_inventory_sha256,
            feature_lineage_sha256=source.features.lineage.sha256,
            outcome_bundle_sha256=outcomes.sha256,
            cutoff_sha256=expected_cutoff_sha256,
            declared_as_of=cutoff.declared_as_of,
            events=evidence,
        ),
        contract_type=FittedOOSOutcomeReplayEvidenceV1,
    )
    return FittedHoldoutOutcomeReplayV1(outcomes=outcomes, evidence=provenance)
