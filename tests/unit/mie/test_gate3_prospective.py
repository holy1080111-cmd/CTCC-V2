from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.mie.validation import (
    BarConstruction,
    BaselineKind,
    BaselineSpec,
    CandidateSpec,
    CostModel,
    DatasetIdentity,
    DatasetPartition,
    DevelopmentValidationSplit,
    EvaluationPlan,
    FeatureSpec,
    FrozenParameter,
    FrozenTrial,
    Gate3Claim,
    Gate3Metric,
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
    MultipleTestingCorrection,
    OutcomeKind,
    OutcomeLabelSpec,
    PartitionWindow,
    ProspectiveAccessOutcome,
    ProspectiveHoldoutSpec,
    ProspectiveHoldoutState,
    TrialRegistry,
    UncertaintyPlan,
    freeze_prospective_holdout_receipt,
    freeze_prospective_preregistration,
    verify_prospective_holdout_receipt,
    verify_prospective_preregistration,
)
from app.mie.validation.artifact import ArtifactVerificationError

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 2, tzinfo=UTC)
HOLDOUT_START = datetime(2026, 10, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 10, 31, tzinfo=UTC)
FIRST_ACCESS = datetime(2026, 11, 1, tzinfo=UTC)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_prospective_preregistration() -> Gate3ProspectivePreregistration:
    baselines = tuple(
        BaselineSpec(
            baseline_id=f"baseline:{kind.value}",
            kind=kind,
            version="v1",
            configuration_sha256=sha(kind.value),
            frozen_at=CREATED_AT - timedelta(days=1),
        )
        for kind in sorted(BaselineKind, key=lambda item: item.value)
    )
    return Gate3ProspectivePreregistration(
        preregistration_id="gate3:prospective:fixture:v1",
        created_at=CREATED_AT,
        source_tree_sha256=sha("source-tree"),
        training_dataset=DatasetIdentity(
            dataset_id="dataset:training:fixture:v1",
            source="binance.public.data",
            source_version="futures_um.daily.klines.v1",
            instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
            manifest_sha256=sha("training-manifest"),
            content_sha256=sha("training-content"),
            expected_rows=200_000,
            first_event_at=START + timedelta(minutes=1),
            last_event_at=datetime(2026, 8, 21, tzinfo=UTC),
            frozen_at=CREATED_AT - timedelta(days=1),
            event_time_field="closed_at",
            available_time_field="available_at",
        ),
        bar_construction=BarConstruction(interval_seconds=60),
        outcome_label=OutcomeLabelSpec(
            label_id="forward:direction:1m",
            kind=OutcomeKind.FORWARD_RETURN_DIRECTION,
            horizon_seconds=60,
            dependency_seconds=60,
            positive_threshold=D("0"),
        ),
        candidate=CandidateSpec(
            candidate_id="candidate:gate2:calibrated:v1",
            selected_trial_id="trial:001",
            model_version="v1",
            configuration_sha256=sha("trial-001"),
            source_sha256=sha("candidate-source"),
        ),
        features=(
            FeatureSpec(
                feature_id="mie:gate2:all",
                feature_version="v1",
                dependency_seconds=3_600,
                parameters=(
                    FrozenParameter(name="alpha", value=D("0.25")),
                    FrozenParameter(name="history_bars", value=256),
                ),
            ),
        ),
        selection_split=DevelopmentValidationSplit(
            development=PartitionWindow(
                partition=DatasetPartition.DEVELOPMENT,
                start_at=datetime(2026, 1, 2, tzinfo=UTC),
                end_at=datetime(2026, 4, 1, tzinfo=UTC),
            ),
            validation=PartitionWindow(
                partition=DatasetPartition.VALIDATION,
                start_at=datetime(2026, 4, 2, tzinfo=UTC),
                end_at=datetime(2026, 8, 20, tzinfo=UTC),
            ),
            purge_seconds=3_600,
            embargo_seconds=3_600,
            max_feature_dependency_seconds=3_600,
            label_dependency_seconds=60,
        ),
        walk_forward_plan_sha256=sha("walk-forward-plan"),
        baselines=baselines,
        cost_model=CostModel(
            model_id="cost:model:v1",
            version="v1",
            fee_bps=D("1"),
            funding_bps=D("0.5"),
            spread_bps=D("0.75"),
            slippage_bps=D("1.25"),
            funding_interval_seconds=28_800,
        ),
        evaluation=EvaluationPlan(
            metrics=tuple(sorted(Gate3Metric, key=lambda item: item.value)),
            reliability_bin_count=10,
            cvar_confidence_level=D("0.95"),
            uncertainty=UncertaintyPlan(
                confidence_level=D("0.95"),
                resamples=1_000,
                block_length=60,
                seed=20260902,
                familywise_alpha=D("0.05"),
                multiple_testing_correction=(MultipleTestingCorrection.HOLM_BONFERRONI),
            ),
            trials=TrialRegistry(
                registry_id="trials:prospective:fixture:v1",
                trials=(
                    FrozenTrial(
                        trial_id="trial:001",
                        configuration_sha256=sha("trial-001"),
                    ),
                    FrozenTrial(
                        trial_id="trial:002",
                        configuration_sha256=sha("trial-002"),
                    ),
                ),
                declared_trial_count=2,
                selection_metric=Gate3Metric.BRIER_SCORE,
            ),
        ),
        prospective_holdout=ProspectiveHoldoutSpec(
            holdout_id="binance:btc_eth:1m:2026-10",
            source="binance.public.data",
            source_version="futures_um.daily.klines.v1",
            instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
            coordinate_plan_sha256=sha("prospective-coordinates"),
            bar_interval_seconds=60,
            artifact_interval_seconds=86_400,
            start_at=HOLDOUT_START,
            end_at=HOLDOUT_END,
            publication_lag_seconds=86_400,
            first_permitted_access_at=FIRST_ACCESS,
            expected_artifact_count=60,
            expected_rows=86_400,
        ),
    )


