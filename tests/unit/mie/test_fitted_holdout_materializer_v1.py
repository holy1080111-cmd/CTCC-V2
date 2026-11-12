"""Synthetic original-byte replay only; no provider, OOS evaluation or authority."""

from base64 import b64encode
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.source_primitives import sha
from app.mie.validation import controlled_fitted_holdout_reader_v1 as reader
from app.mie.validation import fitted_holdout_materializer_v1 as source_prep
from app.mie.validation.artifact import (
    ArtifactVerificationError,
    FrozenGate3Artifact,
    freeze_prospective_holdout_receipt,
)
from app.mie.validation.blind_window_dataset import (
    _rows_sha256,
    bind_complete_blind_window_dataset,
)
from app.mie.validation.contracts import DatasetIdentity, Gate3Claim
from app.mie.validation.fitted_holdout_materializer_v1 import (
    FittedHoldoutMaterializationError,
    FittedHoldoutMinuteTapeV1,
    FittedHoldoutMinuteV1,
    FittedHoldoutRawBundleV1,
    FittedHoldoutRawCaptureV1,
    materialize_fitted_holdout_v1,
    prepare_fitted_holdout_source_v1,
)
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
)
from app.mie.validation.prospective import (
    Gate3ProspectiveHoldoutReceipt,
    Gate3ProspectivePreregistration,
    ProspectiveAccessOutcome,
)
from app.mie.validation.prospective_capture_schedule import ProspectiveCoordinatePlanV1
from app.public_market_source.public_market_receipts import parsed_rows
from tests.unit.mie.test_gate3_blind_window_dataset import fixture_inputs


def _source_inputs(monkeypatch, *, origin=None):
    args, captures_by_journal = fixture_inputs(monkeypatch)
    seal = args["preregistration"]
    window = seal.prospective_holdout
    original_entries = [
        captures_by_journal[id(segment.journal)][0][0] for segment in args["segments"]
    ]
    coordinates = ProspectiveCoordinatePlanV1(
        holdout_id=window.holdout_id,
        origin=origin or original_entries[0][0].origin,
        instrument_ids=window.instrument_ids,
        start_at=window.start_at,
        end_at=window.end_at,
        expected_rows=window.expected_rows,
    )
    seal_fields = seal.model_dump(mode="python")
    seal_fields["prospective_holdout"]["coordinate_plan_sha256"] = (
        coordinates.canonical_sha256()
    )
    seal = Gate3ProspectivePreregistration.model_validate(seal_fields)
    return {
        "preregistration": seal,
        "expected_preregistration_sha256": seal.canonical_sha256(),
        "segments": args["segments"],
        "coordinate_plan_bytes": coordinates.canonical_json_bytes(),
    }, original_entries


