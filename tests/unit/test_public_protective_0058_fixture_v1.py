"""Predeclared synthetic fixture consistency; no PostgreSQL/venue/capability."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.database.models.account_issued_revision import (
    DemoCollectorIssuedAccountRevision as Issued,
)
from app.database.repositories import native_postsubmit_v1 as journal
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import control_bound_ledger as bound
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_ledger_contracts_v2 as wire
from app.trade_qualification import native_protective_exit_facts_v1 as facts
from app.trade_qualification import reservations as ledger
from tests.integration.native_public_protective_0058_fixture_v1 import (
    PublicJournal0058,
    _fresh_terminal,
    _sessions,
    request_for_issued,
)
from tests.integration.test_native_original_oco_public_0058_pg import (
    _template as _template,  # noqa: PLC0414 -- explicit synthetic fixture registration.
)
from tests.unit.test_native_ledger_contracts_v2 import native_binding
from tests.unit.test_native_protective_exit_facts_v1 import protective_terminal


@pytest.mark.parametrize(
    "_template",
    [
        ("long", "net_mode"),
        ("short", "net_mode"),
        ("long", "long_short_mode"),
        ("short", "long_short_mode"),
    ],
    indirect=True,
)
def test_declared_new_fixture_binds_changed_issuer_scope_time_and_fixed_geometry(
    _template,
):
    now = datetime.now(UTC)
    scope = ledger.LedgerScope(account_id="700001", settlement_currency="USDT")
    proof = canonical(
        {"component": "SYNTHETIC_ISSUER_DATA_ONLY", "execution_authority": False}
    ).decode()
    claims = canonical(
        {"scope": ["demo", "700001", "USDT"], "account_complete": False}
    ).decode()
    row = Issued(
        issuer_sha256=sha(proof.encode()),
        environment="demo",
        account_id=scope.account_id,
        settlement_currency="USDT",
        previous_account_revision=0,
        previous_ledger_revision=0,
        previous_claims_sha256=None,
        account_revision=1,
        ledger_revision=1,
        claims_json=claims,
        claims_sha256=sha(claims.encode()),
        control_revision=2,
        control_event_sha256="2" * 64,
        control_state_sha256="3" * 64,
        source_lineage_sha256="4" * 64,
        hwm_custody_id="5" * 32,
        hwm_bundle_sha256="6" * 64,
        proof_json=proof,
        original_expires_at=now + timedelta(seconds=20),
        db_recorded_at=now - timedelta(milliseconds=1),
    )
    credential = sha(b"synthetic-new-public58-session")
    request = request_for_issued(
        _template,
        scope=scope,
        issued_row=row,
        credential=credential,
        deadline=now + timedelta(seconds=3),
        checked_at=now,
    )
    assert wire.checked_request(request) == request
    assert (
        request.origin.original_event_key != _template.request.origin.original_event_key
    )
    assert (
        request.origin.candidate.candidate_entry
        == _template.request.origin.candidate.candidate_entry
    )
    assert (
        request.origin.candidate.stop_loss
        == _template.request.origin.candidate.stop_loss
    )
    assert (
        request.origin.candidate.take_profit
        == _template.request.origin.candidate.take_profit
    )
    state = control.acquire(
        None,
        scope=control.ControlScope("demo", scope.account_id),
        owner_sha256="7" * 64,
        pins=control.ControlPins(
            "8" * 64, request.origin.original_policy_sha256, credential
        ),
        now=now - timedelta(seconds=1),
    )
    state = control.advance(
        state,
        owner_sha256=state.owner_sha256,
        owner_epoch=1,
        expected_revision=1,
        action="arm_requested",
        command_id="9" * 64,
        now=now - timedelta(milliseconds=1),
        arm_expires_at=now + timedelta(seconds=20),
    )
    reserved = ledger.ReservationReceipt(
        scope=scope,
        reservation_id=ledger.reservation_id(scope, request.origin.original_event_key),
        original_event_key=request.origin.original_event_key,
        report_id=request.origin.candidate.report_id,
        instrument_id=request.risk_inputs.instrument.instrument_id,
        direction=request.origin.candidate.direction,
        correlation_group=request.risk_inputs.instrument.correlation_group,
        request_sha256=ledger.digest(request),
        coverage=request.coverage,
        state="reserved",
        state_revision=1,
        account_revision=1,
        ledger_revision=2,
        created_at=now,
        updated_at=now,
        deadline=request.origin.deadline,
    )
    consumed = reserved.model_copy(
        update={
            "state": "consumed",
            "state_revision": 2,
            "ledger_revision": 3,
            "updated_at": now + timedelta(microseconds=1),
        }
    )
    prior = bound.build_reserved_evidence(
        request, reserved, state, "a" * 64, checked_at=now
    )
    inner = wire.build_native_intent(
        request, consumed, execution_binding=native_binding(request)
    )
    outer = bound.build_control_bound_intent(request, inner, prior, state, "a" * 64)
    target = facts.facts.NativeReconciliationTargetV1(
        request,
        wire.canonical_request(request),
        outer.canonical_json,
        outer.sha256,
        reserved.reservation_id,
        "700000",
        "https://www.okx.com",
        credential,
        "b" * 64,
    )
    original, terminal = protective_terminal(target)
    original = _sessions(original, "public58-original")
    hold_at = original[-1][1].eof_at + timedelta(seconds=1)
    terminal = _sessions(
        _fresh_terminal(terminal, after=hold_at, alter_exit=True), "public58-terminal"
    )
    assert terminal[0][1].started_at > hold_at
    actual = facts.assess_original_protective_exit_v1(target, original, terminal)
    replay = _sessions(
        _fresh_terminal(
            terminal,
            after=terminal[-1][1].eof_at + timedelta(seconds=1),
            alter_exit=False,
        ),
        "public58-postflat",
    )
    assert (
        facts.assess_original_protective_exit_v1(target, original, replay).receipt_json
        == actual.receipt_json
    )
    assert actual.execution_authority is actual.release_authority is False

    async def reopen_serialized_fixture():
        # Pure serializer test only: this fake sink cannot create SQL lineage,
        # a reservation, source authority, an Arm or an exchange submission.
        rows = []
        publisher = PublicJournal0058(None, target, outer, 2)

        async def retain(_session, capture, **_unused):
            raw = canonical(capture).decode()
            identity = sha(raw.encode())
            rows.append(
                SimpleNamespace(
                    outcome_id=identity,
                    capture_json=raw,
                    observation_kind="reconciliation",
                    status="uncertain",
                    sequence=len(rows) + 1,
                )
            )
            return identity, now

        publisher.append = retain
        manifest, _ = await publisher.manifest(
            None, original, actual, label="pure-original"
        )
        _, reopened = journal._read_manifest(tuple(rows), manifest)
        assert reopened == original

    asyncio.run(reopen_serialized_fixture())