def valid_receipt() -> Gate3ProspectiveHoldoutReceipt:
    preregistration = valid_prospective_preregistration()
    return Gate3ProspectiveHoldoutReceipt(
        receipt_id="gate3:prospective:receipt:fixture:v1",
        recorded_at=FIRST_ACCESS + timedelta(hours=2),
        preregistration=preregistration,
        preregistration_sha256=preregistration.canonical_sha256(),
        first_accessed_at=FIRST_ACCESS,
        holdout_dataset=DatasetIdentity(
            dataset_id="dataset:prospective:fixture:v1",
            source="binance.public.data",
            source_version="futures_um.daily.klines.v1",
            instrument_ids=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
            manifest_sha256=sha("holdout-manifest"),
            content_sha256=sha("holdout-content"),
            expected_rows=86_400,
            first_event_at=HOLDOUT_START + timedelta(minutes=1),
            last_event_at=HOLDOUT_END,
            frozen_at=FIRST_ACCESS + timedelta(hours=1),
            event_time_field="closed_at",
            available_time_field="available_at",
        ),
        acquisition_plan_sha256=sha("prospective-coordinates"),
        artifact_count=60,
        all_artifacts_verified=True,
        access_policy_compliant=True,
        access_outcome=ProspectiveAccessOutcome.SEALED_UNREAD,
        candidate_changed_after_preregistration=False,
        predictive_oos_eligible=True,
    )


def test_prospective_seal_is_canonical_immutable_and_zero_authority() -> None:
    preregistration = valid_prospective_preregistration()
    frozen = freeze_prospective_preregistration(preregistration)

    assert preregistration.holdout_state == (
        ProspectiveHoldoutState.SCHEDULED_UNOBSERVED
    )
    assert preregistration.current_claim == Gate3Claim.COMPUTATIONAL
    assert preregistration.runtime_consumers == 0
    assert preregistration.execution_authority is False
    assert frozen.payload == preregistration.canonical_json_bytes()
    assert (
        verify_prospective_preregistration(
            frozen.payload,
            expected_sha256=frozen.sha256,
        )
        == preregistration
    )

    with pytest.raises(ValidationError, match="frozen"):
        preregistration.runtime_consumers = 1  # type: ignore[misc]

    tampered = preregistration.model_copy(update={"execution_authority": True})
    with pytest.raises(ArtifactVerificationError, match="revalidation failed"):
        freeze_prospective_preregistration(tampered)


