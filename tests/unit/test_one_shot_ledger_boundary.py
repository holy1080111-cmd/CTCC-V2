"""Owned synthetic G12/recheck plus a read-only DB0017 event diagnostic."""

from dataclasses import replace

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import one_shot_ledger_boundary as bridge
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.event_observation import VERSION, LedgerEventObservation
from app.trade_qualification.market_bridge import public_market_snapshot
from app.trade_qualification.recheck import evaluate_recorded_recheck
from app.trade_qualification.recheck_models import freeze_recheck_origin
from app.trade_qualification.reservations import LedgerScope, reservation_id
from tests.unit.test_qualification_account_capture import UID
from tests.unit.test_qualification_one_shot import (
    CaptureHarness,
    arguments,
    supplements,
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


@pytest.mark.asyncio
async def test_recheck_with_changed_risk_is_rejected_before_event_read(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    original_capture = bridge.publish_capture_recheck
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(None, clock=args["clock"])
    reads = []

    async def forged_capture(**kwargs):
        capture = await original_capture(**kwargs)
        assert capture.recheck is not None
        public = capture.market_packet
        assert public is not None
        ticker = public.ws.ticker
        reference = WSReferenceObservation(
            report_id=public.report_id,
            instrument_id=public.instrument_id,
            bid=ticker.bid,
            ask=ticker.ask,
            source_time=ticker.source_time,
            received_at=ticker.received_at,
        )
        frozen = kwargs["original_inputs"]
        original_risk = frozen["risk_inputs"]
        changed_risk = PortfolioInputs(
            requested_contracts=original_risk.requested_contracts + 1,
            requested_leverage=original_risk.requested_leverage,
            instrument=None,
            account=None,
            authority=None,
        )
        forged = evaluate_recorded_recheck(
            kwargs["original_market"],
            public_market_snapshot(public, expected_bundle_sha256=public.bundle_sha256),
            origin=freeze_recheck_origin(capture.evidence),
            original_inputs=frozen,
            quote=public.quote,
            reference=reference,
            current_risk_inputs=changed_risk,
            consumed_event_keys=frozen["consumed_event_keys"],
            observed_at=capture.recheck.observed_at,
        )
        assert forged != capture.recheck
        assert forged.origin == capture.recheck.origin
        assert forged.quote_bundle_sha256 == capture.recheck.quote_bundle_sha256
        return replace(capture, recheck=forged)

    async def read_event(*_args):
        reads.append(True)
        raise AssertionError("mismatched R7 record reached DB read")

    monkeypatch.setattr(bridge, "publish_capture_recheck", forged_capture)
    monkeypatch.setattr(ledger, "read_event_observation", read_event)
    result = await bridge.publish_capture_inspect_event_ledger(
        ledger=ledger, scope=scope, **args
    )
    assert result.code == "capture_binding_invalid"
    assert result.admission == "DENY"
    assert not reads
    harness.assert_closed()


@pytest.mark.asyncio
async def test_caller_mutation_after_g12_cannot_change_replayed_original(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    synthetic_publisher(monkeypatch)
    source_inputs = args["original_inputs"]

    def mutate_caller():
        source_inputs["intent"] = None

    harness = CaptureHarness(
        monkeypatch, inputs[0], args["clock"], before_request=mutate_caller
    )
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(None, clock=args["clock"])
    reads = []

    async def read_event(read_scope, event_key):
        reads.append((read_scope, event_key))
        return _observation(scope, event_key, args["clock"])

    monkeypatch.setattr(ledger, "read_event_observation", read_event)
    result = await bridge.publish_capture_inspect_event_ledger(
        ledger=ledger, scope=scope, **args
    )
    assert source_inputs["intent"] is None
    assert result.code == "trusted_execution_inputs_missing"
    assert result.admission == "DENY"
    assert len(reads) == 1
    harness.assert_closed()


@pytest.mark.asyncio
async def test_pinned_account_mapping_is_replayed_without_authority(
    inputs, tmp_path, monkeypatch
):
    args = arguments(inputs, tmp_path)
    supplied = supplements(inputs)
    args["materialization_inputs"] = supplied
    args["expected_materialization_inputs_sha256"] = (
        bridge.account_materializer.materialization_inputs_sha256(supplied)
    )
    synthetic_publisher(monkeypatch)
    harness = CaptureHarness(monkeypatch, inputs[0], args["clock"])
    scope = LedgerScope(account_id=UID, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(None, clock=args["clock"])
    reads = []

    async def read_event(read_scope, event_key):
        reads.append((read_scope, event_key))
        return _observation(scope, event_key, args["clock"])

    monkeypatch.setattr(ledger, "read_event_observation", read_event)
    result = await bridge.publish_capture_inspect_event_ledger(
        ledger=ledger, scope=scope, **args
    )
    assert result.code == "trusted_execution_inputs_missing"
    assert result.admission == "DENY"
    assert not result.account_complete
    assert not result.execution_authority
    assert len(reads) == 1
    harness.assert_closed()