@pytest.fixture
def materializer_case(monkeypatch):
    source_args, original_entries = _source_inputs(monkeypatch)
    seal = source_args["preregistration"]
    seal_sha = source_args["expected_preregistration_sha256"]
    manifest = bind_complete_blind_window_dataset(
        preregistration=seal,
        expected_preregistration_sha256=seal_sha,
        segments=source_args["segments"],
    )
    window = seal.prospective_holdout
    bundle = FittedHoldoutRawBundleV1(
        manifest_sha256=manifest.canonical_sha256(),
        captures=tuple(
            FittedHoldoutRawCaptureV1(
                receipt=receipt,
                time_before_base64=b64encode(raw["time-before.raw"]).decode("ascii"),
                page_base64=b64encode(raw["page-000.raw"]).decode("ascii"),
                time_after_base64=b64encode(raw["time-after.raw"]).decode("ascii"),
            )
            for _, receipt, raw, _ in original_entries
        ),
    )
    prepared = prepare_fitted_holdout_source_v1(**source_args)
    assert prepared.manifest.payload == manifest.canonical_json_bytes()
    assert prepared.dataset.payload == bundle.canonical_json_bytes()
    binding_bytes = b"synthetic fitted binding"
    binding_sha = sha(binding_bytes)
    inputs = FittedCandidateInputsV1(
        binding=FrozenGate3Artifact(binding_bytes, binding_sha, None),
        expected_binding_sha256=binding_sha,
        batch=None,
        study=None,
        trial_result=None,
        legacy_scores=None,
        preregistration=seal,
        expected_preregistration_sha256=seal_sha,
        source_archive_sha256=sha(b"synthetic source"),
    )
    verified = []

    def verify_fitted_inputs(candidate):
        assert candidate is inputs
        verified.append(candidate.expected_binding_sha256)

    # Keep the actual readback, receipt and raw replay. The independent fitted
    # model recomputation itself is covered by the fitted custody fixtures.
    monkeypatch.setattr(reader, "_verified", verify_fitted_inputs)

    def no_holdout_open(*_args, **_kwargs):
        pytest.fail("materialization attempted to reopen the protected files")

    monkeypatch.setattr(reader, "_read_pinned_file", no_holdout_open)

    def invocation(*, manifest_bytes=None, dataset_bytes=None, coordinate_bytes=None):
        if manifest_bytes is None:
            manifest_bytes = prepared.manifest.payload
        if dataset_bytes is None:
            dataset_bytes = prepared.dataset.payload
        if coordinate_bytes is None:
            coordinate_bytes = source_args["coordinate_plan_bytes"]
        read_at = window.first_permitted_access_at
        dataset = DatasetIdentity(
            dataset_id="holdout:synthetic:fitted-minute-wire:v1",
            source=window.source,
            source_version=window.source_version,
            instrument_ids=window.instrument_ids,
            manifest_sha256=sha(manifest_bytes),
            content_sha256=sha(dataset_bytes),
            expected_rows=window.expected_rows,
            first_event_at=window.start_at + timedelta(minutes=1),
            last_event_at=window.end_at,
            frozen_at=read_at,
            event_time_field="closed_at",
            available_time_field="available_at",
        )
        receipt = freeze_prospective_holdout_receipt(
            Gate3ProspectiveHoldoutReceipt(
                receipt_id="holdout:synthetic:acquisition:v1",
                recorded_at=read_at,
                preregistration=seal,
                preregistration_sha256=seal_sha,
                first_accessed_at=read_at,
                holdout_dataset=dataset,
                acquisition_plan_sha256=window.coordinate_plan_sha256,
                artifact_count=window.expected_artifact_count,
                all_artifacts_verified=True,
                access_policy_compliant=True,
                access_outcome=ProspectiveAccessOutcome.SEALED_UNREAD,
                candidate_changed_after_preregistration=False,
                predictive_oos_eligible=True,
            )
        )
        reservation = FittedEvaluationReservationV1(
            seal_sha256=seal_sha,
            binding_sha256=binding_sha,
            acknowledgement_xid="11",
            receipt_sha256=receipt.sha256,
            dataset_sha256=dataset.content_sha256,
            attempt_sha256=sha(b"synthetic attempt"),
            database_oid=40,
            evaluator_oid=41,
            reservation_xid="12",
            reserved_at=read_at,
            database_read_at=read_at + timedelta(seconds=1),
        )
        return materialize_fitted_holdout_v1(
            reader.ControlledFittedHoldoutReadV1(
                reservation, manifest_bytes, dataset_bytes
            ),
            inputs=inputs,
            receipt_bytes=receipt.payload,
            coordinate_plan_bytes=coordinate_bytes,
            expected_binding_sha256=binding_sha,
            expected_receipt_sha256=receipt.sha256,
            expected_reservation_sha256=reader.fitted_holdout_reservation_sha256(
                reservation
            ),
        )

    first_raw_rows, _ = parsed_rows(original_entries[0][2]["page-000.raw"])
    return invocation, manifest, bundle, first_raw_rows[0], verified


def test_source_preparation_refuses_origin_outside_sealed_coordinates(monkeypatch):
    args, _ = _source_inputs(monkeypatch, origin="https://eea.okx.com")
    with pytest.raises(
        FittedHoldoutMaterializationError, match="fitted_holdout_raw_source_mismatch"
    ):
        prepare_fitted_holdout_source_v1(**args)


def test_source_preparation_respects_protected_reader_wire_limit(monkeypatch):
    args, _ = _source_inputs(monkeypatch)
    prepared = prepare_fitted_holdout_source_v1(**args)
    monkeypatch.setattr(
        source_prep, "MAX_DATASET_BYTES", len(prepared.dataset.payload) - 1
    )
    with pytest.raises(
        FittedHoldoutMaterializationError, match="fitted_holdout_source_size_exceeded"
    ):
        prepare_fitted_holdout_source_v1(**args)


def test_replays_exact_bound_minutes_deterministically(materializer_case):
    materialize, manifest, bundle, raw, verified = materializer_case
    tape = materialize()
    assert tape.payload == materialize().payload
    assert len(verified) == 2
    decoded = FittedHoldoutMinuteTapeV1.model_validate_json(tape.payload)
    assert decoded == tape.contract
    assert decoded.canonical_json_bytes() == tape.payload
    bodies = [minute.canonical_json_bytes() for minute in decoded.minutes]
    assert decoded.minutes_sha256 == sha(
        b"".join(len(body).to_bytes(8, "big") + body for body in bodies)
    )
    assert tape.contract.manifest_sha256 == manifest.canonical_sha256()
    assert tape.contract.dataset_sha256 == bundle.canonical_sha256()
    assert tape.contract.current_claim is Gate3Claim.COMPUTATIONAL
    assert not tape.contract.independent_source_custody_verified
    assert not tape.contract.independent_first_access_verified
    assert not tape.contract.predictive_oos_eligible
    assert (
        not tape.contract.promotion_eligible and not tape.contract.execution_authority
    )
    assert (
        tape.contract.runtime_consumers == 0
        and tape.contract.volume_unit == "contracts"
    )
    assert len(tape.contract.minutes) == len(manifest.rows) == 4
    first = tape.contract.minutes[0]
    assert tuple(
        getattr(first.row.bar, field)
        for field in ("open", "high", "low", "close", "volume")
    ) == tuple(map(Decimal, raw[1:6]))
    for minute, locator in zip(tape.contract.minutes, manifest.rows, strict=True):
        assert minute.evidence == locator
        assert minute.row.instrument_id == locator.instrument_id
        assert minute.row.source_row_sha256 == locator.source_row_sha256
        assert minute.evidence.observed_at < minute.evidence.retained_at
        assert (
            minute.row.available_at
            == minute.evidence.decision_available_at
            == minute.evidence.retained_at
        )


