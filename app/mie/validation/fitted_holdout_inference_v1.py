"""Apply frozen Development-only bins to an independently pinned feature grid.

The caller must obtain the inventory pin from a separate source verifier. The
legacy replay snapshot has no measured receipt proof on its own. This module
checks the declared chronology and exact artifact joins without opening data,
reconstructing source custody, fitting, or accepting evaluation outcomes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import FrozenGate3Artifact, _freeze, _verify
from app.mie.validation.contracts import Gate3Claim, Gate3Contract, Sha256, require_utc
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    BAR_SECONDS,
    INSTRUMENTS,
    MAX_CAMPAIGN_CHILDREN,
    MAX_CAMPAIGN_ROWS,
    ROWS_PER_FOUR_HOURS,
    ProspectiveCampaignV2,
    _protocol_sha256,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    _bin_index,
    _fitted_bin_probability,
    _FittedModel,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    _BAR_SECONDS as _MINUTE_SECONDS,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    _MAX_FITTED_BINDING_BYTES,
    _SOURCE,
    _SOURCE_VERSION,
    DevvalFittedCandidateBindingV1,
)
from app.mie.validation.replay import (
    FrozenFeatureReplayPlanV2,
    PointInTimeReplaySnapshot,
)

_FOUR_HOURS = timedelta(seconds=BAR_SECONDS)
_MAX_EVENTS = MAX_CAMPAIGN_ROWS // ROWS_PER_FOUR_HOURS
_Probability = Annotated[Decimal, Field(ge=0, le=1)]


class FittedHoldoutInferenceError(ValueError):
    """A frozen input or its required common-cohort slot differs."""


class FittedHoldoutChildFeatureSourceV1(Gate3Contract):
    seal_sha256: Sha256
    coordinate_plan_sha256: Sha256
    minute_tape_sha256: Sha256


class FittedHoldoutFeatureSlotV1(Gate3Contract):
    current_bar_available_at: datetime
    replay_sha256: Sha256
    # Retain native JSON so Gate3 decimal normalization cannot change the
    # existing replay identity when the outer inventory is serialized.
    replay_json: str = Field(min_length=1, max_length=16 * 1024)

    @field_validator("current_bar_available_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value, "current_bar_available_at")


class FittedHoldoutFeatureEventV1(Gate3Contract):
    bar_closed_at: datetime
    slots: tuple[FittedHoldoutFeatureSlotV1, FittedHoldoutFeatureSlotV1]

    @field_validator("bar_closed_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        return require_utc(value, "bar_closed_at")


class FittedHoldoutFeatureInventoryV1(Gate3Contract):
    """Independent feature-verification handoff, with no outcome fields."""

    schema_version: Literal["ctcc.mie.fitted_holdout_feature_inventory.v1"] = (
        "ctcc.mie.fitted_holdout_feature_inventory.v1"
    )
    campaign_sha256: Sha256
    binding_sha256: Sha256
    feature_plan_sha256: Sha256
    child_sources: tuple[FittedHoldoutChildFeatureSourceV1, ...] = Field(
        min_length=1, max_length=MAX_CAMPAIGN_CHILDREN
    )
    events: tuple[FittedHoldoutFeatureEventV1, ...] = Field(
        min_length=1, max_length=_MAX_EVENTS
    )


class FittedHoldoutForecastSlotV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    replay_sha256: Sha256
    source_rows_sha256: Sha256
    bin_index: int = Field(ge=0, le=31)
    probability: _Probability | None


class FittedHoldoutForecastEventV1(Gate3Contract):
    bar_closed_at: datetime
    decision_at: datetime
    slots: tuple[FittedHoldoutForecastSlotV1, FittedHoldoutForecastSlotV1]


class FittedHoldoutForecastsV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_holdout_forecasts.v1"] = (
        "ctcc.mie.fitted_holdout_forecasts.v1"
    )
    campaign_sha256: Sha256
    campaign_child_chain_sha256: Sha256
    binding_sha256: Sha256
    feature_inventory_sha256: Sha256
    feature_plan_sha256: Sha256
    fitted_model_sha256: Sha256
    selected_trial_configuration_sha256: Sha256
    events: tuple[FittedHoldoutForecastEventV1, ...] = Field(
        min_length=1, max_length=_MAX_EVENTS
    )
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    independent_source_custody_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    promotion_eligible: Literal[False] = False
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


def _checked[T: Gate3Contract](
    payload: bytes, pin: str, contract: type[T], limit: int
) -> T:
    if type(payload) is not bytes or not 1 <= len(payload) <= limit:
        raise FittedHoldoutInferenceError("fitted_inference_payload_invalid")
    return _verify(payload, expected_sha256=pin, contract_type=contract)


def _require_frozen_protocol(
    campaign: ProspectiveCampaignV2,
    binding: DevvalFittedCandidateBindingV1,
    seal: Gate3ProspectivePreregistration,
    model: _FittedModel,
    plan: FrozenFeatureReplayPlanV2,
) -> None:
    child = campaign.children[0]
    window = seal.prospective_holdout
    candidate = seal.candidate
    source_pin = binding.source_checkpoint.canonical_sha256()
    if (
        campaign.source_tree_sha256 != seal.source_tree_sha256
        or campaign.protocol_sha256 != _protocol_sha256(seal)
        or seal.source_tree_sha256 != source_pin
        or candidate.source_sha256 != source_pin
        or candidate.selected_trial_id != model.trial.trial_id
        or candidate.model_version != model.model_version
        or candidate.configuration_sha256 != binding.selected_trial_configuration_sha256
        or model.trial.canonical_sha256() != binding.selected_trial_configuration_sha256
        or seal.cost_model != model.cost_model
        or seal.evaluation.trials.canonical_sha256() != binding.trial_registry_sha256
        or seal.walk_forward_plan_sha256 != binding.study_plan_sha256
        or seal.training_dataset.canonical_sha256() != binding.training_dataset_sha256
        or child.seal_sha256 != binding.preregistration_sha256
        or child.coordinate_plan_sha256 != binding.holdout_coordinate_sha256
        or seal.bar_construction.interval_seconds != _MINUTE_SECONDS
        or window.bar_interval_seconds != _MINUTE_SECONDS
        or window.artifact_interval_seconds != _MINUTE_SECONDS
        or window.source != _SOURCE
        or window.source_version != _SOURCE_VERSION
        or window.coordinate_plan_sha256 != child.coordinate_plan_sha256
        or window.holdout_id != child.holdout_id
        or window.instrument_ids != INSTRUMENTS
        or window.start_at != child.start_at
        or window.end_at != child.end_at
        or window.expected_rows != child.expected_rows
        or seal.created_at != child.seal_declared_at
        or window.first_permitted_access_at != child.first_permitted_access_at
        or any(
            item.first_permitted_access_at <= item.end_at for item in campaign.children
        )
        or campaign.feature_plan_sha256 != binding.feature_plan_sha256
        or plan.canonical_sha256 != binding.feature_plan_sha256
        or plan.history_bars != campaign.history_bars
        or plan.bar_horizon.label != "4H"
        or plan.bar_horizon.seconds != BAR_SECONDS
        or plan.outcome_horizon_seconds != campaign.outcome_horizon_seconds
        or plan.outcome_horizon_seconds != seal.outcome_label.horizon_seconds
        or plan.positive_threshold != seal.outcome_label.positive_threshold
    ):
        raise FittedHoldoutInferenceError("fitted_inference_protocol_mismatch")
    if (
        len(seal.features) != 1
        or seal.features[0].feature_id != model.trial.feature
        or seal.features[0].feature_version != plan.feature_version
        or {item.name: item.value for item in seal.features[0].parameters}
        != {
            "feature_plan_sha256": binding.feature_plan_sha256,
            "history_bars": plan.history_bars,
        }
        or len(model.bins) != len(model.trial.bin_edges) + 1
        or any(
            item.positives > item.observations
            or item.probability
            != _fitted_bin_probability(model.trial, item.observations, item.positives)
            for item in model.bins
        )
    ):
        raise FittedHoldoutInferenceError("fitted_inference_feature_or_bin_mismatch")


def _forecast_events(
    campaign: ProspectiveCampaignV2,
    inventory: FittedHoldoutFeatureInventoryV1,
    plan: FrozenFeatureReplayPlanV2,
    model: _FittedModel,
) -> tuple[FittedHoldoutForecastEventV1, ...]:
    expected_children = tuple(
        (child.seal_sha256, child.coordinate_plan_sha256) for child in campaign.children
    )
    actual_children = tuple(
        (child.seal_sha256, child.coordinate_plan_sha256)
        for child in inventory.child_sources
    )
    if expected_children != actual_children or len(
        {child.minute_tape_sha256 for child in inventory.child_sources}
    ) != len(expected_children):
        raise FittedHoldoutInferenceError("fitted_inference_child_inventory_mismatch")

    first = campaign.start_at + _FOUR_HOURS * plan.history_bars
    last = campaign.end_at - timedelta(seconds=plan.outcome_horizon_seconds)
    expected_events = (last - first) // _FOUR_HOURS + 1
    if expected_events < campaign.minimum_candidate_bars or expected_events != len(
        inventory.events
    ):
        raise FittedHoldoutInferenceError("fitted_inference_event_inventory_mismatch")

    used_histories = set()
    results = []
    for index, event in enumerate(inventory.events):
        close = first + index * _FOUR_HOURS
        source_child = next(
            child
            for child in campaign.children
            if child.start_at < close <= child.end_at
        )
        decision = max(slot.current_bar_available_at for slot in event.slots)
        decision_limit = min(
            close + _FOUR_HOURS, source_child.first_permitted_access_at
        )
        if event.bar_closed_at != close or not close <= decision < decision_limit:
            raise FittedHoldoutInferenceError("fitted_inference_event_time_mismatch")
        forecasts = []
        for instrument, slot in zip(INSTRUMENTS, event.slots, strict=True):
            try:
                replay = PointInTimeReplaySnapshot.model_validate_json(slot.replay_json)
            except ValueError:
                raise FittedHoldoutInferenceError(
                    "fitted_inference_feature_slot_invalid"
                ) from None
            if (
                replay.model_dump_json() != slot.replay_json
                or replay.replay_sha256 != slot.replay_sha256
                or replay.instrument_id != instrument
                or replay.data_cutoff != close
                or replay.as_of != decision
                or not close <= slot.current_bar_available_at <= decision
                or replay.source_row_count != plan.history_bars
                or replay.feature_snapshot.feature_version != plan.feature_version
                or replay.feature_snapshot.horizon != plan.bar_horizon
                or replay.source_rows_sha256 in used_histories
            ):
                raise FittedHoldoutInferenceError(
                    "fitted_inference_feature_slot_mismatch"
                )
            used_histories.add(replay.source_rows_sha256)
            bin_index = _bin_index(replay, model.trial)
            forecasts.append(
                FittedHoldoutForecastSlotV1(
                    instrument_id=instrument,
                    replay_sha256=slot.replay_sha256,
                    source_rows_sha256=replay.source_rows_sha256,
                    bin_index=bin_index,
                    probability=model.bins[bin_index].probability,
                )
            )
        results.append(
            FittedHoldoutForecastEventV1(
                bar_closed_at=close,
                decision_at=decision,
                slots=(forecasts[0], forecasts[1]),
            )
        )
    return tuple(results)


def infer_fitted_holdout_v1(
    *,
    inventory_bytes: bytes,
    expected_inventory_sha256: str,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    binding_bytes: bytes,
    expected_binding_sha256: str,
    seal_bytes: bytes,
    model_bytes: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
) -> FrozenGate3Artifact[FittedHoldoutForecastsV1]:
    """Make no-outcome forecasts from separate, already verified artifact pins.

    Model bytes are the canonical selected model retained in the verified
    trial result. The campaign and feature inventory pins must be retained
    separately from the supplied bytes. Null probabilities remain in place.
    """
    if type(feature_plan) is not FrozenFeatureReplayPlanV2:
        raise FittedHoldoutInferenceError("fitted_inference_feature_plan_required")
    plan = FrozenFeatureReplayPlanV2.model_validate(
        feature_plan.model_dump(mode="python")
    )
    binding = _checked(
        binding_bytes,
        expected_binding_sha256,
        DevvalFittedCandidateBindingV1,
        _MAX_FITTED_BINDING_BYTES,
    )
    seal = _checked(
        seal_bytes,
        binding.preregistration_sha256,
        Gate3ProspectivePreregistration,
        256 * 1024,
    )
    campaign = _checked(
        campaign_bytes, expected_campaign_sha256, ProspectiveCampaignV2, 64 * 1024
    )
    model = _checked(model_bytes, binding.fitted_model_sha256, _FittedModel, 16 * 1024)
    _require_frozen_protocol(campaign, binding, seal, model, plan)
    inventory = _checked(
        inventory_bytes,
        expected_inventory_sha256,
        FittedHoldoutFeatureInventoryV1,
        4 * 1024 * 1024,
    )
    if (
        inventory.binding_sha256 != expected_binding_sha256
        or inventory.campaign_sha256 != expected_campaign_sha256
        or inventory.feature_plan_sha256 != binding.feature_plan_sha256
    ):
        raise FittedHoldoutInferenceError("fitted_inference_inventory_pin_mismatch")
    return _freeze(
        FittedHoldoutForecastsV1(
            campaign_sha256=expected_campaign_sha256,
            campaign_child_chain_sha256=campaign.child_chain_sha256,
            binding_sha256=expected_binding_sha256,
            feature_inventory_sha256=expected_inventory_sha256,
            feature_plan_sha256=binding.feature_plan_sha256,
            fitted_model_sha256=binding.fitted_model_sha256,
            selected_trial_configuration_sha256=(
                binding.selected_trial_configuration_sha256
            ),
            events=_forecast_events(campaign, inventory, plan, model),
        ),
        contract_type=FittedHoldoutForecastsV1,
    )