def test_prospective_seal_must_predate_holdout_and_respect_embargo() -> None:
    payload = valid_prospective_preregistration().model_dump()
    payload["created_at"] = HOLDOUT_START
    with pytest.raises(ValidationError, match="predate the first holdout"):
        Gate3ProspectivePreregistration.model_validate(payload)

    payload = valid_prospective_preregistration().model_dump()
    near_holdout = HOLDOUT_START - timedelta(seconds=3_599)
    payload["training_dataset"]["last_event_at"] = near_holdout
    payload["training_dataset"]["frozen_at"] = near_holdout
    payload["created_at"] = near_holdout + timedelta(seconds=1)
    with pytest.raises(ValidationError, match="holdout embargo"):
        Gate3ProspectivePreregistration.model_validate(payload)


def test_prospective_coordinates_fail_closed_on_counts_and_access_time() -> None:
    holdout = valid_prospective_preregistration().prospective_holdout
    payload = holdout.model_dump()
    payload["expected_rows"] -= 1
    with pytest.raises(ValidationError, match="row count"):
        ProspectiveHoldoutSpec.model_validate(payload)

    payload = holdout.model_dump()
    payload["expected_artifact_count"] -= 1
    with pytest.raises(ValidationError, match="artifact count"):
        ProspectiveHoldoutSpec.model_validate(payload)

    payload = holdout.model_dump()
    payload["first_permitted_access_at"] -= timedelta(seconds=1)
    with pytest.raises(ValidationError, match="publication lag"):
        ProspectiveHoldoutSpec.model_validate(payload)


def test_prospective_receipt_binds_the_seal_and_qualifying_dataset() -> None:
    receipt = valid_receipt()
    frozen = freeze_prospective_holdout_receipt(receipt)

    assert receipt.predictive_oos_eligible is True
    assert receipt.current_claim == Gate3Claim.COMPUTATIONAL
    assert receipt.strategy_evaluated is False
    assert receipt.runtime_consumers == 0
    assert receipt.execution_authority is False
    assert receipt.real_order_tested is False
    assert (
        verify_prospective_holdout_receipt(
            frozen.payload,
            expected_sha256=frozen.sha256,
        )
        == receipt
    )

    payload = receipt.model_dump()
    payload["preregistration_sha256"] = sha("wrong")
    with pytest.raises(ValidationError, match="preregistration hash"):
        Gate3ProspectiveHoldoutReceipt.model_validate(payload)

    payload = receipt.model_dump()
    payload["acquisition_plan_sha256"] = sha("wrong")
    with pytest.raises(ValidationError, match="acquisition plan"):
        Gate3ProspectiveHoldoutReceipt.model_validate(payload)

    payload = receipt.model_dump()
    payload["holdout_dataset"]["expected_rows"] -= 1
    with pytest.raises(ValidationError, match="row count"):
        Gate3ProspectiveHoldoutReceipt.model_validate(payload)


def test_early_or_exposed_access_is_recorded_but_never_eligible() -> None:
    receipt = valid_receipt()
    payload = receipt.model_dump()
    payload["first_accessed_at"] = FIRST_ACCESS - timedelta(hours=1)
    payload["access_policy_compliant"] = False
    payload["predictive_oos_eligible"] = False
    early = Gate3ProspectiveHoldoutReceipt.model_validate(payload)
    assert early.predictive_oos_eligible is False

    payload["access_policy_compliant"] = True
    with pytest.raises(ValidationError, match="access-policy result"):
        Gate3ProspectiveHoldoutReceipt.model_validate(payload)

    payload = receipt.model_dump()
    payload["access_outcome"] = ProspectiveAccessOutcome.DESCRIPTIVE_SUMMARY_EXPOSED
    with pytest.raises(ValidationError, match="predictive eligibility"):
        Gate3ProspectiveHoldoutReceipt.model_validate(payload)

    payload["predictive_oos_eligible"] = False
    exposed = Gate3ProspectiveHoldoutReceipt.model_validate(payload)
    assert exposed.predictive_oos_eligible is False


def test_prospective_contracts_have_no_order_or_sizing_geometry() -> None:
    forbidden = {
        "account_id",
        "order_id",
        "quantity",
        "contracts",
        "leverage",
        "margin",
        "exchange_payload",
        "write_authority",
    }
    for contract_type in (
        ProspectiveHoldoutSpec,
        Gate3ProspectivePreregistration,
        Gate3ProspectiveHoldoutReceipt,
    ):
        assert forbidden.isdisjoint(contract_type.model_fields)
