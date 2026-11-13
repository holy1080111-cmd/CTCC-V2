"""Retain one label-free Validation prediction through existing DB0046 custody.

This is a computational precursor only. DB0046 has no per-event unique key,
independent custodian, or outcome-release gate; its timestamps cannot prove
that a prediction preceded first access to Validation outcomes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import ClassVar, Literal

from app.database.repositories.public_runtime_bundle_custody import (
    RetainedRuntimeBundleReadback,
    RuntimeBundleCustodyError,
    RuntimeBundleCustodyRepository,
    _consume_runtime_bundle_custody_receipt,
    runtime_bundle_id_v1,
)
from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import SHA256_RE, _verify
from app.mie.validation.prospective_devval_validation_prediction_v1 import (
    _MAX_PREDICTION_BYTES,
    DevvalValidationPredictionV1,
)

_ENVELOPE_SCHEMA = "ctcc.mie.devval_prediction_db0046_precursor.v1"


class PredictionCustodyPrecursorError(ValueError):
    """A fixed refusal without prediction, database, or connection content."""


@dataclass(frozen=True, slots=True)
class PredictionCustodyPinsV1:
    """Caller-supplied exact pins; this type proves no independent retention."""

    prediction_sha256: str
    schedule_sha256: str
    coverage_sha256: str
    batch_sha256: str
    slot_ordinal: int
    feature_projection_sha256: str
    raw_event_sha256: str
    policy_sha256: str
    development_fit_sha256: str
    development_fit_input_sha256: str
    feature_plan_sha256: str
    scorer_config_sha256: str


@dataclass(frozen=True, slots=True)
class PredictionCustodyObservationV1:
    """A committed DB0046 readback, with no chronology or access claim."""

    mode: Literal["published", "recovered_read_only"]
    bundle_id: str
    prediction_sha256: str
    proof_envelope_sha256: str
    recorded_at: datetime
    database_readback_at: datetime
    append_transaction_id: str | None
    readback_transaction_id: str | None
    prediction: DevvalValidationPredictionV1 = field(repr=False, compare=False)
    status: ClassVar[Literal["retained_computational"]] = "retained_computational"
    independent_custody_verified: ClassVar[Literal[False]] = False
    pre_outcome_publication_verified: ClassVar[Literal[False]] = False
    evaluator_first_access_verified: ClassVar[Literal[False]] = False
    predictive_oos_eligible: ClassVar[Literal[False]] = False
    execution_authority: ClassVar[Literal[False]] = False


def _validated_pins(pins: PredictionCustodyPinsV1) -> dict:
    if (
        type(pins) is not PredictionCustodyPinsV1
        or type(pins.slot_ordinal) is not int
        or pins.slot_ordinal < 0
        or any(
            type(value) is not str or SHA256_RE.fullmatch(value) is None
            for name, value in asdict(pins).items()
            if name != "slot_ordinal"
        )
    ):
        raise PredictionCustodyPrecursorError("prediction_precursor_input_invalid")
    return asdict(pins)


def _checked_prediction(
    payload: bytes, pins: PredictionCustodyPinsV1
) -> DevvalValidationPredictionV1:
    _validated_pins(pins)
    if type(payload) is not bytes or not 0 < len(payload) <= _MAX_PREDICTION_BYTES:
        raise PredictionCustodyPrecursorError("prediction_precursor_input_invalid")
    try:
        prediction = _verify(
            payload,
            expected_sha256=pins.prediction_sha256,
            contract_type=DevvalValidationPredictionV1,
        )
    except ValueError:
        raise PredictionCustodyPrecursorError(
            "prediction_precursor_prediction_invalid"
        ) from None
    if (
        prediction.identity.schedule_sha256 != pins.schedule_sha256
        or prediction.identity.coverage_sha256 != pins.coverage_sha256
        or prediction.identity.batch_sha256 != pins.batch_sha256
        or prediction.identity.slot_ordinal != pins.slot_ordinal
        or prediction.feature_projection_sha256 != pins.feature_projection_sha256
        or prediction.raw_event_sha256 != pins.raw_event_sha256
        or prediction.policy_sha256 != pins.policy_sha256
        or prediction.development_fit_sha256 != pins.development_fit_sha256
        or prediction.development_fit_input_sha256 != pins.development_fit_input_sha256
        or prediction.feature_plan_sha256 != pins.feature_plan_sha256
        or prediction.scorer_config_sha256 != pins.scorer_config_sha256
    ):
        raise PredictionCustodyPrecursorError("prediction_precursor_pins_changed")
    return prediction


def prediction_proof_envelope_sha256_v1(pins: PredictionCustodyPinsV1) -> str:
    """Bind caller-supplied event/prediction pins to an opaque DB0046 row."""
    return sha(canonical({"schema": _ENVELOPE_SCHEMA, "pins": _validated_pins(pins)}))


def _observation(
    readback: RetainedRuntimeBundleReadback,
    *,
    payload: bytes,
    pins: PredictionCustodyPinsV1,
    prediction: DevvalValidationPredictionV1,
    mode: Literal["published", "recovered_read_only"],
) -> PredictionCustodyObservationV1:
    proof = prediction_proof_envelope_sha256_v1(pins)
    bundle_id = runtime_bundle_id_v1(
        bundle_sha256=pins.prediction_sha256, proof_envelope_sha256=proof
    )
    if (
        type(readback) is not RetainedRuntimeBundleReadback
        or readback.bundle_id != bundle_id
        or readback.bundle_sha256 != pins.prediction_sha256
        or readback.proof_envelope_sha256 != proof
        or readback.bundle_raw != payload
        or type(readback.recorded_at) is not datetime
        or type(readback.database_readback_at) is not datetime
        or readback.recorded_at.tzinfo is None
        or readback.database_readback_at.tzinfo is None
        or readback.recorded_at > readback.database_readback_at
        or (mode == "published" and readback.append_transaction_id is None)
    ):
        raise PredictionCustodyPrecursorError("prediction_precursor_readback_changed")
    return PredictionCustodyObservationV1(
        mode=mode,
        bundle_id=bundle_id,
        prediction_sha256=pins.prediction_sha256,
        proof_envelope_sha256=proof,
        recorded_at=readback.recorded_at,
        database_readback_at=readback.database_readback_at,
        append_transaction_id=readback.append_transaction_id,
        readback_transaction_id=readback.readback_transaction_id,
        prediction=prediction,
    )


async def publish_prediction_precursor_v1(
    repository: RuntimeBundleCustodyRepository,
    *,
    prediction_payload: bytes,
    pins: PredictionCustodyPinsV1,
) -> PredictionCustodyObservationV1:
    """Append once using DB0046 and consume its actual separate-session receipt."""
    if type(repository) is not RuntimeBundleCustodyRepository:
        raise PredictionCustodyPrecursorError("prediction_precursor_repository_invalid")
    prediction = _checked_prediction(prediction_payload, pins)
    proof = prediction_proof_envelope_sha256_v1(pins)
    try:
        await repository.verify_deployment()
        receipt = await repository.seal_bundle(
            bundle_raw=prediction_payload,
            expected_sha256=pins.prediction_sha256,
            proof_envelope_sha256=proof,
        )
        readback = _consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=pins.prediction_sha256,
            expected_proof_envelope_sha256=proof,
        )
    except RuntimeBundleCustodyError as error:
        code = (
            "prediction_precursor_commit_uncertain"
            if error.args == ("runtime_bundle_commit_uncertain",)
            else "prediction_precursor_custody_unavailable"
        )
        raise PredictionCustodyPrecursorError(code) from None
    return _observation(
        readback,
        payload=prediction_payload,
        pins=pins,
        prediction=prediction,
        mode="published",
    )


async def read_prediction_precursor_v1(
    repository: RuntimeBundleCustodyRepository,
    *,
    prediction_payload: bytes,
    pins: PredictionCustodyPinsV1,
) -> PredictionCustodyObservationV1:
    """Recover by exact externally retained pins; never append on uncertainty."""
    if type(repository) is not RuntimeBundleCustodyRepository:
        raise PredictionCustodyPrecursorError("prediction_precursor_repository_invalid")
    prediction = _checked_prediction(prediction_payload, pins)
    proof = prediction_proof_envelope_sha256_v1(pins)
    try:
        await repository.verify_deployment()
        readback = await repository.read_retained_bundle_by_pins(
            expected_sha256=pins.prediction_sha256,
            proof_envelope_sha256=proof,
        )
    except RuntimeBundleCustodyError:
        raise PredictionCustodyPrecursorError(
            "prediction_precursor_custody_unavailable"
        ) from None
    return _observation(
        readback,
        payload=prediction_payload,
        pins=pins,
        prediction=prediction,
        mode="recovered_read_only",
    )
