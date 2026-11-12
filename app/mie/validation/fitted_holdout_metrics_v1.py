"""Computational-only common-cohort scoring of frozen holdout forecasts.

All prediction, baseline, label, and policy bytes have separate external pins.
This function neither opens the holdout nor proves independent custody, the
pre-window plan publication, label source, or a unique evaluation attempt.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    _freeze,
    _verify,
)
from app.mie.validation.contracts import (
    BaselineSpec,
    Gate3Claim,
    Gate3Contract,
    Sha256,
    UncertaintyPlan,
    require_utc,
)
from app.mie.validation.fitted_holdout_inference_v1 import (
    FittedHoldoutFeatureInventoryV1,
    FittedHoldoutForecastsV1,
    infer_fitted_holdout_v1,
)
from app.mie.validation.metrics import (
    _ARITHMETIC_CONTEXT,
    brier_loss_values,
    moving_block_bootstrap_interval,
    probability_metrics,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    BAR_SECONDS,
    INSTRUMENTS,
    MAX_CAMPAIGN_CHILDREN,
    MAX_CAMPAIGN_ROWS,
    ProspectiveCampaignV2,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import _FittedModel
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedCandidateBindingV1,
)
from app.mie.validation.replay import (
    ForwardDirectionLabelV2,
    FrozenFeatureReplayPlanV2,
)

_Probability = Annotated[Decimal, Field(ge=0, le=1)]
_MAX_EVENTS = MAX_CAMPAIGN_ROWS // (240 * len(INSTRUMENTS))
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_PAIR_DOMAIN = "ctcc.mie.paired_event_window.v1"


class FittedOOSMetricsError(ValueError):
    """A pinned input, common cohort, or declared policy differs."""


class FittedOOSScorePlanV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_score_plan.v1"] = (
        "ctcc.mie.fitted_oos_score_plan.v1"
    )
    campaign_sha256: Sha256
    child_chain_sha256: Sha256
    binding_sha256: Sha256
    preregistration_sha256: Sha256
    candidate_id: str
    baseline_specs: tuple[BaselineSpec, BaselineSpec, BaselineSpec]
    outcome_horizon_seconds: int = Field(ge=BAR_SECONDS)
    positive_threshold: Decimal
    reliability_bin_count: int = Field(ge=2, le=100)
    minimum_candidate_events: int = Field(ge=1)
    minimum_scored_windows: int = Field(ge=2, le=MAX_CAMPAIGN_CHILDREN)
    minimum_brier_advantage: Decimal = Field(gt=0, le=1)
    maximum_candidate_ece: Decimal = Field(ge=0, le=1)
    uncertainty: UncertaintyPlan
    missing_policy: Literal["reject"] = "reject"
    planned_at: datetime
    independently_published_before_window: Literal[False] = False
    predictive_oos_eligible: Literal[False] = False
    execution_authority: Literal[False] = False

    @field_validator("planned_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return require_utc(value, "planned_at")


class FittedOOSBaselineSlotV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    constant_prevalence: _Probability | None
    frozen_legacy_score: _Probability | None
    no_skill: _Probability | None


class FittedOOSBaselineEventV1(Gate3Contract):
    bar_closed_at: datetime
    slots: tuple[FittedOOSBaselineSlotV1, FittedOOSBaselineSlotV1]

    @field_validator("bar_closed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return require_utc(value, "bar_closed_at")


class FittedOOSBaselineBundleV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_baselines.v1"] = (
        "ctcc.mie.fitted_oos_baselines.v1"
    )
    campaign_sha256: Sha256
    preregistration_sha256: Sha256
    baseline_specs: tuple[BaselineSpec, BaselineSpec, BaselineSpec]
    events: tuple[FittedOOSBaselineEventV1, ...] = Field(
        min_length=1, max_length=_MAX_EVENTS
    )
    independent_baseline_source_verified: Literal[False] = False
    execution_authority: Literal[False] = False


class FittedOOSOutcomeSlotV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    label: ForwardDirectionLabelV2 | None


class FittedOOSOutcomeEventV1(Gate3Contract):
    bar_closed_at: datetime
    slots: tuple[FittedOOSOutcomeSlotV1, FittedOOSOutcomeSlotV1]

    @field_validator("bar_closed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return require_utc(value, "bar_closed_at")


class FittedOOSOutcomeBundleV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_outcomes.v1"] = (
        "ctcc.mie.fitted_oos_outcomes.v1"
    )
    campaign_sha256: Sha256
    forecast_sha256: Sha256
    events: tuple[FittedOOSOutcomeEventV1, ...] = Field(
        min_length=1, max_length=_MAX_EVENTS
    )
    independent_outcome_source_verified: Literal[False] = False
    execution_authority: Literal[False] = False


class FittedOOSScoredSlotV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    replay_sha256: Sha256
    candidate_probability: _Probability | None
    constant_prevalence: _Probability | None
    frozen_legacy_score: _Probability | None
    no_skill: _Probability | None
    outcome: bool | None
    missing: tuple[
        Literal[
            "candidate_probability",
            "constant_prevalence",
            "frozen_legacy_score",
            "no_skill",
            "outcome",
        ],
        ...,
    ]


class FittedOOSScoredEventV1(Gate3Contract):
    bar_closed_at: datetime
    child_ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    slots: tuple[FittedOOSScoredSlotV1, FittedOOSScoredSlotV1]


class FittedOOSCalibrationBinV1(Gate3Contract):
    index: int = Field(ge=0)
    lower_bound: _Probability
    upper_bound: _Probability
    mean_prediction: _Probability | None
    observed_frequency: _Probability | None
    sample_count: int = Field(ge=0)


class FittedOOSProbabilityScoreV1(Gate3Contract):
    subject_id: str
    brier_score: _Probability
    log_loss: Decimal = Field(ge=0)
    expected_calibration_error: _Probability
    sample_count: int = Field(gt=0)
    reliability_bins: tuple[FittedOOSCalibrationBinV1, ...] = Field(min_length=2)


class FittedOOSBaselineDeltaV1(Gate3Contract):
    baseline_id: str
    brier_advantage: Decimal
    log_loss_advantage: Decimal
    ece_advantage: Decimal


class FittedOOSPairedWindowBlockV1(Gate3Contract):
    baseline_id: str
    event_count: int = Field(ge=2)
    block_length: int = Field(ge=2)
    resamples: int = Field(ge=1000)
    confidence_level: Decimal = Field(gt=0, lt=1)
    derived_seed: int = Field(ge=0)
    brier_advantage_estimate: Decimal
    descriptive_lower: Decimal
    descriptive_upper: Decimal
    method_domain: Literal["ctcc.mie.paired_event_window.v1"] = _PAIR_DOMAIN
    p_value: None = None
    inferential_claim_accepted: Literal[False] = False


class FittedOOSWindowScoreV1(Gate3Contract):
    child_ordinal: int = Field(ge=0, lt=MAX_CAMPAIGN_CHILDREN)
    start_at: datetime
    end_at: datetime
    event_count: int = Field(ge=0)
    status: Literal["warmup_no_events", "incomplete", "complete_descriptive"]
    candidate: FittedOOSProbabilityScoreV1 | None
    baselines: tuple[FittedOOSProbabilityScoreV1, ...]
    deltas: tuple[FittedOOSBaselineDeltaV1, ...]
    paired_blocks: tuple[FittedOOSPairedWindowBlockV1, ...]
    uncertainty_complete: bool


class FittedOOSSymbolScoreV1(Gate3Contract):
    instrument_id: Literal["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    candidate: FittedOOSProbabilityScoreV1
    baselines: tuple[FittedOOSProbabilityScoreV1, ...]
    deltas: tuple[FittedOOSBaselineDeltaV1, ...]


class FittedOOSMetricsV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.fitted_oos_metrics.v1"] = (
        "ctcc.mie.fitted_oos_metrics.v1"
    )
    score_plan_sha256: Sha256
    campaign_sha256: Sha256
    child_chain_sha256: Sha256
    binding_sha256: Sha256
    forecast_sha256: Sha256
    feature_inventory_sha256: Sha256
    baseline_bundle_sha256: Sha256
    outcome_bundle_sha256: Sha256
    events: tuple[FittedOOSScoredEventV1, ...] = Field(min_length=1)
    windows: tuple[FittedOOSWindowScoreV1, ...] = Field(min_length=1)
    missing_slot_count: int = Field(ge=0)
    coverage_status: Literal["complete", "incomplete"]
    overall_candidate: FittedOOSProbabilityScoreV1 | None
    overall_baselines: tuple[FittedOOSProbabilityScoreV1, ...]
    overall_deltas: tuple[FittedOOSBaselineDeltaV1, ...]
    symbol_scores: tuple[FittedOOSSymbolScoreV1, ...]
    descriptive_threshold_screen_passed: bool
    inferential_p_values: None = None
    current_claim: Literal[Gate3Claim.COMPUTATIONAL] = Gate3Claim.COMPUTATIONAL
    prewindow_publication_verified: Literal[False] = False
    independent_source_custody_verified: Literal[False] = False
    unique_evaluation_attempt_verified: Literal[False] = False
    statistical_significance_accepted: Literal[False] = False
    formal_oos_accepted: Literal[False] = False
    gate4_eligible: Literal[False] = False
    execution_authority: Literal[False] = False


def _checked[T: Gate3Contract](payload: bytes, pin: str, contract: type[T]) -> T:
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_INPUT_BYTES:
        raise FittedOOSMetricsError("fitted_oos_input_size_invalid")
    return _verify(payload, expected_sha256=pin, contract_type=contract)


def _source_child(campaign: ProspectiveCampaignV2, close: datetime) -> int:
    matches = tuple(
        child.ordinal
        for child in campaign.children
        if child.start_at < close <= child.end_at
    )
    if len(matches) != 1:
        raise FittedOOSMetricsError("fitted_oos_child_window_ambiguous")
    return matches[0]


def _join_slots(
    campaign: ProspectiveCampaignV2,
    forecasts: FittedHoldoutForecastsV1,
    inventory: FittedHoldoutFeatureInventoryV1,
    baselines: FittedOOSBaselineBundleV1,
    outcomes: FittedOOSOutcomeBundleV1,
    seal: Gate3ProspectivePreregistration,
) -> tuple[FittedOOSScoredEventV1, ...]:
    if not (
        len(forecasts.events)
        == len(inventory.events)
        == len(baselines.events)
        == len(outcomes.events)
    ):
        raise FittedOOSMetricsError("fitted_oos_event_coverage_incomplete")
    joined = []
    for predicted, source, baseline, outcome in zip(
        forecasts.events,
        inventory.events,
        baselines.events,
        outcomes.events,
        strict=True,
    ):
        close = predicted.bar_closed_at
        if any(event.bar_closed_at != close for event in (source, baseline, outcome)):
            raise FittedOOSMetricsError("fitted_oos_event_order_changed")
        ordinal = _source_child(campaign, close)
        slots = []
        for instrument, prediction, available, base, observed in zip(
            INSTRUMENTS,
            predicted.slots,
            source.slots,
            baseline.slots,
            outcome.slots,
            strict=True,
        ):
            if (
                prediction.instrument_id != instrument
                or base.instrument_id != instrument
                or observed.instrument_id != instrument
            ):
                raise FittedOOSMetricsError("fitted_oos_symbol_order_changed")
            label = observed.label
            if label is not None and (
                label.instrument_id != instrument
                or label.base_bar_closed_at != close
                or label.base_available_at != available.current_bar_available_at
                or label.decision_at != predicted.decision_at
                or label.feature_replay_sha256 != prediction.replay_sha256
                or label.feature_source_rows_sha256 != prediction.source_rows_sha256
                or label.feature_plan_sha256 != forecasts.feature_plan_sha256
                or label.bar_horizon_seconds != BAR_SECONDS
                or label.horizon_seconds != seal.outcome_label.horizon_seconds
                or label.positive_threshold != seal.outcome_label.positive_threshold
                or label.outcome_at > campaign.end_at
            ):
                raise FittedOOSMetricsError("fitted_oos_label_lineage_changed")
            if label is not None:
                _source_child(campaign, label.outcome_at)
                if label.read_at < campaign.first_permitted_access_at:
                    raise FittedOOSMetricsError("fitted_oos_label_access_too_early")
            probabilities = (
                prediction.probability,
                base.constant_prevalence,
                base.frozen_legacy_score,
                base.no_skill,
            )
            names = (
                "candidate_probability",
                "constant_prevalence",
                "frozen_legacy_score",
                "no_skill",
            )
            missing = tuple(
                name
                for name, value in zip(names, probabilities, strict=True)
                if value is None
            ) + (("outcome",) if label is None else ())
            slots.append(
                FittedOOSScoredSlotV1(
                    instrument_id=instrument,
                    replay_sha256=prediction.replay_sha256,
                    candidate_probability=prediction.probability,
                    constant_prevalence=base.constant_prevalence,
                    frozen_legacy_score=base.frozen_legacy_score,
                    no_skill=base.no_skill,
                    outcome=label.positive if label is not None else None,
                    missing=missing,
                )
            )
        joined.append(
            FittedOOSScoredEventV1(
                bar_closed_at=close, child_ordinal=ordinal, slots=(slots[0], slots[1])
            )
        )
    return tuple(joined)


def _score(subject_id: str, slots, bin_count: int) -> FittedOOSProbabilityScoreV1:
    values = tuple(value for value, _ in slots)
    labels = tuple(label for _, label in slots)
    metric = probability_metrics(values, labels, bin_count=bin_count)
    return FittedOOSProbabilityScoreV1(
        subject_id=subject_id,
        brier_score=metric.brier_score,
        log_loss=metric.log_loss,
        expected_calibration_error=metric.expected_calibration_error,
        sample_count=metric.sample_count,
        reliability_bins=tuple(
            FittedOOSCalibrationBinV1(
                index=item.index,
                lower_bound=item.lower_bound,
                upper_bound=item.upper_bound,
                mean_prediction=item.mean_prediction,
                observed_frequency=item.observed_frequency,
                sample_count=item.sample_count,
            )
            for item in metric.reliability_bins
        ),
    )


def _scores(events, plan: FittedOOSScorePlanV1, *, instrument: str | None = None):
    rows = tuple(
        slot
        for event in events
        for slot in event.slots
        if instrument is None or slot.instrument_id == instrument
    )
    if not rows:
        raise FittedOOSMetricsError("fitted_oos_no_scored_events")
    labels = tuple(bool(slot.outcome) for slot in rows)
    fields = (
        (plan.candidate_id, "candidate_probability"),
        (plan.baseline_specs[0].baseline_id, "constant_prevalence"),
        (plan.baseline_specs[1].baseline_id, "frozen_legacy_score"),
        (plan.baseline_specs[2].baseline_id, "no_skill"),
    )
    scored = tuple(
        _score(
            subject_id,
            tuple(
                (getattr(slot, field), label)
                for slot, label in zip(rows, labels, strict=True)
            ),
            plan.reliability_bin_count,
        )
        for subject_id, field in fields
    )
    candidate, *baselines = scored
    deltas = tuple(
        FittedOOSBaselineDeltaV1(
            baseline_id=baseline.subject_id,
            brier_advantage=baseline.brier_score - candidate.brier_score,
            log_loss_advantage=baseline.log_loss - candidate.log_loss,
            ece_advantage=(
                baseline.expected_calibration_error
                - candidate.expected_calibration_error
            ),
        )
        for baseline in baselines
    )
    return candidate, tuple(baselines), deltas


def _paired_blocks(events, plan: FittedOOSScorePlanV1, ordinal: int):
    if len(events) < plan.uncertainty.block_length:
        return ()
    blocks = []
    fields = ("constant_prevalence", "frozen_legacy_score", "no_skill")
    for spec, field in zip(plan.baseline_specs, fields, strict=True):
        per_event = []
        for event in events:
            outcomes = tuple(int(slot.outcome) for slot in event.slots)
            candidate = brier_loss_values(
                tuple(slot.candidate_probability for slot in event.slots), outcomes
            )
            baseline = brier_loss_values(
                tuple(getattr(slot, field) for slot in event.slots), outcomes
            )
            with localcontext(_ARITHMETIC_CONTEXT):
                per_event.append(
                    (sum(baseline, Decimal(0)) - sum(candidate, Decimal(0)))
                    / Decimal(len(INSTRUMENTS))
                )
        domain = (
            f"{_PAIR_DOMAIN}|{plan.uncertainty.seed}|{plan.campaign_sha256}|"
            f"{ordinal}|{spec.kind.value}"
        )
        seed = int.from_bytes(hashlib.sha256(domain.encode("ascii")).digest(), "big")
        interval = moving_block_bootstrap_interval(
            tuple(per_event),
            block_length=plan.uncertainty.block_length,
            resamples=plan.uncertainty.resamples,
            confidence_level=plan.uncertainty.confidence_level,
            seed=seed,
        )
        blocks.append(
            FittedOOSPairedWindowBlockV1(
                baseline_id=spec.baseline_id,
                event_count=len(events),
                block_length=interval.block_length,
                resamples=interval.resamples,
                confidence_level=interval.confidence_level,
                derived_seed=seed,
                brier_advantage_estimate=interval.estimate,
                descriptive_lower=interval.confidence_lower,
                descriptive_upper=interval.confidence_upper,
            )
        )
    return tuple(blocks)


def score_fitted_holdout_v1(
    *,
    score_plan_bytes: bytes,
    expected_score_plan_sha256: str,
    forecast_bytes: bytes,
    expected_forecast_sha256: str,
    inventory_bytes: bytes,
    expected_inventory_sha256: str,
    baseline_bytes: bytes,
    expected_baseline_sha256: str,
    outcome_bytes: bytes,
    expected_outcome_sha256: str,
    campaign_bytes: bytes,
    expected_campaign_sha256: str,
    binding_bytes: bytes,
    expected_binding_sha256: str,
    seal_bytes: bytes,
    model_bytes: bytes,
    feature_plan: FrozenFeatureReplayPlanV2,
) -> FrozenGate3Artifact[FittedOOSMetricsV1]:
    """Score an exact paired cohort without any acceptance or trading authority."""
    plan = _checked(score_plan_bytes, expected_score_plan_sha256, FittedOOSScorePlanV1)
    campaign = _checked(campaign_bytes, expected_campaign_sha256, ProspectiveCampaignV2)
    binding = _checked(
        binding_bytes, expected_binding_sha256, DevvalFittedCandidateBindingV1
    )
    seal = _checked(
        seal_bytes, binding.preregistration_sha256, Gate3ProspectivePreregistration
    )
    if (
        plan.campaign_sha256 != expected_campaign_sha256
        or plan.child_chain_sha256 != campaign.child_chain_sha256
        or plan.binding_sha256 != expected_binding_sha256
        or plan.preregistration_sha256 != binding.preregistration_sha256
        or plan.candidate_id != seal.candidate.candidate_id
        or plan.baseline_specs != seal.baselines
        or plan.outcome_horizon_seconds != seal.outcome_label.horizon_seconds
        or plan.positive_threshold != seal.outcome_label.positive_threshold
        or plan.reliability_bin_count != seal.evaluation.reliability_bin_count
        or plan.uncertainty != seal.evaluation.uncertainty
        or not seal.created_at <= plan.planned_at < campaign.start_at
        or plan.minimum_candidate_events < campaign.minimum_candidate_bars
        or plan.minimum_scored_windows > len(campaign.children)
    ):
        raise FittedOOSMetricsError("fitted_oos_predeclared_policy_mismatch")
    original = _checked(
        forecast_bytes, expected_forecast_sha256, FittedHoldoutForecastsV1
    )
    recomputed = infer_fitted_holdout_v1(
        inventory_bytes=inventory_bytes,
        expected_inventory_sha256=expected_inventory_sha256,
        campaign_bytes=campaign_bytes,
        expected_campaign_sha256=expected_campaign_sha256,
        binding_bytes=binding_bytes,
        expected_binding_sha256=expected_binding_sha256,
        seal_bytes=seal_bytes,
        model_bytes=model_bytes,
        feature_plan=feature_plan,
    )
    if recomputed.payload != forecast_bytes:
        raise FittedOOSMetricsError("fitted_oos_forecast_replay_changed")
    model = _checked(model_bytes, binding.fitted_model_sha256, _FittedModel)
    training_count = sum(item.observations for item in model.bins)
    if not training_count:
        raise FittedOOSMetricsError("fitted_oos_development_prevalence_unavailable")
    with localcontext(_ARITHMETIC_CONTEXT):
        training_prevalence = Decimal(
            sum(item.positives for item in model.bins)
        ) / Decimal(training_count)
    inventory = _checked(
        inventory_bytes, expected_inventory_sha256, FittedHoldoutFeatureInventoryV1
    )
    baselines = _checked(
        baseline_bytes, expected_baseline_sha256, FittedOOSBaselineBundleV1
    )
    outcomes = _checked(
        outcome_bytes, expected_outcome_sha256, FittedOOSOutcomeBundleV1
    )
    if (
        baselines.campaign_sha256 != expected_campaign_sha256
        or baselines.preregistration_sha256 != binding.preregistration_sha256
        or baselines.baseline_specs != plan.baseline_specs
        or outcomes.campaign_sha256 != expected_campaign_sha256
        or outcomes.forecast_sha256 != expected_forecast_sha256
    ):
        raise FittedOOSMetricsError("fitted_oos_source_bundle_pin_mismatch")
    events = _join_slots(campaign, original, inventory, baselines, outcomes, seal)
    constant_values = tuple(
        slot.constant_prevalence
        for event in events
        for slot in event.slots
        if slot.constant_prevalence is not None
    )
    if any(
        slot.no_skill != Decimal("0.5")
        for event in events
        for slot in event.slots
        if slot.no_skill is not None
    ) or any(value != training_prevalence for value in constant_values):
        raise FittedOOSMetricsError("fitted_oos_baseline_policy_changed")
    missing_count = sum(bool(slot.missing) for event in events for slot in event.slots)
    complete = missing_count == 0
    windows = []
    for child in campaign.children:
        subset = tuple(
            event for event in events if event.child_ordinal == child.ordinal
        )
        if not subset:
            status = "warmup_no_events"
        else:
            status = "complete_descriptive" if complete else "incomplete"
        candidate = None
        baseline_scores = ()
        deltas = ()
        paired = ()
        if complete and subset:
            candidate, baseline_scores, deltas = _scores(subset, plan)
            paired = _paired_blocks(subset, plan, child.ordinal)
        windows.append(
            FittedOOSWindowScoreV1(
                child_ordinal=child.ordinal,
                start_at=child.start_at,
                end_at=child.end_at,
                event_count=len(subset),
                status=status,
                candidate=candidate,
                baselines=baseline_scores,
                deltas=deltas,
                paired_blocks=paired,
                uncertainty_complete=(
                    bool(subset)
                    and complete
                    and len(paired) == len(plan.baseline_specs)
                ),
            )
        )
    overall_candidate = None
    overall_baselines = ()
    overall_deltas = ()
    symbol_scores = ()
    screen = False
    if complete:
        overall_candidate, overall_baselines, overall_deltas = _scores(events, plan)
        symbol_scores = tuple(
            FittedOOSSymbolScoreV1(
                instrument_id=instrument,
                candidate=subject,
                baselines=baseline_scores,
                deltas=deltas,
            )
            for instrument in INSTRUMENTS
            for subject, baseline_scores, deltas in (
                _scores(events, plan, instrument=instrument),
            )
        )
        scored_windows = tuple(window for window in windows if window.event_count)
        screen = (
            len(events) >= plan.minimum_candidate_events
            and len(scored_windows) >= plan.minimum_scored_windows
            and overall_candidate.expected_calibration_error
            <= plan.maximum_candidate_ece
            and all(
                item.brier_advantage >= plan.minimum_brier_advantage
                for item in overall_deltas
            )
            and all(
                window.candidate is not None
                and window.candidate.expected_calibration_error
                <= plan.maximum_candidate_ece
                and all(
                    item.brier_advantage >= plan.minimum_brier_advantage
                    for item in window.deltas
                )
                for window in scored_windows
            )
            and all(
                symbol.candidate.expected_calibration_error
                <= plan.maximum_candidate_ece
                and all(
                    item.brier_advantage >= plan.minimum_brier_advantage
                    for item in symbol.deltas
                )
                for symbol in symbol_scores
            )
        )
    result = _freeze(
        FittedOOSMetricsV1(
            score_plan_sha256=expected_score_plan_sha256,
            campaign_sha256=expected_campaign_sha256,
            child_chain_sha256=campaign.child_chain_sha256,
            binding_sha256=expected_binding_sha256,
            forecast_sha256=expected_forecast_sha256,
            feature_inventory_sha256=expected_inventory_sha256,
            baseline_bundle_sha256=expected_baseline_sha256,
            outcome_bundle_sha256=expected_outcome_sha256,
            events=events,
            windows=tuple(windows),
            missing_slot_count=missing_count,
            coverage_status="complete" if complete else "incomplete",
            overall_candidate=overall_candidate,
            overall_baselines=overall_baselines,
            overall_deltas=overall_deltas,
            symbol_scores=symbol_scores,
            descriptive_threshold_screen_passed=screen,
        ),
        contract_type=FittedOOSMetricsV1,
    )
    if len(result.payload) > _MAX_OUTPUT_BYTES:
        raise ArtifactVerificationError("fitted_oos_output_size_invalid")
    return result
