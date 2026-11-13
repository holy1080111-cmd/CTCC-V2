"""Synthetic controls for a DB0046 prediction retention precursor, no DB I/O."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.public_runtime_bundle_custody import (
    RetainedRuntimeBundleReadback,
    RuntimeBundleCustodyError,
    runtime_bundle_id_v1,
)
from app.domain.source_primitives import canonical, sha
from app.mie.validation import (
    prospective_devval_prediction_db0046_precursor_v1 as custody,
)
from app.mie.validation.prospective_devval_validation_inputs_v1 import (
    ValidationEventIdentityV1,
)
from app.mie.validation.prospective_devval_validation_prediction_v1 import (
    DevvalValidationPredictionV1,
    ValidationPredictionSideV1,
)


def _missing_side(symbol):
    return ValidationPredictionSideV1(
        instrument_id=symbol,
        status="missing",
        feature_replay_sha256=None,
        raw_result_sha256=None,
        raw_direction=None,
        bin_index=None,
        probability=None,
    )


def _case():
    close = datetime(2026, 11, 20, 4, tzinfo=UTC)
    prediction = DevvalValidationPredictionV1(
        identity=ValidationEventIdentityV1(
            schedule_sha256="a" * 64,
            coverage_sha256="b" * 64,
            batch_sha256="c" * 64,
            slot_ordinal=7,
            event_closed_at=close,
            decision_at=None,
            decision_cutoff_at=close + timedelta(minutes=1),
        ),
        feature_projection_sha256="d" * 64,
        raw_event_sha256="e" * 64,
        policy_sha256="f" * 64,
        development_fit_sha256="1" * 64,
        development_fit_input_sha256="2" * 64,
        final_development_rows_sha256="3" * 64,
        latest_development_label_read_at=close - timedelta(hours=25),
        validation_feature_history_start_at=close - timedelta(hours=24),
        feature_plan_sha256="4" * 64,
        scorer_config_sha256="5" * 64,
        status="incomplete",
        sides=(_missing_side("BTC-USDT-SWAP"), _missing_side("ETH-USDT-SWAP")),
    )
    payload = prediction.canonical_json_bytes()
    pins = custody.PredictionCustodyPinsV1(
        prediction_sha256=sha(payload),
        schedule_sha256=prediction.identity.schedule_sha256,
        coverage_sha256=prediction.identity.coverage_sha256,
        batch_sha256=prediction.identity.batch_sha256,
        slot_ordinal=prediction.identity.slot_ordinal,
        feature_projection_sha256=prediction.feature_projection_sha256,
        raw_event_sha256=prediction.raw_event_sha256,
        policy_sha256=prediction.policy_sha256,
        development_fit_sha256=prediction.development_fit_sha256,
        development_fit_input_sha256=prediction.development_fit_input_sha256,
        feature_plan_sha256=prediction.feature_plan_sha256,
        scorer_config_sha256=prediction.scorer_config_sha256,
    )
    return payload, pins


def _readback(payload, pins, *, changed=False):
    proof = custody.prediction_proof_envelope_sha256_v1(pins)
    at = datetime(2026, 11, 20, 4, 0, 1, tzinfo=UTC)
    return RetainedRuntimeBundleReadback(
        bundle_id=runtime_bundle_id_v1(
            bundle_sha256=pins.prediction_sha256, proof_envelope_sha256=proof
        ),
        bundle_sha256=pins.prediction_sha256,
        proof_envelope_sha256=proof,
        recorded_at=at,
        database_readback_at=at + timedelta(milliseconds=1),
        bundle_raw=payload + b" " if changed else payload,
        append_backend_pid=101,
        append_transaction_id="211",
        readback_backend_pid=102,
        readback_transaction_id="212",
    )


class _FakeRepository:
    def __init__(self, readback):
        self.readback = readback
        self.calls = []
        self.uncertain = False

    async def verify_deployment(self):
        self.calls.append("verify")

    async def seal_bundle(self, **kwargs):
        self.calls.append(("seal", kwargs))
        if self.uncertain:
            raise RuntimeBundleCustodyError("runtime_bundle_commit_uncertain")
        return object()

    async def read_retained_bundle_by_pins(self, **kwargs):
        self.calls.append(("read", kwargs))
        return self.readback


@pytest.mark.asyncio
async def test_prediction_is_replayed_then_db0046_commit_readback_is_consumed(
    monkeypatch,
):
    payload, pins = _case()
    readback = _readback(payload, pins)
    repository = _FakeRepository(readback)
    monkeypatch.setattr(custody, "RuntimeBundleCustodyRepository", _FakeRepository)

    def consume(receipt, **kwargs):
        assert kwargs == {
            "expected_bundle_sha256": pins.prediction_sha256,
            "expected_proof_envelope_sha256": readback.proof_envelope_sha256,
        }
        repository.calls.append("consume")
        return readback

    monkeypatch.setattr(custody, "_consume_runtime_bundle_custody_receipt", consume)
    observed = await custody.publish_prediction_precursor_v1(
        repository, prediction_payload=payload, pins=pins
    )
    assert [
        item[0] if isinstance(item, tuple) else item for item in repository.calls
    ] == [
        "verify",
        "seal",
        "consume",
    ]
    assert repository.calls[1][1] == {
        "bundle_raw": payload,
        "expected_sha256": pins.prediction_sha256,
        "proof_envelope_sha256": readback.proof_envelope_sha256,
    }
    assert observed.mode == "published"
    assert observed.proof_envelope_sha256 != pins.prediction_sha256
    assert observed.append_transaction_id == "211"
    assert observed.prediction.identity.slot_ordinal == 7
    assert observed.status == "retained_computational"
    assert not any(
        (
            observed.independent_custody_verified,
            observed.pre_outcome_publication_verified,
            observed.evaluator_first_access_verified,
            observed.predictive_oos_eligible,
            observed.execution_authority,
        )
    )


@pytest.mark.asyncio
async def test_changed_or_outcome_bearing_prediction_denies_before_db(monkeypatch):
    payload, pins = _case()
    repository = _FakeRepository(_readback(payload, pins))
    monkeypatch.setattr(custody, "RuntimeBundleCustodyRepository", _FakeRepository)
    with pytest.raises(custody.PredictionCustodyPrecursorError, match="pins_changed"):
        await custody.publish_prediction_precursor_v1(
            repository,
            prediction_payload=payload,
            pins=replace(pins, slot_ordinal=8),
        )
    altered = json.loads(payload)
    altered["validation_outcome"] = "synthetic"
    outcome_bearing = canonical(altered)
    with pytest.raises(
        custody.PredictionCustodyPrecursorError, match="prediction_invalid"
    ):
        await custody.publish_prediction_precursor_v1(
            repository,
            prediction_payload=outcome_bearing,
            pins=replace(pins, prediction_sha256=sha(outcome_bearing)),
        )
    assert repository.calls == []


@pytest.mark.asyncio
async def test_uncertain_append_never_retries_and_recovery_only_reads(monkeypatch):
    payload, pins = _case()
    readback = _readback(payload, pins)
    repository = _FakeRepository(readback)
    repository.uncertain = True
    monkeypatch.setattr(custody, "RuntimeBundleCustodyRepository", _FakeRepository)
    with pytest.raises(
        custody.PredictionCustodyPrecursorError, match="commit_uncertain"
    ):
        await custody.publish_prediction_precursor_v1(
            repository, prediction_payload=payload, pins=pins
        )
    assert [
        item[0] if isinstance(item, tuple) else item for item in repository.calls
    ] == [
        "verify",
        "seal",
    ]
    fresh = _FakeRepository(
        replace(readback, append_backend_pid=None, append_transaction_id=None)
    )
    recovered = await custody.read_prediction_precursor_v1(
        fresh, prediction_payload=payload, pins=pins
    )
    assert [item[0] if isinstance(item, tuple) else item for item in fresh.calls] == [
        "verify",
        "read",
    ]
    assert recovered.mode == "recovered_read_only"
    assert recovered.append_transaction_id is None
    assert recovered.pre_outcome_publication_verified is False


def test_event_pins_change_the_domain_separated_db0046_proof():
    _, pins = _case()
    first = custody.prediction_proof_envelope_sha256_v1(pins)
    second = custody.prediction_proof_envelope_sha256_v1(
        replace(pins, slot_ordinal=pins.slot_ordinal + 1)
    )
    assert first != second
    assert first != pins.prediction_sha256
    with pytest.raises(custody.PredictionCustodyPrecursorError, match="input_invalid"):
        custody.prediction_proof_envelope_sha256_v1(replace(pins, slot_ordinal=True))


@pytest.mark.asyncio
async def test_changed_readback_denied_without_promotion(monkeypatch):
    payload, pins = _case()
    repository = _FakeRepository(_readback(payload, pins, changed=True))
    monkeypatch.setattr(custody, "RuntimeBundleCustodyRepository", _FakeRepository)
    with pytest.raises(
        custody.PredictionCustodyPrecursorError, match="readback_changed"
    ):
        await custody.read_prediction_precursor_v1(
            repository, prediction_payload=payload, pins=pins
        )
