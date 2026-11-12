"""Synthetic locked-forecast and measured-source label replay controls."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.mie.validation import fitted_holdout_outcome_replay_v1 as subject
from app.mie.validation.artifact import ArtifactVerificationError, _freeze, _verify
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
)
from app.mie.validation.fitted_holdout_feature_inventory_v1 import (
    FittedHoldoutChildReadbackInputV1,
    FittedHoldoutFeatureChildLineageV1,
    FittedHoldoutFeatureInventoryBuildV1,
    FittedHoldoutFeatureLineageV1,
    _FittedHoldoutSourceReplayV1,
)
from app.mie.validation.fitted_holdout_inference_v1 import (
    FittedHoldoutChildFeatureSourceV1,
    FittedHoldoutFeatureInventoryV1,
    infer_fitted_holdout_v1,
)
from app.mie.validation.fitted_holdout_metrics_v1 import FittedOOSOutcomeBundleV1
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
)
from app.mie.validation.prospective import Gate3ProspectivePreregistration
from app.mie.validation.prospective_campaign_v2 import (
    INSTRUMENTS,
    ROWS_PER_FOUR_HOURS,
    ProspectiveCampaignV2,
    _child_chain_sha256,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedCandidateBindingV1,
)
from tests.unit.mie.test_fitted_holdout_inference_v1 import (
    _synthetic_bars,
    inference_case,  # noqa: F401 - imported pytest fixture
    sha,
)


def _split_last_child(original, split_at):
    first, second, third = original.children
    first_rows = 5 * ROWS_PER_FOUR_HOURS
    left = third.model_copy(
        update={
            "coordinate_plan_sha256": sha("left coordinates"),
            "schedule_sha256": sha("left schedule"),
            "end_at": split_at,
            "first_permitted_access_at": split_at + timedelta(minutes=1),
            "expected_rows": first_rows,
        }
    )
    right = third.model_copy(
        update={
            "ordinal": 3,
            "first_row": third.first_row + first_rows,
            "expected_rows": third.expected_rows - first_rows,
            "holdout_id": "synthetic:labels:fourth",
            "seal_sha256": sha("right seal"),
            "coordinate_plan_sha256": sha("right coordinates"),
            "schedule_sha256": sha("right schedule"),
            "start_at": split_at,
        }
    )
    children = (first, second, left, right)
    return ProspectiveCampaignV2.model_validate(
        original.model_copy(
            update={
                "children": children,
                "child_chain_sha256": _child_chain_sha256(children),
            }
        ).model_dump(mode="python")
    )


def _child_lineage(child, binding_pin):
    def pin(name):
        return sha(f"synthetic label {child.ordinal} {name}")

    return FittedHoldoutFeatureChildLineageV1(
        ordinal=child.ordinal,
        seal_sha256=child.seal_sha256,
        schedule_sha256=child.schedule_sha256,
        coordinate_plan_sha256=child.coordinate_plan_sha256,
        binding_sha256=binding_pin if child.ordinal == 0 else pin("binding"),
        receipt_sha256=pin("receipt"),
        reservation_sha256=pin("reservation"),
        manifest_sha256=pin("manifest"),
        dataset_sha256=pin("dataset"),
        minute_tape_sha256=pin("tape"),
        original_minutes_sha256=pin("minutes"),
        aggregation_sha256=pin("aggregate"),
    )


def _child_stub(item, candidate_inputs, reserved_at):
    # The upstream rebuild is stubbed for these tests. Its raw, schedule and
    # reservation checks are exercised by the existing source-builder suite.
    reservation = FittedEvaluationReservationV1(
        seal_sha256=item.seal_sha256,
        binding_sha256=item.binding_sha256,
        acknowledgement_xid="11",
        receipt_sha256=item.receipt_sha256,
        dataset_sha256=item.dataset_sha256,
        attempt_sha256=sha(f"attempt {item.ordinal}"),
        database_oid=40,
        evaluator_oid=41,
        reservation_xid=str(12 + item.ordinal),
        reserved_at=reserved_at,
        database_read_at=reserved_at + timedelta(seconds=item.ordinal + 1),
    )
    return FittedHoldoutChildReadbackInputV1(
        schedule=None,
        candidate_inputs=candidate_inputs,
        readback=ControlledFittedHoldoutReadV1(reservation, b"", b""),
        receipt_bytes=b"",
        coordinate_plan_bytes=b"",
        expected_binding_sha256=item.binding_sha256,
        expected_receipt_sha256=item.receipt_sha256,
        expected_reservation_sha256=item.reservation_sha256,
    )


@pytest.fixture
def outcome_case(
    monkeypatch,
    inference_case,  # noqa: F811 - imported pytest fixture
):
    previous_inventory, frozen = inference_case
    original = ProspectiveCampaignV2.model_validate_json(frozen["campaign_bytes"])
    campaign = _split_last_child(original, previous_inventory.events[0].bar_closed_at)
    campaign_pin = campaign.canonical_sha256()
    plan = frozen["feature_plan"]
    binding = _verify(
        frozen["binding_bytes"],
        expected_sha256=frozen["expected_binding_sha256"],
        contract_type=DevvalFittedCandidateBindingV1,
    )
    binding_pin = binding.canonical_sha256()
    child_lineage = tuple(
        _child_lineage(child, binding_pin) for child in campaign.children
    )
    inventory = _freeze(
        previous_inventory.model_copy(
            update={
                "campaign_sha256": campaign_pin,
                "child_sources": tuple(
                    FittedHoldoutChildFeatureSourceV1(
                        seal_sha256=item.seal_sha256,
                        coordinate_plan_sha256=item.coordinate_plan_sha256,
                        minute_tape_sha256=item.minute_tape_sha256,
                    )
                    for item in child_lineage
                ),
            }
        ),
        contract_type=FittedHoldoutFeatureInventoryV1,
    )
    lineage = _freeze(
        FittedHoldoutFeatureLineageV1(
            campaign_sha256=campaign_pin,
            campaign_child_chain_sha256=campaign.child_chain_sha256,
            anchor_binding_sha256=binding_pin,
            feature_plan_sha256=plan.canonical_sha256,
            feature_inventory_sha256=inventory.sha256,
            fitted_model_sha256=binding.fitted_model_sha256,
            children=child_lineage,
        ),
        contract_type=FittedHoldoutFeatureLineageV1,
    )
    source = _FittedHoldoutSourceReplayV1(
        campaign=campaign,
        plan=plan,
        features=FittedHoldoutFeatureInventoryBuildV1(inventory, lineage),
        rows=(
            _synthetic_bars(INSTRUMENTS[0], 1, timedelta(seconds=2)),
            _synthetic_bars(INSTRUMENTS[1], -1, timedelta(seconds=3)),
        ),
    )
    forecast = infer_fitted_holdout_v1(
        **{
            **frozen,
            "inventory_bytes": inventory.payload,
            "expected_inventory_sha256": inventory.sha256,
            "campaign_bytes": campaign.canonical_json_bytes(),
            "expected_campaign_sha256": campaign_pin,
        }
    )
    candidate = FittedCandidateInputsV1(
        binding=_freeze(binding, contract_type=DevvalFittedCandidateBindingV1),
        expected_binding_sha256=binding_pin,
        batch=None,
        study=None,
        trial_result=None,
        legacy_scores=None,
        preregistration=Gate3ProspectivePreregistration.model_validate_json(
            frozen["seal_bytes"]
        ),
        expected_preregistration_sha256=binding.preregistration_sha256,
        source_archive_sha256=binding.source_archive_sha256,
    )
    children = tuple(
        _child_stub(item, candidate, campaign.first_permitted_access_at)
        for item in child_lineage
    )
    cutoff = subject.FittedOOSLabelReplayCutoffV1(
        campaign_sha256=campaign_pin,
        forecast_sha256=forecast.sha256,
        feature_inventory_sha256=inventory.sha256,
        feature_lineage_sha256=lineage.sha256,
        declared_as_of=campaign.first_permitted_access_at + timedelta(seconds=5),
    )
    monkeypatch.setattr(
        subject, "_rebuild_fitted_holdout_source_v1", lambda **_: source
    )
    return (
        source,
        forecast,
        cutoff,
        {
            "forecast_bytes": forecast.payload,
            "expected_forecast_sha256": forecast.sha256,
            "inventory_bytes": inventory.payload,
            "expected_inventory_sha256": inventory.sha256,
            "cutoff_bytes": cutoff.canonical_json_bytes(),
            "expected_cutoff_sha256": cutoff.canonical_sha256(),
            "campaign_bytes": campaign.canonical_json_bytes(),
            "expected_campaign_sha256": campaign_pin,
            "feature_plan": plan,
            "children": children,
            "model_bytes": frozen["model_bytes"],
        },
    )


def _with_cutoff(arguments, cutoff):
    return {
        **arguments,
        "cutoff_bytes": cutoff.canonical_json_bytes(),
        "expected_cutoff_sha256": cutoff.canonical_sha256(),
    }


def test_exact_labels_preserve_cross_child_source_rows_and_declared_timing(
    outcome_case,
):
    source, forecast, cutoff, arguments = outcome_case
    replay = subject.build_fitted_holdout_outcomes_v1(**arguments)
    assert replay == subject.build_fitted_holdout_outcomes_v1(**arguments)
    checked = _verify(
        replay.outcomes.payload,
        expected_sha256=replay.outcomes.sha256,
        contract_type=FittedOOSOutcomeBundleV1,
    )
    assert checked.forecast_sha256 == forecast.sha256
    assert len(checked.events) == len(forecast.contract.events) == 3
    evidence = replay.evidence.contract
    assert evidence.outcome_bundle_sha256 == replay.outcomes.sha256
    assert evidence.declared_as_of == cutoff.declared_as_of
    assert evidence.measured_read_time_verified is False
    assert evidence.evaluator_first_access_verified is False
    assert evidence.forecast_publication_verified is False
    assert evidence.predictive_oos_eligible is False
    assert evidence.execution_authority is False
    assert forecast.contract.events[0].slots[1].probability is None
    for symbol_index, observed in enumerate(checked.events[0].slots):
        row = evidence.events[0].slots[symbol_index]
        base, outcome = source.rows[symbol_index][20:22]
        label = observed.label
        assert label is not None
        assert label.positive is (symbol_index == 0)
        assert label.base_row_sha256 == base.source_row_sha256
        assert label.outcome_row_sha256 == outcome.source_row_sha256
        assert label.base_available_at == base.available_at
        assert label.outcome_available_at == outcome.available_at
        assert label.read_at == cutoff.declared_as_of
        assert row.base_row_json == base.model_dump_json()
        assert row.outcome_row_json == outcome.model_dump_json()
        assert row.latest_required_source_availability == outcome.available_at
        assert row.outcome_window_rows_sha256 == label.outcome_window_rows_sha256
        assert (row.base_child_ordinal, row.outcome_child_ordinal) == (2, 3)
        lineage = source.features.lineage.contract.children
        assert row.base_aggregation_sha256 == lineage[2].aggregation_sha256
        assert row.outcome_aggregation_sha256 == lineage[3].aggregation_sha256


@pytest.mark.parametrize("name", ["forecast_bytes", "inventory_bytes", "cutoff_bytes"])
def test_original_pins_checked_before_rebuilding_source(
    outcome_case, monkeypatch, name
):
    *_, arguments = outcome_case

    def refuse_source_rebuild(**_):
        pytest.fail("unverified prior bytes reached source replay")

    monkeypatch.setattr(
        subject, "_rebuild_fitted_holdout_source_v1", refuse_source_rebuild
    )
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        subject.build_fitted_holdout_outcomes_v1(
            **{**arguments, name: arguments[name] + b" "}
        )


def test_repinned_forecast_value_cannot_reach_labels(outcome_case, monkeypatch):
    _, forecast, cutoff, arguments = outcome_case

    def refuse_labels(*_args, **_kwargs):
        pytest.fail("changed forecast reached outcome labels")

    monkeypatch.setattr(subject, "forward_direction_label_v2", refuse_labels)
    first, *remaining = forecast.contract.events
    btc, eth = first.slots
    btc = btc.model_copy(update={"probability": Decimal("0.7")})
    first = first.model_copy(update={"slots": (btc, eth)})
    changed = forecast.contract.model_copy(update={"events": (first, *remaining)})
    cutoff = cutoff.model_copy(update={"forecast_sha256": changed.canonical_sha256()})
    with pytest.raises(
        subject.FittedHoldoutOutcomeReplayError, match="forecast_replay"
    ):
        subject.build_fitted_holdout_outcomes_v1(
            **_with_cutoff(
                {
                    **arguments,
                    "forecast_bytes": changed.canonical_json_bytes(),
                    "expected_forecast_sha256": changed.canonical_sha256(),
                },
                cutoff,
            )
        )


@pytest.mark.parametrize("changed_input", ["inventory", "feature_lineage"])
def test_new_pins_cannot_change_verified_feature_source(outcome_case, changed_input):
    source, forecast, cutoff, arguments = outcome_case
    if changed_input == "feature_lineage":
        cutoff = cutoff.model_copy(
            update={"feature_lineage_sha256": sha("different feature lineage")}
        )
    else:
        inventory = source.features.inventory.contract
        first, *remaining = inventory.events
        btc, eth = first.slots
        btc = btc.model_copy(
            update={
                "current_bar_available_at": btc.current_bar_available_at
                + timedelta(microseconds=1)
            }
        )
        first = first.model_copy(update={"slots": (btc, eth)})
        changed = inventory.model_copy(update={"events": (first, *remaining)})
        changed_forecast = forecast.contract.model_copy(
            update={"feature_inventory_sha256": changed.canonical_sha256()}
        )
        arguments = {
            **arguments,
            "inventory_bytes": changed.canonical_json_bytes(),
            "expected_inventory_sha256": changed.canonical_sha256(),
            "forecast_bytes": changed_forecast.canonical_json_bytes(),
            "expected_forecast_sha256": changed_forecast.canonical_sha256(),
        }
        cutoff = cutoff.model_copy(
            update={
                "feature_inventory_sha256": changed.canonical_sha256(),
                "forecast_sha256": changed_forecast.canonical_sha256(),
            }
        )
    with pytest.raises(subject.FittedHoldoutOutcomeReplayError, match="feature_source"):
        subject.build_fitted_holdout_outcomes_v1(**_with_cutoff(arguments, cutoff))


def test_declared_time_must_include_the_last_recorded_child_readback(outcome_case):
    source, _, cutoff, arguments = outcome_case
    declared = cutoff.model_copy(
        update={"declared_as_of": source.campaign.first_permitted_access_at}
    )
    with pytest.raises(
        subject.FittedHoldoutOutcomeReplayError, match="cutoff_too_early"
    ):
        subject.build_fitted_holdout_outcomes_v1(**_with_cutoff(arguments, declared))


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ("missing_target", "grid_incomplete"),
        ("late_target", "source_slot_changed"),
        ("changed_feature_prefix", "feature replay differs"),
    ],
)
def test_unchanged_forecast_cannot_label_incomplete_or_changed_rows(
    outcome_case, monkeypatch, change, refusal
):
    source, _, cutoff, arguments = outcome_case
    btc, eth = source.rows
    if change == "missing_target":
        btc = btc[:-1]
    elif change == "late_target":
        target = btc[-1].model_copy(
            update={"available_at": cutoff.declared_as_of + timedelta(seconds=1)}
        )
        btc = (*btc[:-1], target)
    else:
        first = btc[0].model_copy(update={"source_row_sha256": sha("changed prefix")})
        btc = (first, *btc[1:])
    changed = replace(source, rows=(btc, eth))
    monkeypatch.setattr(
        subject, "_rebuild_fitted_holdout_source_v1", lambda **_: changed
    )
    with pytest.raises(ValueError, match=refusal):
        subject.build_fitted_holdout_outcomes_v1(**arguments)