def test_minute_contract_refuses_observation_time_as_decision_availability(
    materializer_case,
):
    materialize, _, _, _, _ = materializer_case
    minute = materialize().contract.minutes[0]
    earlier = minute.row.model_copy(
        update={"available_at": minute.evidence.observed_at}
    )
    changed = minute.model_copy(update={"row": earlier})
    with pytest.raises(
        ValidationError, match="fitted_holdout_minute_evidence_mismatch"
    ):
        FittedHoldoutMinuteV1.model_validate_json(changed.canonical_json_bytes())


def test_tape_contract_rejects_tampered_minute_inventory_digest(materializer_case):
    materialize, _, _, _, _ = materializer_case
    changed = materialize().contract.model_copy(update={"minutes_sha256": "0" * 64})
    with pytest.raises(
        ValidationError, match="fitted_holdout_minute_inventory_mismatch"
    ):
        FittedHoldoutMinuteTapeV1.model_validate_json(changed.canonical_json_bytes())


def test_rehashed_outer_artifacts_do_not_admit_changed_raw_page(materializer_case):
    materialize, _, bundle, _, verified = materializer_case
    changed_capture = bundle.captures[0].model_copy(
        update={"page_base64": b64encode(b'{"data":[]}').decode("ascii")}
    )
    changed = bundle.model_copy(
        update={"captures": (changed_capture, *bundle.captures[1:])}
    )
    with pytest.raises(FittedHoldoutMaterializationError, match="raw_replay_refused"):
        materialize(dataset_bytes=changed.canonical_json_bytes())
    assert len(verified) == 1


@pytest.mark.parametrize("change", ["missing", "reordered", "duplicate"])
def test_missing_reordered_or_duplicate_capture_refused(materializer_case, change):
    materialize, _, bundle, _, _ = materializer_case
    a, b, *rest = bundle.captures
    captures, reason = {
        "missing": ((b, *rest), "source_identity_mismatch"),
        "reordered": ((b, a, *rest), "capture_pin_mismatch"),
        "duplicate": ((a, a, *rest), "capture_pin_mismatch"),
    }[change]
    changed = bundle.model_copy(update={"captures": captures})
    with pytest.raises(FittedHoldoutMaterializationError, match=reason):
        materialize(dataset_bytes=changed.canonical_json_bytes())


def test_raw_observation_chronology_is_recomputed_after_manifest_rehash(
    materializer_case,
):
    materialize, manifest, bundle, _, _ = materializer_case
    first, *rest = manifest.rows
    before = first.observed_at - timedelta(microseconds=1)
    assert first.request_started_at <= before
    rows = (first.model_copy(update={"observed_at": before}), *rest)
    changed_manifest = manifest.model_copy(
        update={"rows": rows, "rows_sha256": _rows_sha256(rows)}
    )
    changed_bundle = bundle.model_copy(
        update={"manifest_sha256": changed_manifest.canonical_sha256()}
    )
    with pytest.raises(FittedHoldoutMaterializationError, match="row_locator_mismatch"):
        materialize(
            manifest_bytes=changed_manifest.canonical_json_bytes(),
            dataset_bytes=changed_bundle.canonical_json_bytes(),
        )


def test_late_retention_is_rejected_even_with_new_outer_pins(materializer_case):
    materialize, manifest, bundle, _, _ = materializer_case
    *rest, last = manifest.rows
    late = manifest.first_permitted_evaluator_access_at
    rows = (
        *rest,
        last.model_copy(update={"retained_at": late, "decision_available_at": late}),
    )
    changed_manifest = manifest.model_copy(
        update={"rows": rows, "rows_sha256": _rows_sha256(rows)}
    )
    changed_bundle = bundle.model_copy(
        update={"manifest_sha256": changed_manifest.canonical_sha256()}
    )
    with pytest.raises(ArtifactVerificationError, match="schema validation failed"):
        materialize(
            manifest_bytes=changed_manifest.canonical_json_bytes(),
            dataset_bytes=changed_bundle.canonical_json_bytes(),
        )


def test_opaque_legacy_wire_and_changed_coordinate_pin_refused(materializer_case):
    materialize, _, bundle, _, _ = materializer_case
    with pytest.raises(ArtifactVerificationError, match="schema validation failed"):
        materialize(dataset_bytes=b"legacy opaque dataset")
    with pytest.raises(ArtifactVerificationError, match="SHA256 mismatch"):
        materialize(coordinate_bytes=b"{}")
    with pytest.raises(ArtifactVerificationError, match="not canonical"):
        materialize(dataset_bytes=bundle.canonical_json_bytes() + b"\n")
