"""Build the frozen inference handoff from already obtained child readbacks.

This computational path opens no source paths and makes no database or
provider calls. Child readbacks and their independent pins are prerequisites.
It does not establish campaign-wide first access or consumption authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from app.domain.source_primitives import sha
from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.blind_window_dataset import CompleteBlindWindowDataset
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    _verified_receipt,
    fitted_holdout_reservation_sha256,
)
from app.mie.validation.controlled_holdout_reader import MAX_MANIFEST_BYTES
from app.mie.validation.fitted_holdout_aggregation_v1 import (
    FittedHoldoutAggregationV1,
    aggregate_fitted_holdout_readback_v1,
)
from app.mie.validation.fitted_holdout_inference_v1 import (
    FittedHoldoutChildFeatureSourceV1,
    FittedHoldoutFeatureEventV1,
    FittedHoldoutFeatureInventoryV1,
    FittedHoldoutFeatureSlotV1,
    FittedHoldoutForecastsV1,
    infer_fitted_holdout_v1,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
)
from app.mie.validation.prospective_campaign_v2 import (
    BAR_SECONDS,
    INSTRUMENTS,
    MAX_CAMPAIGN_CHILDREN,
    ROWS_PER_FOUR_HOURS,
    CampaignChildV2,
    ProspectiveCampaignV2,
    verify_prospective_campaign_v2,
)
from app.mie.validation.prospective_capture_schedule import ProspectiveCaptureScheduleV1
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    _MAX_FITTED_BINDING_BYTES,
    DevvalFittedCandidateBindingV1,
)
from app.mie.validation.replay import (
    FrozenFeatureReplayPlanV2,
    PointInTimeBar,
    replay_features_at,
)

_BAR_WIDTH = timedelta(seconds=BAR_SECONDS)
_MINUTES_PER_BAR = BAR_SECONDS // 60


class FittedHoldoutFeatureInventoryError(ValueError):
    """A frozen declaration, custody pin, or causal source grid differs."""


@dataclass(frozen=True, slots=True, repr=False)
class FittedHoldoutChildReadbackInputV1:
    schedule: ProspectiveCaptureScheduleV1
    candidate_inputs: FittedCandidateInputsV1
    readback: ControlledFittedHoldoutReadV1
    receipt_bytes: bytes
    coordinate_plan_bytes: bytes
    expected_binding_sha256: str
    expected_receipt_sha256: str
    expected_reservation_sha256: str


class FittedHoldoutFeatureChildLineageV1(Gate3Contract):
    ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    seal_sha256: Sha256
    schedule_sha256: Sha256
    coordinate_plan_sha256: Sha256
    binding_sha256: Sha256
    receipt_sha256: Sha256
    reservation_sha256: Sha256
    manifest_sha256: Sha256
    dataset_sha256: Sha256
    minute_tape_sha256: Sha256
    original_minutes_sha256: Sha256
    aggregation_sha256: Sha256


class FittedHoldoutFeatureLineageV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_holdout_feature_lineage.v1"] = (
        "ctcc.mie.fitted_holdout_feature_lineage.v1"
    )
    campaign_sha256: Sha256
    campaign_child_chain_sha256: Sha256
    anchor_binding_sha256: Sha256
    feature_plan_sha256: Sha256
    feature_inventory_sha256: Sha256
    fitted_model_sha256: Sha256
    children: tuple[FittedHoldoutFeatureChildLineageV1, ...] = Field(
        min_length=1, max_length=MAX_CAMPAIGN_CHILDREN
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independent_source_custody_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


@dataclass(frozen=True, slots=True)
class FittedHoldoutFeatureInventoryBuildV1:
    inventory: FrozenGate3Artifact[FittedHoldoutFeatureInventoryV1]
    lineage: FrozenGate3Artifact[FittedHoldoutFeatureLineageV1]


@dataclass(frozen=True, slots=True, repr=False)
class FittedHoldoutForecastReplayV1:
    features: FittedHoldoutFeatureInventoryBuildV1
    forecasts: FrozenGate3Artifact[FittedHoldoutForecastsV1]


@dataclass(frozen=True, slots=True, repr=False)
class _FittedHoldoutSourceReplayV1:
    campaign: ProspectiveCampaignV2
    plan: FrozenFeatureReplayPlanV2
    features: FittedHoldoutFeatureInventoryBuildV1
    rows: tuple[tuple[PointInTimeBar, ...], tuple[PointInTimeBar, ...]]


def _checked_child_binding(
    child: FittedHoldoutChildReadbackInputV1,
    reference: CampaignChildV2,
    campaign_first_access_at: datetime,
) -> DevvalFittedCandidateBindingV1:
    inputs = child.candidate_inputs
    binding = inputs.binding
    if (
        type(binding) is not FrozenGate3Artifact
        or type(binding.payload) is not bytes
        or not 1 <= len(binding.payload) <= _MAX_FITTED_BINDING_BYTES
        or binding.sha256 != child.expected_binding_sha256
        or inputs.expected_binding_sha256 != child.expected_binding_sha256
        or inputs.expected_preregistration_sha256 != reference.seal_sha256
        or type(child.coordinate_plan_bytes) is not bytes
        or not 1 <= len(child.coordinate_plan_bytes) <= 64 * 1024
        or sha(child.coordinate_plan_bytes) != reference.coordinate_plan_sha256
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_child_pin_changed")
    checked = _verify(
        binding.payload,
        expected_sha256=child.expected_binding_sha256,
        contract_type=DevvalFittedCandidateBindingV1,
    )
    reservation = child.readback.reservation
    if (
        checked.preregistration_sha256 != reference.seal_sha256
        or checked.holdout_coordinate_sha256 != reference.coordinate_plan_sha256
        or fitted_holdout_reservation_sha256(reservation)
        != child.expected_reservation_sha256
        or reservation.seal_sha256 != reference.seal_sha256
        or reservation.binding_sha256 != child.expected_binding_sha256
        or reservation.receipt_sha256 != child.expected_receipt_sha256
        or require_utc(reservation.reserved_at, "reserved_at")
        < campaign_first_access_at
    ):
        raise FittedHoldoutFeatureInventoryError(
            "holdout_feature_child_custody_changed"
        )
    return checked


def _require_scheduled_manifest(
    child: FittedHoldoutChildReadbackInputV1,
    *,
    seal_sha256: str,
    coordinate_plan_sha256: str,
    expected_rows: int,
) -> None:
    receipt = _verified_receipt(
        child.candidate_inputs, child.receipt_bytes, child.expected_receipt_sha256
    )
    dataset = receipt.holdout_dataset
    manifest_bytes = child.readback.manifest_bytes
    if (
        type(manifest_bytes) is not bytes
        or not 1 <= len(manifest_bytes) <= MAX_MANIFEST_BYTES
        or child.readback.reservation.dataset_sha256 != dataset.content_sha256
        or child.readback.reservation.reserved_at < receipt.recorded_at
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_manifest_pin_changed")
    manifest = _verify(
        manifest_bytes,
        expected_sha256=dataset.manifest_sha256,
        contract_type=CompleteBlindWindowDataset,
    )
    if (
        manifest.preregistration_sha256 != seal_sha256
        or manifest.coordinate_plan_sha256 != coordinate_plan_sha256
        or len(manifest.rows) != expected_rows
        or any(
            row.capture_plan_sha256 != plan.canonical_sha256()
            for row, plan in zip(manifest.rows, child.schedule.plans, strict=True)
        )
    ):
        raise FittedHoldoutFeatureInventoryError(
            "holdout_feature_schedule_plan_changed"
        )


def _append_child_bars(
    report: FittedHoldoutAggregationV1,
    reference: CampaignChildV2,
    plan: FrozenFeatureReplayPlanV2,
    stitched: tuple[list[PointInTimeBar], list[PointInTimeBar]],
    seen_receipts: set[str],
    seen_bar_ids: set[str],
) -> None:
    if (
        report.seal_sha256 != reference.seal_sha256
        or report.coordinate_plan_sha256 != reference.coordinate_plan_sha256
        or report.start_at != reference.start_at
        or report.end_at != reference.end_at
        or report.instrument_ids != INSTRUMENTS
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_aggregation_changed")
    bar_count = reference.expected_rows // ROWS_PER_FOUR_HOURS
    for symbol_index, (series, output) in enumerate(
        zip(report.series, stitched, strict=True)
    ):
        frame = next(
            item for item in series.timeframes if item.horizon == plan.bar_horizon
        )
        if (
            frame.excluded_leading_minutes
            or frame.excluded_trailing_minutes
            or frame.input_minute_count != bar_count * _MINUTES_PER_BAR
            or len(frame.bars) != bar_count
        ):
            raise FittedHoldoutFeatureInventoryError(
                "holdout_feature_child_grid_changed"
            )
        for index, aggregate in enumerate(frame.bars):
            row = aggregate.row
            close = reference.start_at + (index + 1) * _BAR_WIDTH
            receipts = set(aggregate.capture_receipt_sha256s)
            if (
                row.instrument_id != INSTRUMENTS[symbol_index]
                or row.bar.closed_at != close
                or not close <= row.available_at < reference.first_permitted_access_at
                or aggregate.first_ordinal != index * ROWS_PER_FOUR_HOURS + symbol_index
                or aggregate.ordinal_stride != len(INSTRUMENTS)
                or row.source_row_id in seen_bar_ids
                or len(receipts) != _MINUTES_PER_BAR
                or not receipts.isdisjoint(seen_receipts)
            ):
                raise FittedHoldoutFeatureInventoryError(
                    "holdout_feature_child_constituent_changed"
                )
            seen_bar_ids.add(row.source_row_id)
            seen_receipts.update(receipts)
            output.append(row)


def _causal_feature_events(
    campaign: ProspectiveCampaignV2,
    plan: FrozenFeatureReplayPlanV2,
    stitched: tuple[list[PointInTimeBar], list[PointInTimeBar]],
    child_limits: list[datetime],
) -> tuple[FittedHoldoutFeatureEventV1, ...]:
    bar_count = campaign.expected_rows // ROWS_PER_FOUR_HOURS
    lead = plan.outcome_horizon_seconds // BAR_SECONDS
    if (
        any(len(series) != bar_count for series in stitched)
        or len(child_limits) != bar_count
        or bar_count - plan.history_bars + 1 - lead < campaign.minimum_candidate_bars
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_global_grid_changed")
    result = []
    for index in range(plan.history_bars - 1, bar_count - lead):
        close = campaign.start_at + (index + 1) * _BAR_WIDTH
        decision = max(series[index].available_at for series in stitched)
        if any(
            series[index].bar.closed_at != close for series in stitched
        ) or not close <= decision < min(close + _BAR_WIDTH, child_limits[index]):
            raise FittedHoldoutFeatureInventoryError(
                "holdout_feature_decision_noncausal"
            )
        slots = []
        left = index - plan.history_bars + 1
        for series in stitched:
            replay = replay_features_at(
                tuple(series[left : index + 1]),
                as_of=decision,
                bar_horizon=plan.bar_horizon,
                history_bars=plan.history_bars,
                signal_alpha=plan.signal_alpha,
                dynamics_window=plan.dynamics_window,
                momentum_fast_bars=plan.momentum_fast_bars,
                momentum_slow_bars=plan.momentum_slow_bars,
                pivot_left_bars=plan.pivot_left_bars,
                pivot_right_bars=plan.pivot_right_bars,
            )
            slots.append(
                FittedHoldoutFeatureSlotV1(
                    current_bar_available_at=series[index].available_at,
                    replay_sha256=replay.replay_sha256,
                    replay_json=replay.model_dump_json(),
                )
            )
        result.append(
            FittedHoldoutFeatureEventV1(bar_closed_at=close, slots=(slots[0], slots[1]))
        )
    return tuple(result)


def _rebuild_fitted_holdout_source_v1(
    *,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    feature_plan: FrozenFeatureReplayPlanV2,
    children: tuple[FittedHoldoutChildReadbackInputV1, ...],
) -> _FittedHoldoutSourceReplayV1:
    """Retain the exact same verified aggregates for later outcome replay."""
    if (
        type(campaign_bytes) is not bytes
        or not 1 <= len(campaign_bytes) <= 64 * 1024
        or type(feature_plan) is not FrozenFeatureReplayPlanV2
        or type(children) is not tuple
        or not 1 <= len(children) <= MAX_CAMPAIGN_CHILDREN
        or any(
            type(child) is not FittedHoldoutChildReadbackInputV1
            or type(child.schedule) is not ProspectiveCaptureScheduleV1
            or type(child.candidate_inputs) is not FittedCandidateInputsV1
            or type(child.readback) is not ControlledFittedHoldoutReadV1
            for child in children
        )
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_inputs_invalid")
    plan = FrozenFeatureReplayPlanV2.model_validate(
        feature_plan.model_dump(mode="python")
    )
    declared = _verify(
        campaign_bytes,
        expected_sha256=expected_campaign_sha256,
        contract_type=ProspectiveCampaignV2,
    )
    campaign = verify_prospective_campaign_v2(
        campaign_bytes,
        expected_sha256=expected_campaign_sha256,
        campaign_id=declared.campaign_id,
        children=tuple(
            (child.candidate_inputs.preregistration, child.schedule)
            for child in children
        ),
        feature_plan=plan,
        expected_feature_plan_sha256=declared.feature_plan_sha256,
        minimum_candidate_bars=declared.minimum_candidate_bars,
    )
    if any(
        child.first_permitted_access_at <= child.end_at for child in campaign.children
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_zero_publication_lag")
    pairs = tuple(zip(children, campaign.children, strict=True))
    bindings = tuple(
        _checked_child_binding(child, reference, campaign.first_permitted_access_at)
        for child, reference in pairs
    )
    anchor = bindings[0]
    anchor_bytes = anchor.canonical_json_bytes()
    if anchor.feature_plan_sha256 != campaign.feature_plan_sha256 or any(
        item.model_copy(
            update={
                "preregistration_sha256": anchor.preregistration_sha256,
                "holdout_coordinate_sha256": anchor.holdout_coordinate_sha256,
            }
        ).canonical_json_bytes()
        != anchor_bytes
        for item in bindings[1:]
    ):
        raise FittedHoldoutFeatureInventoryError("holdout_feature_frozen_model_changed")

    stitched: tuple[list[PointInTimeBar], list[PointInTimeBar]] = ([], [])
    child_limits: list[datetime] = []
    seen_receipts: set[str] = set()
    seen_bar_ids: set[str] = set()
    lineages = []
    for child, reference in pairs:
        _require_scheduled_manifest(
            child,
            seal_sha256=reference.seal_sha256,
            coordinate_plan_sha256=reference.coordinate_plan_sha256,
            expected_rows=reference.expected_rows,
        )
        aggregation = aggregate_fitted_holdout_readback_v1(
            child.readback,
            inputs=child.candidate_inputs,
            receipt_bytes=child.receipt_bytes,
            coordinate_plan_bytes=child.coordinate_plan_bytes,
            expected_binding_sha256=child.expected_binding_sha256,
            expected_receipt_sha256=child.expected_receipt_sha256,
            expected_reservation_sha256=child.expected_reservation_sha256,
        )
        report = aggregation.contract
        if (
            report.binding_sha256 != child.expected_binding_sha256
            or report.receipt_sha256 != child.expected_receipt_sha256
            or report.reservation_sha256 != child.expected_reservation_sha256
            or report.dataset_sha256 != child.readback.reservation.dataset_sha256
        ):
            raise FittedHoldoutFeatureInventoryError(
                "holdout_feature_aggregation_lineage_changed"
            )
        _append_child_bars(
            report, reference, plan, stitched, seen_receipts, seen_bar_ids
        )
        child_limits.extend(
            [reference.first_permitted_access_at]
            * (reference.expected_rows // ROWS_PER_FOUR_HOURS)
        )
        lineages.append(
            FittedHoldoutFeatureChildLineageV1(
                ordinal=reference.ordinal,
                seal_sha256=report.seal_sha256,
                schedule_sha256=reference.schedule_sha256,
                coordinate_plan_sha256=report.coordinate_plan_sha256,
                binding_sha256=report.binding_sha256,
                receipt_sha256=report.receipt_sha256,
                reservation_sha256=report.reservation_sha256,
                manifest_sha256=report.manifest_sha256,
                dataset_sha256=report.dataset_sha256,
                minute_tape_sha256=report.tape_sha256,
                original_minutes_sha256=report.original_minutes_sha256,
                aggregation_sha256=aggregation.sha256,
            )
        )

    anchor_pin = children[0].expected_binding_sha256
    inventory = _freeze(
        FittedHoldoutFeatureInventoryV1(
            campaign_sha256=expected_campaign_sha256,
            binding_sha256=anchor_pin,
            feature_plan_sha256=campaign.feature_plan_sha256,
            child_sources=tuple(
                FittedHoldoutChildFeatureSourceV1(
                    seal_sha256=child.seal_sha256,
                    coordinate_plan_sha256=child.coordinate_plan_sha256,
                    minute_tape_sha256=child.minute_tape_sha256,
                )
                for child in lineages
            ),
            events=_causal_feature_events(campaign, plan, stitched, child_limits),
        ),
        contract_type=FittedHoldoutFeatureInventoryV1,
    )
    lineage = _freeze(
        FittedHoldoutFeatureLineageV1(
            campaign_sha256=expected_campaign_sha256,
            campaign_child_chain_sha256=campaign.child_chain_sha256,
            anchor_binding_sha256=anchor_pin,
            feature_plan_sha256=campaign.feature_plan_sha256,
            feature_inventory_sha256=inventory.sha256,
            fitted_model_sha256=anchor.fitted_model_sha256,
            children=tuple(lineages),
        ),
        contract_type=FittedHoldoutFeatureLineageV1,
    )
    return _FittedHoldoutSourceReplayV1(
        campaign=campaign,
        plan=plan,
        features=FittedHoldoutFeatureInventoryBuildV1(
            inventory=inventory, lineage=lineage
        ),
        rows=(tuple(stitched[0]), tuple(stitched[1])),
    )


def build_fitted_holdout_feature_inventory_v1(
    *,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    feature_plan: FrozenFeatureReplayPlanV2,
    children: tuple[FittedHoldoutChildReadbackInputV1, ...],
) -> FittedHoldoutFeatureInventoryBuildV1:
    """Reproduce every synchronized feature slot from exact prior custody bytes."""
    return _rebuild_fitted_holdout_source_v1(
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        feature_plan=feature_plan,
        children=children,
    ).features


def replay_fitted_holdout_forecasts_v1(
    *,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    feature_plan: FrozenFeatureReplayPlanV2,
    children: tuple[FittedHoldoutChildReadbackInputV1, ...],
    model_bytes: bytes,
) -> FittedHoldoutForecastReplayV1:
    """Replay the source-bound features and the pinned fit in one offline call."""
    features = build_fitted_holdout_feature_inventory_v1(
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        feature_plan=feature_plan,
        children=children,
    )
    anchor = children[0].candidate_inputs
    forecasts = infer_fitted_holdout_v1(
        inventory_bytes=features.inventory.payload,
        expected_inventory_sha256=features.inventory.sha256,
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        binding_bytes=anchor.binding.payload,
        expected_binding_sha256=children[0].expected_binding_sha256,
        seal_bytes=anchor.preregistration.canonical_json_bytes(),
        model_bytes=model_bytes,
        feature_plan=feature_plan,
    )
    return FittedHoldoutForecastReplayV1(features=features, forecasts=forecasts)
