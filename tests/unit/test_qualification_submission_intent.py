"""Synthetic upstream claims only; no IO, executable permit or real order."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from app.trade_qualification.reservations import (
    QualificationLedgerError,
    ReservationReceipt,
    digest,
    prepare_reservation,
    reservation_id,
)
from app.trade_qualification.submission_intent import (
    MAX_INTENT_BYTES,
    build_submission_intent,
    replay_submission_intent,
)
from tests.unit.qualification_ledger_fixtures import ledger_fixture


@pytest.fixture(scope="module", params=("long", "short"))
def pair(request):
    fixture = ledger_fixture(request.param)
    original = fixture.request
    coverage, _ = prepare_reservation(
        original, fixture.claims, (), observed_at=fixture.now
    )
    receipt = ReservationReceipt(
        scope=original.scope,
        reservation_id=reservation_id(
            original.scope, original.origin.original_event_key
        ),
        original_event_key=original.origin.original_event_key,
        report_id=original.origin.candidate.report_id,
        instrument_id=original.origin.evidence.pre_evidence.prefix.intent.instrument_id,
        direction=original.origin.candidate.direction,
        correlation_group=original.risk_inputs.instrument.correlation_group,
        request_sha256=digest(original),
        coverage=coverage,
        state="consumed",
        state_revision=2,
        account_revision=1,
        ledger_revision=3,
        created_at=fixture.now,
        updated_at=fixture.now,
        deadline=original.origin.deadline,
    )
    return original, receipt


def test_round_trip_deterministic_and_original_facts_only(pair):
    request, receipt = pair
    result = build_submission_intent(request, receipt)
    again, consumed = replay_submission_intent(
        result.canonical_json, request, expected_sha256=result.sha256
    )
    assert again == result and consumed == receipt
    assert result == build_submission_intent(request, receipt)
    body = json.loads(result.canonical_json)
    assert body["candidate_entry"] == str(request.origin.candidate.candidate_entry)
    assert body["stop_loss"] == str(request.origin.candidate.stop_loss)
    assert body["take_profit"] == str(request.origin.candidate.take_profit)
    assert body["contracts"] == str(request.risk_inputs.requested_contracts)
    assert body["leverage"] == request.risk_inputs.requested_leverage
    assert len(body["client_order_id"]) == 32
    assert body["client_order_id"] != body["protection_client_order_id"]
    assert body["consumed_receipt"]["original_event_key"] == receipt.original_event_key
    for key in (
        "execution_authority",
        "order_retry_authority",
        "order_submitted",
        "all_fill_prices_covered",
    ):
        assert body[key] is False
    assert "consumed_receipt" not in repr(result)
    assert result.execution_authority is False and result.order_retry_authority is False
    with pytest.raises(FrozenInstanceError):
        result.sha256 = "f" * 64


@pytest.mark.parametrize(
    "change",
    [
        {"state": "reserved"},
        {"state": "uncertain"},
        {"state": "reconciled_flat"},
        {"state_revision": 3},
        {"request_sha256": "f" * 64},
        {"original_event_key": "f" * 64},
        {"reservation_id": "f" * 64},
        {"report_id": "other"},
        {"instrument_id": "ETH-USDT-SWAP"},
        {"instrument_id": "BTC/USDT:USDT"},
        {"correlation_group": "other"},
        {"ledger_revision": 1},
    ],
)
def test_changed_reservation_cannot_prepare_intent(pair, change):
    request, receipt = pair
    with pytest.raises(ValueError):
        build_submission_intent(request, receipt.model_copy(update=change))


@pytest.mark.parametrize("when", ("publication", "expiry"))
def test_consume_time_outside_original_window_rejected(pair, when):
    request, receipt = pair
    stamp = (
        request.origin.publication_completed_at
        if when == "publication"
        else request.origin.deadline
    )
    with pytest.raises(ValueError):
        build_submission_intent(
            request, receipt.model_copy(update={"updated_at": stamp})
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "future"),
        ("client_order_id", "CTCCRETRY"),
        ("protection_client_order_id", "CTCCRETRY"),
        ("candidate_entry", "1"),
        ("stop_loss", "1"),
        ("take_profit", "1"),
        ("contracts", "1"),
        ("leverage", 125),
        ("order_submitted", True),
        ("order_retry_authority", True),
        ("execution_authority", True),
        ("execution_authority", 0),
        ("all_fill_prices_covered", True),
        ("extra", "ignored"),
    ],
)
def test_self_rehashed_modified_intent_rejected(pair, field, value):
    request, receipt = pair
    body = json.loads(build_submission_intent(request, receipt).canonical_json)
    body[field] = value
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    with pytest.raises(QualificationLedgerError, match="record_invalid"):
        replay_submission_intent(
            raw, request, expected_sha256=hashlib.sha256(raw.encode()).hexdigest()
        )


@pytest.mark.parametrize(
    "raw",
    (None, b"{}", "", "[", "[]", "null", "{}", " " * (MAX_INTENT_BYTES + 1)),
    ids=("none", "bytes", "empty", "malformed", "list", "null", "missing", "oversize"),
)
def test_invalid_raw_rejected(pair, raw):
    request, _ = pair
    pin = hashlib.sha256(raw.encode()).hexdigest() if type(raw) is str else "f" * 64
    with pytest.raises(QualificationLedgerError):
        replay_submission_intent(raw, request, expected_sha256=pin)


def test_wrong_external_pin_rejected(pair):
    request, receipt = pair
    record = build_submission_intent(request, receipt)
    with pytest.raises(QualificationLedgerError, match="integrity_mismatch"):
        replay_submission_intent(
            record.canonical_json, request, expected_sha256="f" * 64
        )


def test_noncanonical_and_duplicate_fields_rejected(pair):
    request, receipt = pair
    record = build_submission_intent(request, receipt)
    for raw in (
        record.canonical_json + "\n",
        '{"version":"ignored",' + record.canonical_json[1:],
    ):
        with pytest.raises(QualificationLedgerError):
            replay_submission_intent(
                raw, request, expected_sha256=hashlib.sha256(raw.encode()).hexdigest()
            )


def test_different_scope_and_future_deadline_do_not_rebind(pair):
    request, receipt = pair
    for updates in (
        {"scope": receipt.scope.model_copy(update={"account_id": "987654321"})},
        {"deadline": receipt.deadline + timedelta(seconds=1)},
    ):
        with pytest.raises(ValueError):
            build_submission_intent(request, receipt.model_copy(update=updates))


@pytest.mark.parametrize("entry", ("build", "replay"))
def test_recomputed_execution_contract_value_mismatch_rejected(pair, entry):
    from decimal import Decimal

    from app.trade_qualification.reservations import RiskCoverage, exact_coverage

    request, receipt = pair
    candidate = receipt.coverage.candidate
    execution = receipt.coverage.execution.model_copy(
        update={"contract_value": candidate.contract_value * Decimal(2)}
    )
    # Recompute every aggregate, so the new relationship guard is what rejects
    # this record, not a stale coverage sum or an invalid Decimal.
    coverage = RiskCoverage(
        candidate=candidate,
        execution=execution,
        **exact_coverage(candidate, execution),
    )
    changed = receipt.model_copy(update={"coverage": coverage})
    if entry == "build":
        with pytest.raises(
            QualificationLedgerError, match="^submit_intent_geometry_mismatch$"
        ):
            build_submission_intent(request, changed)
    else:
        body = json.loads(build_submission_intent(request, receipt).canonical_json)
        body["consumed_receipt"] = changed.model_dump(mode="json", round_trip=True)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with pytest.raises(
            QualificationLedgerError, match="^submit_intent_record_invalid$"
        ):
            replay_submission_intent(
                raw, request, expected_sha256=hashlib.sha256(raw.encode()).hexdigest()
            )


@pytest.mark.parametrize("codepoint", (0xD800, 0xDFFF, 0xDBFF))
def test_literal_surrogate_json_is_redacted_integrity_failure(pair, codepoint):
    request, _ = pair
    raw = '{"private":"' + chr(codepoint) + '"}'
    with pytest.raises(
        QualificationLedgerError, match="^submit_intent_integrity_mismatch$"
    ):
        replay_submission_intent(raw, request, expected_sha256="f" * 64)


@pytest.mark.parametrize("field", ("candidate_entry", "report_id"))
def test_canonical_escaped_surrogate_cannot_be_self_rehashed_into_intent(pair, field):
    request, receipt = pair
    body = json.loads(build_submission_intent(request, receipt).canonical_json)
    if field == "report_id":
        body["consumed_receipt"][field] = "\ud800"
    else:
        body[field] = "\ud800"
    raw = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    with pytest.raises(
        QualificationLedgerError, match="^submit_intent_record_invalid$"
    ):
        replay_submission_intent(
            raw, request, expected_sha256=hashlib.sha256(raw.encode()).hexdigest()
        )


@pytest.mark.parametrize("owner", ("request", "receipt"))
def test_nested_opaque_scope_account_id_rejected_without_callbacks(pair, owner):
    calls = []

    class OpaqueAccount:
        @property
        def __class__(self):
            calls.append("class")
            raise ValueError("synthetic opaque class callback")

        def __str__(self):
            calls.append("str")
            raise ValueError("synthetic opaque string callback")

        def __eq__(self, other):
            calls.append("eq")
            raise ValueError("synthetic opaque equality callback")

    request, receipt = pair
    selected = request if owner == "request" else receipt
    changed = selected.model_copy(
        update={
            "scope": selected.scope.model_copy(update={"account_id": OpaqueAccount()})
        }
    )
    with pytest.raises(ValueError):
        build_submission_intent(
            changed if owner == "request" else request,
            changed if owner == "receipt" else receipt,
        )
    assert calls == []


@pytest.mark.parametrize("field", ("created_at", "updated_at", "deadline"))
def test_nested_opaque_receipt_timezone_rejected_without_callbacks(pair, field):
    from datetime import tzinfo

    calls = []

    class OpaqueZone(tzinfo):
        def utcoffset(self, value):
            calls.append("utcoffset")
            raise ValueError("synthetic opaque timezone callback")

        def dst(self, value):
            calls.append("dst")
            raise ValueError("synthetic opaque timezone callback")

    request, receipt = pair
    stamp = getattr(receipt, field).replace(tzinfo=OpaqueZone())
    assert calls == []
    changed = receipt.model_copy(update={field: stamp})
    with pytest.raises(ValueError):
        build_submission_intent(request, changed)
    assert calls == []
