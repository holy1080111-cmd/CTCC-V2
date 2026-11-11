"""Check the file boundary against typed reservation outcomes without a database."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import sha
from app.mie.validation.artifact import (
    FrozenGate3Artifact,
    freeze_prospective_holdout_receipt,
)
from app.mie.validation.contracts import Gate3Claim
from app.mie.validation.controlled_fitted_holdout_reader_v1 import (
    ControlledFittedHoldoutReadV1,
    fitted_holdout_reservation_sha256,
    read_fitted_prospective_holdout_once,
    verify_fitted_holdout_readback_v1,
)
from app.mie.validation.controlled_holdout_reader import ControlledHoldoutReadError
from app.mie.validation.gate3_fitted_candidate_custody_v1 import (
    FittedCandidateInputsV1,
    FittedEvaluationReservationV1,
    Gate3FittedCandidateCustodyV1,
    Gate3FittedCustodyError,
)
from tests.unit.mie.test_gate3_prospective import valid_receipt


@pytest.fixture
def read_case(tmp_path):
    receipt = freeze_prospective_holdout_receipt(valid_receipt())
    seal = receipt.contract.preregistration
    binding = FrozenGate3Artifact(b"fixture binding", "a" * 64, None)
    inputs = FittedCandidateInputsV1(
        binding=binding,
        expected_binding_sha256=binding.sha256,
        batch=None,
        study=None,
        trial_result=None,
        legacy_scores=None,
        preregistration=seal,
        expected_preregistration_sha256=seal.canonical_sha256(),
        source_archive_sha256="b" * 64,
    )
    arguments = {
        "custody": object.__new__(Gate3FittedCandidateCustodyV1),
        "inputs": inputs,
        "expected_ack_sha256": "c" * 64,
        "receipt_bytes": receipt.payload,
        "expected_receipt_sha256": receipt.sha256,
        "manifest_path": tmp_path / "manifest",
        "dataset_path": tmp_path / "dataset",
    }
    reservation = FittedEvaluationReservationV1(
        seal_sha256=inputs.expected_preregistration_sha256,
        binding_sha256=binding.sha256,
        acknowledgement_xid="11",
        receipt_sha256=receipt.sha256,
        dataset_sha256=receipt.contract.holdout_dataset.content_sha256,
        attempt_sha256="d" * 64,
        database_oid=42,
        evaluator_oid=43,
        reservation_xid="12",
        reserved_at=receipt.contract.recorded_at,
        database_read_at=receipt.contract.recorded_at + timedelta(seconds=1),
    )
    return arguments, reservation


@pytest.fixture
def replay_case(monkeypatch, read_case):
    arguments, reservation = read_case
    calls = []

    def replay_fitted_inputs(inputs):
        assert inputs is arguments["inputs"]
        calls.append(inputs)

    def unexpected_access(*_args, **_kwargs):
        pytest.fail("readback attempted path access or a second reservation")

    monkeypatch.setattr(
        "app.mie.validation.controlled_fitted_holdout_reader_v1._verified",
        replay_fitted_inputs,
    )
    monkeypatch.setattr(
        "app.mie.validation.controlled_fitted_holdout_reader_v1._read_pinned_file",
        unexpected_access,
    )
    monkeypatch.setattr(
        Gate3FittedCandidateCustodyV1, "reserve_evaluation", unexpected_access
    )
    return (
        ControlledFittedHoldoutReadV1(
            reservation, b"holdout-manifest", b"holdout-content"
        ),
        {
            "inputs": arguments["inputs"],
            "receipt_bytes": arguments["receipt_bytes"],
            "expected_binding_sha256": arguments["inputs"].expected_binding_sha256,
            "expected_receipt_sha256": arguments["expected_receipt_sha256"],
            "expected_reservation_sha256": fitted_holdout_reservation_sha256(
                reservation
            ),
        },
        calls,
    )


def test_offline_readback_is_deterministic_and_computational_only(replay_case):
    bundle, pins, calls = replay_case
    result = verify_fitted_holdout_readback_v1(bundle, **pins)
    assert verify_fitted_holdout_readback_v1(bundle, **pins) == result
    assert calls == [pins["inputs"], pins["inputs"]]
    assert result.current_claim is Gate3Claim.COMPUTATIONAL
    assert result.binding_sha256 == pins["expected_binding_sha256"]
    assert result.receipt_sha256 == pins["expected_receipt_sha256"]
    assert result.reservation_sha256 == pins["expected_reservation_sha256"]
    assert result.manifest_sha256 == sha(bundle.manifest_bytes)
    assert result.dataset_sha256 == sha(bundle.dataset_bytes)
    assert not result.independent_first_access_verified
    assert not result.predictive_oos_eligible and not result.execution_authority


@pytest.mark.parametrize("field", ["manifest_bytes", "dataset_bytes"])
def test_offline_readback_refuses_changed_holdout_bytes(replay_case, field):
    bundle, pins, calls = replay_case
    changed = replace(bundle, **{field: b"different"})
    with pytest.raises(ControlledHoldoutReadError, match="readback_bytes_mismatch"):
        verify_fitted_holdout_readback_v1(changed, **pins)
    assert not calls


def test_offline_readback_requires_independent_reservation_and_binding_pins(
    replay_case,
):
    bundle, pins, calls = replay_case
    changed = replace(
        bundle, reservation=replace(bundle.reservation, attempt_sha256="f" * 64)
    )
    with pytest.raises(ControlledHoldoutReadError, match="reservation_pin_mismatch"):
        verify_fitted_holdout_readback_v1(changed, **pins)
    pins["expected_binding_sha256"] = "e" * 64
    with pytest.raises(ControlledHoldoutReadError, match="readback_inputs_invalid"):
        verify_fitted_holdout_readback_v1(bundle, **pins)
    assert not calls


def test_offline_readback_refuses_pinned_reservation_from_a_different_receipt(
    replay_case,
):
    bundle, pins, calls = replay_case
    wrong_receipt = replace(bundle.reservation, receipt_sha256="e" * 64)
    pins["expected_reservation_sha256"] = fitted_holdout_reservation_sha256(
        wrong_receipt
    )
    with pytest.raises(ControlledHoldoutReadError, match="reservation_mismatch"):
        verify_fitted_holdout_readback_v1(
            replace(bundle, reservation=wrong_receipt), **pins
        )
    assert not calls


def test_offline_readback_refuses_reservation_before_acquisition_receipt(replay_case):
    bundle, pins, calls = replay_case
    too_early = replace(
        bundle.reservation,
        reserved_at=bundle.reservation.reserved_at - timedelta(seconds=1),
    )
    pins["expected_reservation_sha256"] = fitted_holdout_reservation_sha256(too_early)
    with pytest.raises(ControlledHoldoutReadError, match="reservation_mismatch"):
        verify_fitted_holdout_readback_v1(
            replace(bundle, reservation=too_early), **pins
        )
    assert not calls


@pytest.mark.asyncio
async def test_failed_reservation_stops_before_holdout_path_lookup(
    monkeypatch, read_case
):
    arguments, _ = read_case

    async def deny(*_args, **_kwargs):
        raise Gate3FittedCustodyError("synthetic reservation denial")

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", deny)
    # Both file names are absent. A premature lookup would raise a path error.
    with pytest.raises(Gate3FittedCustodyError, match="synthetic reservation denial"):
        await read_fitted_prospective_holdout_once(**arguments)


@pytest.mark.asyncio
async def test_changed_reservation_cannot_reach_holdout_file(monkeypatch, read_case):
    arguments, reservation = read_case

    async def changed(*_args, **_kwargs):
        return replace(reservation, dataset_sha256="e" * 64)

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", changed)
    with pytest.raises(ControlledHoldoutReadError, match="reservation_mismatch"):
        await read_fitted_prospective_holdout_once(**arguments)


@pytest.mark.asyncio
async def test_matching_reservation_reads_exact_bytes_without_predictive_authority(
    monkeypatch, read_case
):
    arguments, reservation = read_case
    arguments["manifest_path"].write_bytes(b"holdout-manifest")
    arguments["dataset_path"].write_bytes(b"holdout-content")

    async def committed(
        _self, inputs, *, expected_ack_sha256, receipt_sha256, dataset_sha256
    ):
        assert inputs is arguments["inputs"]
        assert expected_ack_sha256 == arguments["expected_ack_sha256"]
        assert receipt_sha256 == reservation.receipt_sha256
        assert dataset_sha256 == reservation.dataset_sha256
        return reservation

    monkeypatch.setattr(Gate3FittedCandidateCustodyV1, "reserve_evaluation", committed)
    result = await read_fitted_prospective_holdout_once(**arguments)
    assert result.manifest_bytes == b"holdout-manifest"
    assert result.dataset_bytes == b"holdout-content"
    assert result.reservation == reservation
    assert not result.independent_first_access_verified
    assert not result.predictive_oos_eligible and not result.execution_authority
