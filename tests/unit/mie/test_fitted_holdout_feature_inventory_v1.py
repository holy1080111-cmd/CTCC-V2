"""Synthetic raw-readback and campaign chronology regressions."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.artifact import FrozenGate3Artifact
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    fitted_holdout_reservation_sha256,
)
from app.mie.validation.fitted_holdout_aggregation_v1 import (
    aggregate_fitted_holdout_readback_v1,
)
from app.mie.validation.fitted_holdout_feature_inventory_v1 import (
    FittedHoldoutChildReadbackInputV1,
    FittedHoldoutFeatureInventoryError,
    _causal_feature_events,
    _checked_child_binding,
    _require_scheduled_manifest,
)
from app.mie.validation.fitted_holdout_inference_v1 import infer_fitted_holdout_v1
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
)
from app.mie.validation.prospective_campaign_v2 import (
    INSTRUMENTS,
    ROWS_PER_FOUR_HOURS,
    CampaignChildV2,
    ProspectiveCampaignV2,
    _protocol_sha256,
)
from app.mie.validation.prospective_capture_schedule import (
    ProspectiveCoordinatePlanV1,
    build_capture_schedule,
)
from app.mie.validation.prospective_devval_fitted_binding_v1 import (
    DevvalFittedCandidateBindingV1,
)
from tests.unit.mie.test_fitted_holdout_inference_v1 import (
    _synthetic_bars,
    inference_case,  # noqa: F401 - pytest fixture
)
from tests.unit.mie.test_fitted_holdout_materializer_v1 import (
    materializer_case,  # noqa: F401 - pytest fixture
)
from tests.unit.mie.test_gate3_prospective_campaign_v2 import child


def test_same_coordinate_raw_readback_must_match_every_scheduled_capture(
    materializer_case,  # noqa: F811 - imported pytest fixture
):
    invoke, manifest, _, _, _ = materializer_case

    def check_schedule(readback, **arguments):
        aggregate = aggregate_fitted_holdout_readback_v1(readback, **arguments)
        assert aggregate.contract.manifest_sha256 == manifest.canonical_sha256()
        coordinates = ProspectiveCoordinatePlanV1.model_validate_json(
            arguments["coordinate_plan_bytes"]
        )
        inputs = arguments["inputs"]
        schedule = build_capture_schedule(
            seal=inputs.preregistration,
            coordinate_plan=coordinates,
            planned_at=coordinates.start_at - timedelta(seconds=1),
        )
        # The original raw fixture plans its first two captures at this
        # shared time but uses a later creation time for the last minute.
        assert (
            manifest.rows[0].capture_plan_sha256 == schedule.plans[0].canonical_sha256()
        )
        assert (
            manifest.rows[-1].capture_plan_sha256
            != schedule.plans[-1].canonical_sha256()
        )
        child_readback = FittedHoldoutChildReadbackInputV1(
            schedule=schedule,
            candidate_inputs=inputs,
            readback=readback,
            receipt_bytes=arguments["receipt_bytes"],
            coordinate_plan_bytes=arguments["coordinate_plan_bytes"],
            expected_binding_sha256=arguments["expected_binding_sha256"],
            expected_receipt_sha256=arguments["expected_receipt_sha256"],
            expected_reservation_sha256=arguments["expected_reservation_sha256"],
        )
        with pytest.raises(FittedHoldoutFeatureInventoryError, match="schedule_plan"):
            _require_scheduled_manifest(
                child_readback,
                seal_sha256=inputs.expected_preregistration_sha256,
                coordinate_plan_sha256=coordinates.canonical_sha256(),
                expected_rows=coordinates.expected_rows,
            )

    invoke(process=check_schedule)


def test_individually_valid_early_reservation_cannot_enter_campaign(
    inference_case,  # noqa: F811 - imported pytest fixture
):
    _, fitted = inference_case
    seal, schedule = child(0, rows=ROWS_PER_FOUR_HOURS)
    coordinates = schedule.coordinate_plan
    seal_sha256 = seal.canonical_sha256()
    coordinate_sha256 = coordinates.canonical_sha256()
    reference = CampaignChildV2(
        ordinal=0,
        first_row=0,
        expected_rows=coordinates.expected_rows,
        holdout_id=coordinates.holdout_id,
        seal_sha256=seal_sha256,
        coordinate_plan_sha256=coordinate_sha256,
        schedule_sha256=schedule.canonical_sha256(),
        protocol_sha256=_protocol_sha256(seal),
        start_at=coordinates.start_at,
        end_at=coordinates.end_at,
        first_permitted_access_at=seal.prospective_holdout.first_permitted_access_at,
        seal_declared_at=seal.created_at,
        schedule_declared_at=schedule.planned_at,
    )
    binding = DevvalFittedCandidateBindingV1.model_validate_json(
        fitted["binding_bytes"]
    ).model_copy(
        update={
            "preregistration_sha256": seal_sha256,
            "holdout_coordinate_sha256": coordinate_sha256,
        }
    )
    binding_pin = binding.canonical_sha256()
    inputs = FittedCandidateInputsV1(
        binding=FrozenGate3Artifact(
            binding.canonical_json_bytes(), binding_pin, binding
        ),
        expected_binding_sha256=binding_pin,
        batch=None,
        study=None,
        trial_result=None,
        legacy_scores=None,
        preregistration=seal,
        expected_preregistration_sha256=seal_sha256,
        source_archive_sha256=binding.source_archive_sha256,
    )
    early_at = reference.first_permitted_access_at
    campaign_first = early_at + timedelta(hours=4)
    receipt = b"synthetic child receipt"
    dataset = b"synthetic child dataset"
    reservation = FittedEvaluationReservationV1(
        seal_sha256=seal_sha256,
        binding_sha256=binding_pin,
        acknowledgement_xid="11",
        receipt_sha256=sha(receipt),
        dataset_sha256=sha(dataset),
        attempt_sha256=sha(b"synthetic reservation"),
        database_oid=40,
        evaluator_oid=41,
        reservation_xid="12",
        reserved_at=early_at,
        database_read_at=early_at + timedelta(seconds=1),
    )
    early = FittedHoldoutChildReadbackInputV1(
        schedule=schedule,
        candidate_inputs=inputs,
        readback=ControlledFittedHoldoutReadV1(
            reservation, b"synthetic manifest", dataset
        ),
        receipt_bytes=receipt,
        coordinate_plan_bytes=coordinates.canonical_json_bytes(),
        expected_binding_sha256=binding_pin,
        expected_receipt_sha256=sha(receipt),
        expected_reservation_sha256=fitted_holdout_reservation_sha256(reservation),
    )
    assert _checked_child_binding(early, reference, early_at) == binding
    with pytest.raises(FittedHoldoutFeatureInventoryError, match="custody_changed"):
        _checked_child_binding(early, reference, campaign_first)

    timely = replace(
        reservation,
        reserved_at=campaign_first,
        database_read_at=campaign_first + timedelta(seconds=1),
    )
    later = replace(
        early,
        readback=replace(early.readback, reservation=timely),
        expected_reservation_sha256=fitted_holdout_reservation_sha256(timely),
    )
    assert _checked_child_binding(later, reference, campaign_first) == binding


def test_causal_child_prefixes_reproduce_the_frozen_forecast_inputs(
    inference_case,  # noqa: F811 - imported pytest fixture
):
    inventory, arguments = inference_case
    campaign = ProspectiveCampaignV2.model_validate_json(arguments["campaign_bytes"])
    plan = arguments["feature_plan"]
    rows = (
        list(_synthetic_bars(INSTRUMENTS[0], 1, timedelta(seconds=2))),
        list(_synthetic_bars(INSTRUMENTS[1], -1, timedelta(seconds=3))),
    )
    child_limits = [
        child.first_permitted_access_at
        for child in campaign.children
        for _ in range(child.expected_rows // ROWS_PER_FOUR_HOURS)
    ]
    events = _causal_feature_events(campaign, plan, rows, child_limits)
    assert events == inventory.events
    rebuilt = inventory.model_copy(update={"events": events})
    forecasts = infer_fitted_holdout_v1(
        **{
            **arguments,
            "inventory_bytes": rebuilt.canonical_json_bytes(),
            "expected_inventory_sha256": rebuilt.canonical_sha256(),
        }
    )
    assert forecasts.contract.feature_inventory_sha256 == rebuilt.canonical_sha256()
    assert len(forecasts.contract.events) == len(events) == 3
    rows[0][0] = rows[0][0].model_copy(
        update={"available_at": events[0].bar_closed_at + timedelta(minutes=1)}
    )
    with pytest.raises(ValueError, match="available|history|contiguous"):
        _causal_feature_events(campaign, plan, rows, child_limits)
