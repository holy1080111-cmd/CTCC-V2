"""Pure publication and acknowledgement records for label-free Validation.

The observations are supplied by callers. This module neither authenticates
their roles/clocks nor reserves a database slot or releases outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.database.repositories.public_runtime_bundle_custody import runtime_bundle_id_v1
from app.domain.source_primitives import canonical, sha
from app.mie.validation.artifact import _verify
from app.mie.validation.contracts import (
    DatasetPartition,
    Gate3Contract,
    Sha256,
    require_utc,
)
from app.mie.validation.prospective_devval_global_companion_schedule_v1 import (
    _MAX_SCHEDULE_BYTES,
    GlobalDevvalCompanionScheduleV1,
)
from app.mie.validation.prospective_devval_prediction_db0046_precursor_v1 import (
    PredictionCustodyObservationV1,
    PredictionCustodyPinsV1,
    prediction_proof_envelope_sha256_v1,
)
from app.mie.validation.prospective_devval_validation_prediction_v1 import (
    verify_devval_validation_prediction_v1,
)
from app.mie.validation.replay import FrozenFeatureReplayPlanV2

_MAX_RECORD_BYTES = 4096
_ROLE_OID = Annotated[int, Field(gt=0, lt=2**32)]
_XID = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]


class PredictionPublicationError(ValueError):
    """Fixed refusal without source data or role details."""


@dataclass(frozen=True, slots=True, repr=False)
class ValidationPredictionReplayInputsV1:
    projection_payload: bytes
    expected_projection_sha256: str
    raw_event_payload: bytes
    expected_raw_event_sha256: str
    development_fit_payload: bytes
    expected_development_fit_sha256: str
    expected_development_fit_input_sha256: str
    policy_payload: bytes
    expected_policy_sha256: str
    scorer_config_json: bytes
    feature_plan: FrozenFeatureReplayPlanV2


class ValidationPredictionPublicationV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.validation_prediction_publication.v1"] = (
        "ctcc.mie.validation_prediction_publication.v1"
    )
    semantic_slot_sha256: Sha256
    schedule_sha256: Sha256
    slot_ordinal: int = Field(ge=0)
    prediction_sha256: Sha256
    db0046_bundle_id: Annotated[str, Field(min_length=1, max_length=128)]
    db0046_proof_sha256: Sha256
    db0046_readback_at: datetime
    publisher_role_oid: _ROLE_OID
    publication_xid: _XID
    recorded_at: datetime
    observation_basis: Literal["declared_unverified"] = "declared_unverified"
    independent_custody_verified: Literal[False] = False
    pre_outcome_publication_verified: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def exact_slot_and_chronology(self) -> ValidationPredictionPublicationV1:
        if self.semantic_slot_sha256 != _slot_sha(
            self.schedule_sha256, self.slot_ordinal
        ) or require_utc(self.db0046_readback_at, "DB0046 readback") >= require_utc(
            self.recorded_at, "publication"
        ):
            raise ValueError("prediction publication slot or chronology differs")
        return self


class ValidationPredictionAcknowledgementV1(Gate3Contract):
    schema_version: Literal["ctcc.mie.validation_prediction_acknowledgement.v1"] = (
        "ctcc.mie.validation_prediction_acknowledgement.v1"
    )
    publication_sha256: Sha256
    semantic_slot_sha256: Sha256
    prediction_sha256: Sha256
    observer_role_oid: _ROLE_OID
    acknowledgement_xid: _XID
    acknowledged_at: datetime
    observation_basis: Literal["declared_unverified"] = "declared_unverified"
    independent_custody_verified: Literal[False] = False
    pre_outcome_publication_verified: Literal[False] = False
    evaluator_first_access_verified: Literal[False] = False
    execution_authority: Literal[False] = False


def _slot_sha(schedule_sha256, slot_ordinal):
    return sha(
        canonical(
            {
                "schema": "ctcc.mie.validation_prediction_slot.v1",
                "schedule_sha256": schedule_sha256,
                "slot_ordinal": slot_ordinal,
            }
        )
    )


def _read(payload, pin, kind, limit=_MAX_RECORD_BYTES):
    if type(payload) is not bytes or not 0 < len(payload) <= limit:
        raise PredictionPublicationError("validation_prediction_publication_size")
    return _verify(payload, expected_sha256=pin, contract_type=kind)


def _prediction_proof(prediction, prediction_sha256):
    identity = prediction.identity
    return prediction_proof_envelope_sha256_v1(
        PredictionCustodyPinsV1(
            prediction_sha256=prediction_sha256,
            schedule_sha256=identity.schedule_sha256,
            coverage_sha256=identity.coverage_sha256,
            batch_sha256=identity.batch_sha256,
            slot_ordinal=identity.slot_ordinal,
            feature_projection_sha256=prediction.feature_projection_sha256,
            raw_event_sha256=prediction.raw_event_sha256,
            policy_sha256=prediction.policy_sha256,
            development_fit_sha256=prediction.development_fit_sha256,
            development_fit_input_sha256=prediction.development_fit_input_sha256,
            feature_plan_sha256=prediction.feature_plan_sha256,
            scorer_config_sha256=prediction.scorer_config_sha256,
        )
    )


def verify_prediction_publication_v1(
    payload: bytes,
    *,
    expected_publication_sha256: str,
    schedule_payload: bytes,
    expected_schedule_sha256: str,
    prediction_payload: bytes,
    expected_prediction_sha256: str,
    inputs: ValidationPredictionReplayInputsV1,
    precursor: PredictionCustodyObservationV1,
) -> ValidationPredictionPublicationV1:
    """Recompute label-free prediction and compare the exact supplied publication."""
    try:
        if (
            type(inputs) is not ValidationPredictionReplayInputsV1
            or type(precursor) is not PredictionCustodyObservationV1
        ):
            raise ValueError("inputs")
        publication = _read(
            payload, expected_publication_sha256, ValidationPredictionPublicationV1
        )
        schedule = _read(
            schedule_payload,
            expected_schedule_sha256,
            GlobalDevvalCompanionScheduleV1,
            _MAX_SCHEDULE_BYTES,
        )
        prediction = verify_devval_validation_prediction_v1(
            prediction_payload,
            expected_prediction_sha256=expected_prediction_sha256,
            projection_payload=inputs.projection_payload,
            expected_projection_sha256=inputs.expected_projection_sha256,
            raw_event_payload=inputs.raw_event_payload,
            expected_raw_event_sha256=inputs.expected_raw_event_sha256,
            development_fit_payload=inputs.development_fit_payload,
            expected_development_fit_sha256=inputs.expected_development_fit_sha256,
            expected_development_fit_input_sha256=inputs.expected_development_fit_input_sha256,
            policy_payload=inputs.policy_payload,
            expected_policy_sha256=inputs.expected_policy_sha256,
            scorer_config_json=inputs.scorer_config_json,
            feature_plan=inputs.feature_plan,
        )
        identity = prediction.identity
        slot = schedule.slots[identity.slot_ordinal]
        proof = _prediction_proof(prediction, expected_prediction_sha256)
        bundle_id = runtime_bundle_id_v1(
            bundle_sha256=expected_prediction_sha256, proof_envelope_sha256=proof
        )
        if (
            identity.schedule_sha256 != expected_schedule_sha256
            or slot.partition is not DatasetPartition.VALIDATION
            or slot.event_closed_at != identity.event_closed_at
            or slot.decision_cutoff_at != identity.decision_cutoff_at
            or publication.schedule_sha256 != expected_schedule_sha256
            or publication.slot_ordinal != identity.slot_ordinal
            or publication.prediction_sha256 != expected_prediction_sha256
            or precursor.prediction_sha256 != expected_prediction_sha256
            or type(precursor.prediction) is not type(prediction)
            or precursor.prediction.canonical_json_bytes() != prediction_payload
            or publication.db0046_bundle_id != bundle_id
            or precursor.bundle_id != bundle_id
            or publication.db0046_proof_sha256 != proof
            or precursor.proof_envelope_sha256 != proof
            or publication.db0046_readback_at != precursor.database_readback_at
            or not (identity.decision_at or identity.decision_cutoff_at)
            <= require_utc(precursor.recorded_at, "DB0046 recorded")
            <= require_utc(precursor.database_readback_at, "DB0046 readback")
        ):
            raise ValueError("lineage")
        return publication
    except (IndexError, TypeError, ValueError):
        raise PredictionPublicationError(
            "validation_prediction_publication_refused"
        ) from None


def verify_prediction_acknowledgement_v1(
    payload: bytes,
    *,
    expected_acknowledgement_sha256: str,
    publication_payload: bytes,
    expected_publication_sha256: str,
) -> ValidationPredictionAcknowledgementV1:
    """Compare separately retained exact wire pins and declared role/transaction separation."""
    try:
        ack = _read(
            payload,
            expected_acknowledgement_sha256,
            ValidationPredictionAcknowledgementV1,
        )
        publication = _read(
            publication_payload,
            expected_publication_sha256,
            ValidationPredictionPublicationV1,
        )
        if (
            ack.publication_sha256 != expected_publication_sha256
            or ack.semantic_slot_sha256 != publication.semantic_slot_sha256
            or ack.prediction_sha256 != publication.prediction_sha256
            or ack.observer_role_oid == publication.publisher_role_oid
            or ack.acknowledgement_xid == publication.publication_xid
            or require_utc(ack.acknowledged_at, "acknowledgement")
            <= publication.recorded_at
        ):
            raise ValueError("binding")
        return ack
    except (TypeError, ValueError):
        raise PredictionPublicationError(
            "validation_prediction_acknowledgement_refused"
        ) from None
