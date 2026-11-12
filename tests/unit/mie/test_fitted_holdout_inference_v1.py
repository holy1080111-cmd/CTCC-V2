"""Synthetic geometry and features only. No data read, fit, or outcomes."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.mie.features import FeatureBar
from app.mie.validation.artifact import ArtifactVerificationError
from app.mie.validation.fitted_holdout_inference_v1 import (
    FittedHoldoutChildFeatureSourceV1,
    FittedHoldoutFeatureEventV1,
    FittedHoldoutFeatureInventoryV1,
    FittedHoldoutFeatureSlotV1,
    FittedHoldoutInferenceError,
    infer_fitted_holdout_v1,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    BAR_SECONDS,
    INSTRUMENTS,
    MAX_CHILD_ROWS,
    CampaignChildV2,
    ProspectiveCampaignV2,
    _child_chain_sha256,
    _protocol_sha256,
)
from app.mie.validation.prospective_devval_candidate_trials_v1 import (
    DevvalCandidateSourceV1,
    DevvalCandidateTrialV1,
    _FittedModel,
    _ModelBin,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedCandidateBindingV1,
)
from app.mie.validation.replay import (
    PointInTimeBar,
    PointInTimeReplaySnapshot,
    replay_features_at,
)
from tests.unit.mie.test_gate3_prospective import sha, valid_prospective_preregistration
from tests.unit.mie.test_gate3_prospective_campaign_v2 import (
    DECLARED,
    FEATURE_SECONDS,
    FIRST_START,
    feature_plan,
)

D = Decimal
FOUR_HOURS = timedelta(seconds=BAR_SECONDS)


def _synthetic_seal(source, model, plan):
    basis = valid_prospective_preregistration().model_dump(mode="python")
    basis["source_tree_sha256"] = source.canonical_sha256()
    source_identity = {
        "source": "okx.public_market_source",
        "source_version": "okx.history_candles.nine_strings.v1",
    }
    basis["training_dataset"].update(source_identity)
    basis["candidate"].update(
        source_sha256=source.canonical_sha256(),
        model_version=model.model_version,
        configuration_sha256=model.trial.canonical_sha256(),
    )
    basis["features"][0].update(
        feature_id=model.trial.feature,
        feature_version=plan.feature_version,
        dependency_seconds=FEATURE_SECONDS,
        parameters=(
            {"name": "feature_plan_sha256", "value": plan.canonical_sha256},
            {"name": "history_bars", "value": plan.history_bars},
        ),
    )
    basis["selection_split"]["validation"]["start_at"] += timedelta(days=4)
    basis["selection_split"].update(
        purge_seconds=FEATURE_SECONDS,
        embargo_seconds=FEATURE_SECONDS,
        max_feature_dependency_seconds=FEATURE_SECONDS,
        label_dependency_seconds=BAR_SECONDS,
    )
    basis["outcome_label"].update(
        horizon_seconds=BAR_SECONDS, dependency_seconds=BAR_SECONDS
    )
    basis["evaluation"]["trials"]["trials"][0]["configuration_sha256"] = (
        model.trial.canonical_sha256()
    )
    end = FIRST_START + FOUR_HOURS * 8
    basis["prospective_holdout"].update(
        **source_identity,
        holdout_id="synthetic:inference:child:0",
        coordinate_plan_sha256=sha("coordinates 0"),
        artifact_interval_seconds=60,
        start_at=FIRST_START,
        end_at=end,
        publication_lag_seconds=60,
        first_permitted_access_at=end + timedelta(minutes=1),
        expected_artifact_count=MAX_CHILD_ROWS,
        expected_rows=MAX_CHILD_ROWS,
    )
    return Gate3ProspectivePreregistration.model_validate(basis)


def _synthetic_campaign(seal, plan):
    protocol = _protocol_sha256(seal)
    children = []
    for index in range(3):
        start = FIRST_START + index * 8 * FOUR_HOURS
        end = start + 8 * FOUR_HOURS
        children.append(
            CampaignChildV2(
                ordinal=index,
                first_row=index * MAX_CHILD_ROWS,
                expected_rows=MAX_CHILD_ROWS,
                holdout_id=f"synthetic:inference:child:{index}",
                seal_sha256=(
                    seal.canonical_sha256() if index == 0 else sha(f"seal {index}")
                ),
                coordinate_plan_sha256=sha(f"coordinates {index}"),
                schedule_sha256=sha(f"schedule {index}"),
                protocol_sha256=protocol,
                start_at=start,
                end_at=end,
                first_permitted_access_at=end + timedelta(minutes=1),
                seal_declared_at=seal.created_at,
                schedule_declared_at=DECLARED,
            )
        )
    return ProspectiveCampaignV2(
        campaign_id="synthetic:inference:campaign",
        source_tree_sha256=seal.source_tree_sha256,
        protocol_sha256=protocol,
        feature_plan_sha256=plan.canonical_sha256,
        history_bars=plan.history_bars,
        outcome_horizon_seconds=BAR_SECONDS,
        minimum_candidate_bars=3,
        start_at=children[0].start_at,
        end_at=children[-1].end_at,
        first_permitted_access_at=children[-1].first_permitted_access_at,
        declared_at=DECLARED,
        expected_rows=MAX_CHILD_ROWS * len(children),
        children=tuple(children),
        child_chain_sha256=_child_chain_sha256(tuple(children)),
    )


def _synthetic_binding(seal, source, model, plan):
    opaque_synthetic_fields = (
        "source_archive_sha256",
        "campaign_sha256",
        "campaign_plan_sha256",
        "campaign_completion_sha256",
        "campaign_root_binding_sha256",
        "row_inventory_sha256",
        "batch_sha256",
        "study_sha256",
        "legacy_score_bundle_sha256",
        "legacy_score_source_sha256",
        "trial_result_sha256",
        "trial_plan_sha256",
    )
    return DevvalFittedCandidateBindingV1(
        source_checkpoint=source,
        preregistration_sha256=seal.canonical_sha256(),
        holdout_coordinate_sha256=seal.prospective_holdout.coordinate_plan_sha256,
        training_dataset_sha256=seal.training_dataset.canonical_sha256(),
        study_plan_sha256=seal.walk_forward_plan_sha256,
        feature_plan_sha256=plan.canonical_sha256,
        trial_registry_sha256=seal.evaluation.trials.canonical_sha256(),
        selected_trial_configuration_sha256=model.trial.canonical_sha256(),
        fitted_model_sha256=model.canonical_sha256(),
        **{field: sha(field) for field in opaque_synthetic_fields},
    )


def _synthetic_bars(instrument, direction, latency):
    rows = []
    for index in range(1, 25):
        closed_at = FIRST_START + index * FOUR_HOURS
        price = D(100 + direction * index)
        pin = sha(f"{instrument}:{index}")
        rows.append(
            PointInTimeBar(
                instrument_id=instrument,
                source_row_id="synthetic:" + pin,
                source_row_sha256=pin,
                available_at=closed_at + latency,
                bar=FeatureBar(
                    closed_at=closed_at,
                    open=price,
                    high=price + D(1),
                    low=price - D(1),
                    close=price,
                    volume=D(5),
                ),
            )
        )
    return tuple(rows)


def _synthetic_inventory(campaign, binding, plan):
    btc_latency = timedelta(seconds=2)
    eth_latency = timedelta(seconds=3)
    btc = _synthetic_bars(INSTRUMENTS[0], 1, btc_latency)
    eth = _synthetic_bars(INSTRUMENTS[1], -1, eth_latency)
    events = []
    for index in range(21, 24):
        close = FIRST_START + index * FOUR_HOURS
        decision = close + eth_latency
        slots = []
        for rows, latency in ((btc, btc_latency), (eth, eth_latency)):
            replay = replay_features_at(
                rows[:index],
                as_of=decision,
                bar_horizon=plan.bar_horizon,
                history_bars=plan.history_bars,
            )
            slots.append(
                FittedHoldoutFeatureSlotV1(
                    current_bar_available_at=close + latency,
                    replay_sha256=replay.replay_sha256,
                    replay_json=replay.model_dump_json(),
                )
            )
        events.append(
            FittedHoldoutFeatureEventV1(
                bar_closed_at=close,
                slots=(slots[0], slots[1]),
            )
        )
    return FittedHoldoutFeatureInventoryV1(
        campaign_sha256=campaign.canonical_sha256(),
        binding_sha256=binding.canonical_sha256(),
        feature_plan_sha256=plan.canonical_sha256,
        child_sources=tuple(
            FittedHoldoutChildFeatureSourceV1(
                seal_sha256=child.seal_sha256,
                coordinate_plan_sha256=child.coordinate_plan_sha256,
                minute_tape_sha256=sha(f"synthetic tape {child.ordinal}"),
            )
            for child in campaign.children
        ),
        events=tuple(events),
    )


@pytest.fixture(scope="module")
def inference_case():
    source = DevvalCandidateSourceV1(
        commit="a" * 40, tree="b" * 40, manifest_sha256=sha("manifest")
    )
    plan = feature_plan()
    model = _FittedModel(
        trial=DevvalCandidateTrialV1(
            trial_id="trial:001",
            feature="normalized_momentum",
            bin_edges=(D(0),),
            prior_weight=D(2),
            minimum_bin_observations=2,
        ),
        bins=(
            _ModelBin(observations=0, positives=0, probability=None),
            _ModelBin(observations=8, positives=7, probability=D("0.8")),
        ),
        development_rows_sha256=sha("development"),
        cost_model=valid_prospective_preregistration().cost_model,
    )
    seal = _synthetic_seal(source, model, plan)
    campaign = _synthetic_campaign(seal, plan)
    binding = _synthetic_binding(seal, source, model, plan)
    inventory = _synthetic_inventory(campaign, binding, plan)
    return inventory, {
        "inventory_bytes": inventory.canonical_json_bytes(),
        "expected_inventory_sha256": inventory.canonical_sha256(),
        "campaign_bytes": campaign.canonical_json_bytes(),
        "expected_campaign_sha256": campaign.canonical_sha256(),
        "binding_bytes": binding.canonical_json_bytes(),
        "expected_binding_sha256": binding.canonical_sha256(),
        "seal_bytes": seal.canonical_json_bytes(),
        "model_bytes": model.canonical_json_bytes(),
        "feature_plan": plan,
    }


def _run_inventory(inputs, inventory):
    return infer_fitted_holdout_v1(
        **{
            **inputs,
            "inventory_bytes": inventory.canonical_json_bytes(),
            "expected_inventory_sha256": inventory.canonical_sha256(),
        }
    )


def test_frozen_inference_keeps_complete_ordered_slots_and_null_probability(
    inference_case,
):
    inventory, inputs = inference_case
    result = infer_fitted_holdout_v1(**inputs)
    assert result == infer_fitted_holdout_v1(**inputs)
    assert len(result.contract.events) == 3
    for result_event, source_event in zip(
        result.contract.events, inventory.events, strict=True
    ):
        assert result_event.bar_closed_at == source_event.bar_closed_at
        assert (
            result_event.decision_at == source_event.slots[1].current_bar_available_at
        )
        assert tuple(slot.instrument_id for slot in result_event.slots) == INSTRUMENTS
        assert tuple(slot.bin_index for slot in result_event.slots) == (1, 0)
        assert tuple(slot.probability for slot in result_event.slots) == (
            D("0.8"),
            None,
        )
        assert tuple(slot.replay_sha256 for slot in result_event.slots) == tuple(
            slot.replay_sha256 for slot in source_event.slots
        )
    assert result.contract.independent_source_custody_verified is False
    assert result.contract.evaluator_first_access_verified is False
    assert result.contract.predictive_oos_eligible is False
    assert result.contract.execution_authority is False


@pytest.mark.parametrize(
    "payload", ["inventory", "model", "binding", "seal", "campaign"]
)
def test_changed_bytes_fail_the_separately_retained_pin(inference_case, payload):
    _, inputs = inference_case
    key = f"{payload}_bytes"
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        infer_fitted_holdout_v1(**{**inputs, key: inputs[key] + b" "})


def test_exact_bin_edge_preserves_native_snapshot_digest(inference_case):
    inventory, inputs = inference_case
    first, *remaining = inventory.events
    btc, eth = first.slots
    replay = PointInTimeReplaySnapshot.model_validate_json(btc.replay_json)
    snapshot = replay.feature_snapshot
    at_edge = snapshot.momentum.model_copy(update={"normalized_momentum": D("0.000")})
    replay = replay.model_copy(
        update={"feature_snapshot": snapshot.model_copy(update={"momentum": at_edge})}
    )
    btc = btc.model_copy(
        update={
            "replay_sha256": replay.replay_sha256,
            "replay_json": replay.model_dump_json(),
        }
    )
    first = first.model_copy(update={"slots": (btc, eth)})
    changed = inventory.model_copy(update={"events": (first, *remaining)})
    prediction = _run_inventory(inputs, changed).contract.events[0].slots[0]
    assert prediction.bin_index == 1
    assert prediction.probability == D("0.8")
    assert prediction.replay_sha256 == replay.replay_sha256


@pytest.mark.parametrize(
    ("mutation", "refusal"),
    [
        ("omitted_event", "event_inventory"),
        ("reordered_events", "event_time"),
        ("swapped_symbols", "feature_slot"),
        ("late_constituent", "event_time"),
        ("wrong_decision", "feature_slot"),
        ("wrong_replay_hash", "feature_slot"),
        ("short_history", "feature_slot"),
        ("reused_history", "feature_slot"),
        ("wrong_child", "child_inventory"),
    ],
)
def test_repinning_invalid_grid_does_not_make_it_valid(
    inference_case, mutation, refusal
):
    inventory, inputs = inference_case
    first, second, third = inventory.events
    btc, eth = first.slots
    if mutation == "omitted_event":
        changed = inventory.model_copy(update={"events": (first, third)})
    elif mutation == "reordered_events":
        changed = inventory.model_copy(update={"events": (second, first, third)})
    elif mutation == "wrong_child":
        changed = inventory.model_copy(
            update={"child_sources": inventory.child_sources[::-1]}
        )
    else:
        if mutation == "swapped_symbols":
            first = first.model_copy(update={"slots": (eth, btc)})
        elif mutation in {"late_constituent", "wrong_decision"}:
            latency = (
                FOUR_HOURS if mutation == "late_constituent" else timedelta(seconds=1)
            )
            eth = eth.model_copy(
                update={
                    "current_bar_available_at": eth.current_bar_available_at + latency
                }
            )
            first = first.model_copy(update={"slots": (btc, eth)})
        elif mutation == "wrong_replay_hash":
            first = first.model_copy(
                update={
                    "slots": (
                        btc,
                        eth.model_copy(update={"replay_sha256": sha("other")}),
                    )
                }
            )
        else:
            btc_replay = PointInTimeReplaySnapshot.model_validate_json(btc.replay_json)
            eth_replay = PointInTimeReplaySnapshot.model_validate_json(eth.replay_json)
            update = (
                {"source_row_count": 20}
                if mutation == "short_history"
                else {"source_rows_sha256": btc_replay.source_rows_sha256}
            )
            changed_replay = eth_replay.model_copy(update=update)
            eth = eth.model_copy(
                update={
                    "replay_json": changed_replay.model_dump_json(),
                    "replay_sha256": changed_replay.replay_sha256,
                }
            )
            first = first.model_copy(update={"slots": (btc, eth)})
        changed = inventory.model_copy(update={"events": (first, second, third)})
    with pytest.raises(FittedHoldoutInferenceError, match=refusal):
        _run_inventory(inputs, changed)


def test_repinning_a_changed_probability_cannot_change_the_fitted_bin(inference_case):
    inventory, inputs = inference_case
    model = _FittedModel.model_validate_json(inputs["model_bytes"])
    modified_bin = model.bins[1].model_copy(update={"probability": D("0.81")})
    modified = model.model_copy(update={"bins": (model.bins[0], modified_bin)})
    binding = DevvalFittedCandidateBindingV1.model_validate_json(
        inputs["binding_bytes"]
    )
    repinned = binding.model_copy(
        update={"fitted_model_sha256": modified.canonical_sha256()}
    )
    bound_inventory = inventory.model_copy(
        update={"binding_sha256": repinned.canonical_sha256()}
    )
    with pytest.raises(FittedHoldoutInferenceError, match="feature_or_bin_mismatch"):
        _run_inventory(
            {
                **inputs,
                "model_bytes": modified.canonical_json_bytes(),
                "binding_bytes": repinned.canonical_json_bytes(),
                "expected_binding_sha256": repinned.canonical_sha256(),
            },
            bound_inventory,
        )


def test_inventory_for_another_binding_is_refused_even_when_repinned(inference_case):
    inventory, inputs = inference_case
    with pytest.raises(FittedHoldoutInferenceError, match="inventory_pin"):
        _run_inventory(
            inputs,
            inventory.model_copy(update={"binding_sha256": sha("other binding")}),
        )


def test_model_application_uses_the_frozen_feature_plan(inference_case):
    _, inputs = inference_case
    changed = inputs["feature_plan"].model_copy(update={"signal_alpha": D("0.5")})
    with pytest.raises(FittedHoldoutInferenceError, match="protocol_mismatch"):
        infer_fitted_holdout_v1(**{**inputs, "feature_plan": changed})
