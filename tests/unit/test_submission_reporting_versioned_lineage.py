"""Synthetic legacy V2 lineage through DB0018; no exchange IO."""

import json
from datetime import timedelta

import pytest

from app.database.repositories.submission_reporting import outcome_document
from app.trade_evidence.submission_reporting import (
    CapturedSubmission,
    SubmissionReportingError,
    prepare_submission,
)
from app.trade_qualification import reservations
from app.trade_qualification.submission_intent import build_submission_intent
from tests.unit.qualification_range_v5_fixtures import range_v5_ledger_fixture


def test_v2_request_replays_full_submission_report(monkeypatch):
    fixture, binding, _ = range_v5_ledger_fixture("long", monkeypatch)
    v3 = fixture.request
    replay = reservations.ReservationReplayBindingV2(
        contract_version="ctcc-reservation-replay-v2",
        **{
            name: getattr(v3.replay_binding, name)
            for name in reservations.ReservationReplayBindingV2.model_fields
            if name not in reservations.LedgerModel.model_fields
            and name != "contract_version"
        },
    )
    request = reservations.ReservationRequestV2(
        **{
            name: getattr(v3, name)
            for name in reservations.ReservationRequest.model_fields
            if name not in reservations.LedgerModel.model_fields
        },
        contract_version="ctcc-reservation-request-v2",
        replay_binding=replay,
    )
    request = reservations.decode_reservation_request(reservations.canonical(request))
    coverage, result = reservations.prepare_reservation(
        request, fixture.claims, (), observed_at=fixture.now
    )
    assert result.passed
    consumed = reservations.ReservationReceipt(
        scope=request.scope,
        reservation_id=reservations.reservation_id(
            request.scope, request.origin.original_event_key
        ),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=reservations.digest(request),
        coverage=coverage,
        state="consumed",
        state_revision=2,
        account_revision=1,
        ledger_revision=3,
        created_at=fixture.now,
        updated_at=fixture.now,
        deadline=request.origin.deadline,
    )
    intent = build_submission_intent(request, consumed, execution_binding=binding)
    body = json.loads(intent.canonical_json)
    response = {
        "code": "0",
        "data": [
            {
                "sCode": "0",
                "ordId": "99887766",
                "clOrdId": body["client_order_id"],
            }
        ],
    }
    completed = fixture.now + timedelta(milliseconds=1)
    capture = CapturedSubmission(
        exchange_request_sha256=body["exchange_request_sha256"],
        request_started_at=fixture.now,
        headers_received_at=fixture.now,
        body_completed_at=completed,
        completed_at=completed,
        http_status=200,
        raw_body=json.dumps(response).encode(),
        transport_complete=True,
    )
    prepared = prepare_submission(
        request, intent.canonical_json, intent_sha256=intent.sha256, capture=capture
    )
    assert prepared.status == "acknowledged" and prepared.consumed == consumed
    assert (
        len(
            outcome_document(
                prepared,
                transition_id=1,
                sequence=1,
                previous=None,
                revision=4,
                recorded_at=completed,
            ).encode()
        )
        <= 16384
    )
    with pytest.raises(SubmissionReportingError, match="lineage_or_capture_invalid"):
        prepare_submission(
            request,
            intent.canonical_json,
            intent_sha256=intent.sha256,
            capture=capture.model_copy(update={"exchange_request_sha256": "f" * 64}),
        )
