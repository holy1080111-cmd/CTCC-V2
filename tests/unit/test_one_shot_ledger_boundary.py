"""Owned synthetic G12/recheck plus a read-only DB0017 event diagnostic."""

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import one_shot_ledger_boundary as bridge
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.reservations import LedgerScope, reservation_id
from tests.unit.test_qualification_account_capture import UID
from tests.unit.test_qualification_one_shot import (
    CaptureHarness,
    arguments,
    synthetic_publisher,
)
from tests.unit.test_qualification_one_shot import (
    inputs as one_shot_inputs,
)

inputs = one_shot_inputs


def _observation(scope, event_key, clock):
    return LedgerEventObservation(
        contract_version=VERSION,
        scope=scope,
        original_event_key=event_key,
        account_revision=0,
        ledger_revision=0,
        matched=None,
        request_started_at=clock(),
        observed_at=clock(),
        received_at=clock(),
        monotonic_started_ns=1,
        monotonic_received_ns=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ("absent", "wrong_event", "unavailable"))
async def test_owned_recheck_binds_only_read_only_event_observation(
    inputs, tmp_path, monkeypatch, case
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(None, clock=args["clock"])
    reads = []

    async def read_event(read_scope, event_key):
        reads.append((read_scope, event_key))
        if case == "unavailable":
            raise RuntimeError("private SQL detail must not escape")
        observed_key = "f" * 64 if case == "wrong_event" else event_key
        return _observation(scope, observed_key, args["clock"])

    async def forbidden_write(*_args, **_kwargs):
        raise AssertionError("read-only R7 boundary attempted a ledger write")

    monkeypatch.setattr(ledger, "read_event_observation", read_event)
    monkeypatch.setattr(ledger, "reserve", forbidden_write)
    monkeypatch.setattr(ledger, "consume_with_submission_intent", forbidden_write)
    result = await bridge.publish_capture_inspect_event_ledger(
        ledger=ledger, scope=scope, **args
    )
    assert len(reads) == 1
    assert reads[0][0] == scope
    assert result.original_event_key == reads[0][1]
    assert result.exact_reservation_id == reservation_id(scope, reads[0][1])
    assert result.evidence_sha256 and result.recheck_sha256
    assert result.account_packet_sha256
    assert (
        result.code
        == {
            "absent": "trusted_execution_inputs_missing",
            "wrong_event": "ledger_observation_invalid",
            "unavailable": "ledger_observation_unavailable",
        }[case]
    )
    assert (result.ledger_event_state == "absent_at_read") is (case == "absent")
    assert (result.ledger_observation_sha256 is not None) is (case == "absent")
    assert result.admission == "DENY"
    assert not any(
        (
            result.source_authenticity_verified,
            result.account_complete,
            result.intrabar_path_verified,
            result.atomic_risk_reserved,
            result.durable_intent_created,
            result.execution_authority,
            result.order_submitted,
        )
    )
    assert all(request.method == "GET" for request in harness.requests)
    assert "private SQL detail" not in repr(result)
    assert UID not in repr(result)
    harness.assert_closed()


@pytest.mark.asyncio
async def test_wrong_uid_rejected_before_g12_or_account_request(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    scope = LedgerScope(account_id="999", settlement_currency="USDT")
    ledger = QualificationLedgerRepository(None, clock=args["clock"])
    with pytest.raises(ValueError, match="one_shot_ledger_scope_mismatch"):
        await bridge.publish_capture_inspect_event_ledger(
            ledger=ledger, scope=scope, **args
        )
    assert list(tmp_path.iterdir()) == []


def test_diagnostic_cannot_claim_reservation_or_intent():
    with pytest.raises(ValueError, match="one_shot_ledger_cannot_grant_execution"):
        bridge.OneShotLedgerBoundaryDiagnostic(
            code="capture_incomplete",
            report_id="synthetic",
            scope_sha256="0" * 64,
            durable_intent_created=True,
        )
