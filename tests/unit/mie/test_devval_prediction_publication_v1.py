"""Synthetic chronology and semantic binding; no outcome data or database access."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.source_primitives import sha
from app.mie.validation import prospective_devval_prediction_publication_v1 as subject
from app.mie.validation.prospective_devval_prediction_db0046_precursor_v1 import (
    PredictionCustodyObservationV1,
)
from app.mie.validation.prospective_devval_validation_prediction_v1 import (
    predict_devval_validation_event_v1,
)
from tests.unit.mie.test_devval_validation_prediction_v1 import (
    cohort as cohort,  # noqa: PLC0414 -- shared label-free fixtures.
)
from tests.unit.mie.test_devval_validation_prediction_v1 import (
    policy_input as policy_input,  # noqa: PLC0414 -- shared label-free fixtures.
)
from tests.unit.mie.test_devval_validation_prediction_v1 import (
    prediction_input as prediction_input,  # noqa: PLC0414 -- shared label-free fixtures.
)
from tests.unit.mie.test_devval_validation_prediction_v1 import (
    study_input as study_input,  # noqa: PLC0414 -- shared label-free fixtures.
)
from tests.unit.mie.test_devval_validation_prediction_v1 import (
    validation_case as validation_case,  # noqa: PLC0414 -- shared label-free fixtures.
)


@pytest.fixture
def records(prediction_input, cohort):
    args = prediction_input[0]
    artifact = predict_devval_validation_event_v1(**args)
    prediction = artifact.contract
    schedule = cohort[0]
    identity = prediction.identity
    proof = subject._prediction_proof(prediction, artifact.sha256)
    bundle = subject.runtime_bundle_id_v1(
        bundle_sha256=artifact.sha256, proof_envelope_sha256=proof
    )
    at = identity.decision_cutoff_at + timedelta(seconds=1)
    observation = PredictionCustodyObservationV1(
        mode="recovered_read_only",
        bundle_id=bundle,
        prediction_sha256=artifact.sha256,
        proof_envelope_sha256=proof,
        recorded_at=at,
        database_readback_at=at + timedelta(seconds=1),
        append_transaction_id=None,
        readback_transaction_id="10",
        prediction=prediction,
    )
    publication = subject.ValidationPredictionPublicationV1(
        semantic_slot_sha256=subject._slot_sha(schedule.sha256, identity.slot_ordinal),
        schedule_sha256=schedule.sha256,
        slot_ordinal=identity.slot_ordinal,
        prediction_sha256=artifact.sha256,
        db0046_bundle_id=bundle,
        db0046_proof_sha256=proof,
        db0046_readback_at=observation.database_readback_at,
        publisher_role_oid=100,
        publication_xid="12",
        recorded_at=at + timedelta(seconds=2),
    )
    ack = subject.ValidationPredictionAcknowledgementV1(
        publication_sha256=publication.canonical_sha256(),
        semantic_slot_sha256=publication.semantic_slot_sha256,
        prediction_sha256=artifact.sha256,
        observer_role_oid=101,
        acknowledgement_xid="13",
        acknowledged_at=at + timedelta(seconds=3),
    )
    return (
        publication,
        ack,
        {
            "schedule_payload": schedule.payload,
            "expected_schedule_sha256": schedule.sha256,
            "prediction_payload": artifact.payload,
            "expected_prediction_sha256": artifact.sha256,
            "inputs": subject.ValidationPredictionReplayInputsV1(**args),
            "precursor": observation,
        },
    )


def test_exact_publication_replay_and_declared_separate_ack(records):
    publication, ack, arguments = records
    actual = subject.verify_prediction_publication_v1(
        publication.canonical_json_bytes(),
        expected_publication_sha256=publication.canonical_sha256(),
        **arguments,
    )
    assert actual == publication
    assert not actual.independent_custody_verified
    actual_ack = subject.verify_prediction_acknowledgement_v1(
        ack.canonical_json_bytes(),
        expected_acknowledgement_sha256=ack.canonical_sha256(),
        publication_payload=publication.canonical_json_bytes(),
        expected_publication_sha256=publication.canonical_sha256(),
    )
    assert actual_ack == ack
    assert actual_ack.observation_basis == "declared_unverified"
    assert (
        not actual_ack.independent_custody_verified
        and not actual_ack.evaluator_first_access_verified
    )


@pytest.mark.parametrize(
    "fault",
    [
        "slot",
        "prediction",
        "predecision",
        "proof",
        "publish-before-readback",
        "same-clock",
    ],
)
def test_refuses_rehashed_publication_with_wrong_lineage_or_chronology(records, fault):
    publication, _, arguments = records
    if fault == "slot":
        publication = publication.model_copy(
            update={"slot_ordinal": publication.slot_ordinal + 1}
        )
    elif fault == "prediction":
        arguments["inputs"] = replace(
            arguments["inputs"], expected_policy_sha256="f" * 64
        )
    elif fault == "predecision":
        observation = arguments["precursor"]
        arguments["precursor"] = replace(
            observation,
            recorded_at=observation.prediction.identity.event_closed_at
            - timedelta(seconds=1),
        )
    elif fault == "proof":
        publication = publication.model_copy(update={"db0046_proof_sha256": "f" * 64})
        arguments["precursor"] = replace(
            arguments["precursor"], proof_envelope_sha256="f" * 64
        )
    elif fault == "same-clock":
        publication = publication.model_copy(
            update={"recorded_at": publication.db0046_readback_at}
        )
    else:
        publication = publication.model_copy(
            update={
                "recorded_at": publication.db0046_readback_at - timedelta(seconds=1)
            }
        )
    raw = publication.canonical_json_bytes()
    with pytest.raises(subject.PredictionPublicationError, match="publication_refused"):
        subject.verify_prediction_publication_v1(
            raw, expected_publication_sha256=sha(raw), **arguments
        )


@pytest.mark.parametrize(
    "fault", ["same-role", "same-xid", "early", "wrong-publication", "outcome-field"]
)
def test_refuses_rehashed_ack_without_exact_separation_and_no_outcomes(records, fault):
    publication, ack, _ = records
    raw = ack.model_copy(
        update={
            "same-role": {"observer_role_oid": publication.publisher_role_oid},
            "same-xid": {"acknowledgement_xid": publication.publication_xid},
            "early": {"acknowledged_at": publication.recorded_at},
            "wrong-publication": {"publication_sha256": "f" * 64},
            "outcome-field": {},
        }[fault]
    ).canonical_json_bytes()
    if fault == "outcome-field":
        raw = raw[:-1] + b',"outcome":true}'
    with pytest.raises(
        subject.PredictionPublicationError, match="acknowledgement_refused"
    ):
        subject.verify_prediction_acknowledgement_v1(
            raw,
            expected_acknowledgement_sha256=sha(raw),
            publication_payload=publication.canonical_json_bytes(),
            expected_publication_sha256=publication.canonical_sha256(),
        )
