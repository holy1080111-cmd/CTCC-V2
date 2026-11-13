"""Verify the DB0056 writer's time, owner, revision and transaction boundaries."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.database.repositories.demo_control import ControlObservation
from app.domain.source_primitives import canonical, sha
from app.trade_qualification import demo_control as control
from app.trade_qualification import native_prepare_owner_0056_v1 as subject
from app.trade_qualification.control_bound_ledger import ControlExpectation
from app.trade_qualification.reservations import LedgerScope

_AT = datetime(2026, 11, 14, tzinfo=UTC)
_SCOPE = LedgerScope(account_id="700006", settlement_currency="USDT")
_CONTROL = control.ControlScope(_SCOPE.environment, _SCOPE.account_id)
_TOKEN = b"Q" * 32
_PINS = control.ControlPins("a" * 64, "b" * 64, "c" * 64)
_EVENT = "d" * 64


@pytest.fixture
def ready(monkeypatch):
    first = control.acquire(
        None,
        scope=_CONTROL,
        owner_sha256=control.owner_binding(_TOKEN),
        pins=_PINS,
        now=_AT,
    )
    expires_at = _AT + timedelta(seconds=20)
    state = control.advance(
        first,
        owner_sha256=first.owner_sha256,
        owner_epoch=first.owner_epoch,
        expected_revision=first.revision,
        action="arm_requested",
        command_id="f" * 64,
        now=_AT,
        arm_expires_at=expires_at,
    )
    expected = subject.QualificationWriterPins0056V1(
        ControlExpectation(_CONTROL, _TOKEN, 1, 2, _EVENT, _PINS),
        0,
        0,
        None,
        expires_at,
    )
    sync_engine = object()
    engine = SimpleNamespace(sync_engine=sync_engine)
    session = SimpleNamespace(
        sync_session=SimpleNamespace(get_transaction=Mock(return_value=object())),
        get_bind=Mock(return_value=sync_engine),
        execute=AsyncMock(),
        refresh=AsyncMock(),
        get=AsyncMock(),
    )
    row = subject.QualificationAccountScope(
        environment="demo",
        account_id=_SCOPE.account_id,
        settlement_currency="USDT",
        account_revision=0,
        ledger_revision=0,
        created_at=_AT,
        updated_at=_AT,
    )
    observation = SimpleNamespace(
        control=ControlObservation(state, _EVENT, _AT),
        writer_catalog=object(),
    )
    facts = subject.QualificationWriterTransaction0056V1(
        11, 212, "343", _AT + timedelta(seconds=1)
    )
    roles = object.__new__(subject.RestrictedNativeRoles0056WitnessV1)
    case = SimpleNamespace(
        session=session,
        selected=object(),
        roles=roles,
        match=state,
        expected=expected,
        facts=facts,
        row=row,
        observation=observation,
        active=False,
    )

    def check():
        subject._need(case.active)

    @asynccontextmanager
    async def held(selected, actual_session, scope, *, roles, purpose):
        assert selected is case.selected and actual_session is session
        assert scope == _CONTROL and roles is case.roles
        assert (
            purpose is subject.locked.NativeWriterControlPurpose0056.PREPARE_OBSERVATION
        )
        case.active = True
        try:
            yield SimpleNamespace(observation=observation, check_held=check)
        finally:
            case.active = False

    async def writer(actual_session, check_held):
        assert actual_session is session
        check_held()
        return case.facts

    async def snapshot(actual_session, _row):
        assert actual_session is session
        return case.match, _EVENT

    monkeypatch.setattr(
        subject.locked, "_factory_pins", lambda _: (object(), engine, (), ())
    )
    monkeypatch.setattr(subject.locked, "_require_session", Mock())
    monkeypatch.setattr(subject.locked, "held_native_writer_control_0056_v1", held)
    monkeypatch.setattr(subject.locked.DemoControlRepository, "_locked", AsyncMock())
    monkeypatch.setattr(subject.locked.DemoControlRepositoryV2, "_snapshot", snapshot)
    monkeypatch.setattr(
        subject.ledger.QualificationLedgerRepository,
        "_locked",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        subject.locked.writer56,
        "observe_restricted_readwrite_writer_0056_v1",
        AsyncMock(return_value=observation.writer_catalog),
    )
    monkeypatch.setattr(subject, "_actual_transaction", writer)
    monkeypatch.setattr(subject, "_verify_journal", AsyncMock())
    return case


def _enter(case, purpose=subject.QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION):
    return subject.held_native_qualification_writer_0056_v1(
        case.selected,
        case.session,
        _SCOPE,
        roles=case.roles,
        purpose=purpose,
        expected=case.expected,
    )


async def test_bootstrap_scope_can_check_exact_issue_write_preconditions(ready):
    async with _enter(ready) as writer:
        assert writer.transaction == ready.facts
        assert writer._scope_row_for_issue() is ready.row
        assert await writer.recheck() == writer.transaction.observed_at
        assert writer.issuance_authority is writer.execution_authority is False
        assert writer.admission == "DENY"
    assert [call.args[0] for call in ready.session.execute.await_args_list] == [
        *subject._SETUP,
        subject._WRITER_FENCE,
        subject._CONTROL_FENCE,
    ]
    with pytest.raises(subject.NativeQualificationWriter0056Error):
        writer.check_held()


@pytest.mark.parametrize(
    "mutation", ["stop", "expiry", "token", "prior_revision", "reserve_bootstrap"]
)
async def test_inconsistent_owner_expiry_or_scope_refuses_before_yield(ready, mutation):
    purpose = subject.QualificationWritePurpose0056.ISSUE_ACCOUNT_REVISION
    if mutation == "stop":
        ready.match = control.stopped_genesis(_CONTROL, _AT)
    elif mutation == "expiry":
        ready.facts = replace(ready.facts, observed_at=ready.expected.expires_at)
    elif mutation == "token":
        ready.expected = replace(
            ready.expected,
            control=replace(ready.expected.control, owner_token=b"Z" * 32),
        )
    elif mutation == "prior_revision":
        ready.expected = replace(ready.expected, ledger_revision=1)
    else:
        purpose = subject.QualificationWritePurpose0056.RESERVE_QUALIFICATION
    with pytest.raises(
        subject.NativeQualificationWriter0056Error,
        match="^native_qualification_writer_0056_refused$",
    ):
        async with _enter(ready, purpose):
            pytest.fail("invalid precondition got a held writer")


@pytest.mark.parametrize("mutation", ["xid", "expiry", "unowned_revision", "catalog"])
async def test_context_exit_refuses_before_commit_when_fence_changes(ready, mutation):
    with pytest.raises(subject.NativeQualificationWriter0056Error):
        async with _enter(ready):
            if mutation == "xid":
                ready.facts = replace(ready.facts, transaction_id="512")
            elif mutation == "expiry":
                ready.facts = replace(
                    ready.facts, observed_at=ready.expected.expires_at
                )
            elif mutation == "unowned_revision":
                ready.row.ledger_revision += 1
            else:
                subject.locked.writer56.observe_restricted_readwrite_writer_0056_v1.return_value = object()


async def test_issuer_ack_checks_exact_own_trigger_result_before_and_after_context(
    ready, monkeypatch
):
    validate_finite = AsyncMock()
    monkeypatch.setattr(
        subject, "_validate_held_finite_checkpoint_rows", validate_finite
    )
    monkeypatch.setattr(subject, "_finite_payload", lambda raw: raw is not None)
    async with _enter(ready) as writer:
        ready.facts = replace(ready.facts, observed_at=_AT + timedelta(seconds=2))
        raw = "{}"
        claims_hash = sha(raw.encode())
        row = ready.row
        row.account_revision = row.ledger_revision = 1
        row.updated_at = _AT + timedelta(seconds=1, microseconds=1)
        row.claims_json, row.claims_sha256 = raw, claims_hash
        proof = canonical(
            {
                "db_role_oid": writer.transaction.role_oid,
                "db_backend_pid": writer.transaction.backend_pid,
                "db_transaction_id": writer.transaction.transaction_id,
            }
        )
        issuer_hash = sha(proof)
        issued = subject.DemoCollectorIssuedAccountRevision(
            issuer_sha256=issuer_hash,
            proof_json=proof.decode(),
            claims_json=raw,
            claims_sha256=claims_hash,
            environment=row.environment,
            account_id=row.account_id,
            settlement_currency=row.settlement_currency,
            previous_account_revision=0,
            previous_ledger_revision=0,
            account_revision=1,
            ledger_revision=1,
            control_revision=ready.match.revision,
            control_event_sha256=_EVENT,
            control_state_sha256=control.digest(
                control.canonical(control.document(ready.match))
            ),
            original_expires_at=ready.expected.expires_at,
            db_recorded_at=row.updated_at,
        )
        ready.session.get.return_value = issued
        assert (
            await writer.recheck_issued(
                issuer_sha256=issuer_hash, claims_sha256=claims_hash
            )
            == ready.facts.observed_at
        )
    validate_finite.assert_awaited_once()


async def test_failed_issuance_acknowledgement_requires_rollback_even_if_caught(ready):
    with pytest.raises(subject.NativeQualificationWriter0056Error):
        async with _enter(ready) as writer:
            for _ in range(2):
                with pytest.raises(subject.NativeQualificationWriter0056Error):
                    await writer.recheck_issued(
                        issuer_sha256="1" * 64, claims_sha256="2" * 64
                    )
