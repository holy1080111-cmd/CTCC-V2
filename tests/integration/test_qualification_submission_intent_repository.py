"""Actual isolated PostgreSQL commits/locks; all source claims are synthetic."""

import asyncio
import json
import sys
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database.models.qualification_ledger import QualificationReservationTransition
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import QualificationLedgerError
from app.trade_qualification.submission_intent import (
    build_submission_intent,
    replay_submission_intent,
)
from tests.integration import test_qualification_ledger_repository as fixtures
from tests.unit.qualification_execution_binding_fixtures import execution_binding

database = fixtures.database
controlled_current55_database = fixtures.controlled_current55_database
ledger_database = fixtures.ledger_database
fixture = fixtures.fixture
initialize = fixtures.initialize

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("fixture", ("long", "short"), indirect=True)
async def test_atomic_consume_intent_committed_and_independent_restart_readback(
    ledger_database, fixture
):
    repo, clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    result = await repo.consume_with_submission_intent(
        reserved.scope, reserved.original_event_key, expected_revision=2
    )
    body = json.loads(result.canonical_json)
    assert body["version"] == "ctcc-demo-submit-intent-v1"
    assert body["consumed_receipt"]["state"] == "consumed"
    assert body["consumed_receipt"]["ledger_revision"] == 3
    restarted = QualificationLedgerRepository(ledger_database[1], clock=clock)
    assert (
        await restarted.read_submission_intent(
            reserved.scope, reserved.original_event_key, expected_sha256=result.sha256
        )
        == result
    )
    async with ledger_database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservationTransition)
                .filter_by(reservation_id=reserved.reservation_id)
                .order_by(QualificationReservationTransition.state_revision)
            )
        ).all()
        assert len(rows) == 2
        assert rows[1].reason_code == "consumed_with_submit_intent"
        assert rows[1].evidence_json == result.canonical_json
    assert result.execution_authority is False and result.order_retry_authority is False


async def test_concurrent_consumers_only_one_committed_intent(ledger_database, fixture):
    repo, clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    other = QualificationLedgerRepository(ledger_database[1], clock=clock)
    results = await asyncio.gather(
        *(
            item.consume_with_submission_intent(
                reserved.scope, reserved.original_event_key, expected_revision=2
            )
            for item in (repo, other)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, QualificationLedgerError) for item in results) == 1
    assert sum(not isinstance(item, BaseException) for item in results) == 1
    state = await repo.read_scope(reserved.scope)
    assert state.ledger_revision == 3 and len(state.active) == 1


async def test_late_failure_rolls_back_intent_and_consumption(
    ledger_database, fixture, monkeypatch
):
    repo, _clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)

    def deny(*args):
        raise QualificationLedgerError("synthetic_last_guard_rejection")

    monkeypatch.setattr(repo, "_late_guard", deny)
    with pytest.raises(QualificationLedgerError, match="last_guard_rejection"):
        await repo.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=2
        )
    state = await repo.read_scope(reserved.scope)
    assert state.ledger_revision == 2 and state.active[0].state == "reserved"
    async with ledger_database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservationTransition).filter_by(
                    reservation_id=reserved.reservation_id
                )
            )
        ).all()
        assert len(rows) == 1 and rows[0].to_state == "reserved"


async def test_readback_failure_does_not_roll_back_or_retry_committed_consumption(
    ledger_database, fixture, monkeypatch
):
    repo, clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)

    async def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic read unavailable")

    monkeypatch.setattr(repo, "read_submission_intent", unavailable)
    with pytest.raises(ConnectionError):
        await repo.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=2
        )
    state = await repo.read_scope(reserved.scope)
    assert state.ledger_revision == 3 and state.active[0].state == "consumed"
    restarted = QualificationLedgerRepository(ledger_database[1], clock=clock)
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await restarted.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=3
        )


async def test_legacy_consumption_cannot_backfill_an_intent(ledger_database, fixture):
    repo, _ = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    await repo.consume_once(
        reserved.scope, reserved.original_event_key, expected_revision=2
    )
    with pytest.raises(QualificationLedgerError, match="submit_intent_missing"):
        await repo.read_submission_intent(
            reserved.scope, reserved.original_event_key, expected_sha256="f" * 64
        )
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await repo.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=3
        )


async def test_uncertain_and_expired_intent_read_is_audit_only_and_hold_remains(
    ledger_database, fixture
):
    repo, clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    result = await repo.consume_with_submission_intent(
        reserved.scope, reserved.original_event_key, expected_revision=2
    )
    clock.value = fixture.request.origin.deadline + timedelta(days=1)
    await repo.mark_uncertain(
        reserved.scope, reserved.original_event_key, expected_revision=3
    )
    again = await repo.read_submission_intent(
        reserved.scope, reserved.original_event_key, expected_sha256=result.sha256
    )
    assert again == result and again.order_retry_authority is False
    state = await repo.read_scope(reserved.scope)
    assert len(state.active) == 1 and state.active[0].state == "uncertain"
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await repo.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=4
        )


async def test_diagnose_uncertain_v1_intent_replay_without_raw_values(
    ledger_database, fixture
):
    """Exercise the original PostgreSQL case and report fixed replay stages."""
    replay_code = replay_submission_intent.__code__
    build_code = build_submission_intent.__code__
    ordinal = 0
    failures = []

    def trace_replay(frame, event, arg):
        if event == "exception" and len(failures) < 12:
            kind, error, _ = arg
            fixed_stage = str(error) if kind is ValueError else ""
            if fixed_stage not in {"canonical", "version", "legacy_size", "changed"}:
                fixed_stage = "fixed_stage_unavailable"
            kind_name = (
                kind.__name__
                if kind.__name__
                in {
                    "ValueError",
                    "ValidationError",
                    "QualificationLedgerError",
                    "TypeError",
                    "KeyError",
                }
                else "OtherError"
            )
            failures.append(
                (ordinal, frame.f_code.co_name, frame.f_lineno, kind_name, fixed_stage)
            )
        return trace_replay

    def trace_call(frame, event, _):
        nonlocal ordinal
        if event == "call" and frame.f_code is replay_code:
            ordinal += 1
            return trace_replay
        if event == "call" and frame.f_code is build_code:
            return trace_replay
        return None

    original = sys.gettrace()
    try:
        sys.settrace(trace_call)
        await test_uncertain_and_expired_intent_read_is_audit_only_and_hold_remains(
            ledger_database, fixture
        )
    except QualificationLedgerError:
        pytest.fail(f"isolated intent replay diagnostic {failures}", pytrace=False)
    finally:
        sys.settrace(original)
    assert ordinal == 2 and not failures


@pytest.mark.parametrize("fixture", ("long", "short"), indirect=True)
async def test_v2_exact_body_is_committed_with_consumption_and_replayed_after_restart(
    ledger_database, fixture
):
    binding = execution_binding(fixture)
    repo, clock = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    result = await repo.consume_with_submission_intent(
        reserved.scope,
        reserved.original_event_key,
        expected_revision=2,
        execution_binding=binding,
    )
    body = json.loads(result.canonical_json)
    assert body["version"] == "ctcc-demo-submit-intent-v2"
    assert body["exchange_request"]["body"]["ordType"] == "fok"
    assert body["exchange_request"]["body"]["tdMode"] == "isolated"
    async with ledger_database[1]() as session:
        row = await session.scalar(
            select(QualificationReservationTransition).filter_by(
                reservation_id=reserved.reservation_id, to_state="consumed"
            )
        )
        assert row.evidence_json == result.canonical_json
    clock.value = fixture.request.origin.deadline + timedelta(days=1)
    restarted = QualificationLedgerRepository(ledger_database[1], clock=clock)
    await restarted.mark_uncertain(
        reserved.scope, reserved.original_event_key, expected_revision=3
    )
    again = await restarted.read_submission_intent(
        reserved.scope, reserved.original_event_key, expected_sha256=result.sha256
    )
    assert (
        again == result
        and not again.execution_authority
        and not again.order_retry_authority
    )
    state = await restarted.read_scope(reserved.scope)
    assert state.active[0].state == "uncertain"
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await restarted.consume_with_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_revision=4,
            execution_binding=binding,
        )


async def test_invalid_v2_binding_rolls_back_without_falling_back_to_legacy_intent(
    ledger_database, fixture
):
    binding = execution_binding(fixture).model_copy(
        update={"recheck_json": '{"passed":true}'}
    )
    repo, _ = await initialize(ledger_database, fixture)
    reserved = await repo.reserve(fixture.request)
    with pytest.raises(
        QualificationLedgerError, match="submit_execution_binding_invalid"
    ):
        await repo.consume_with_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_revision=2,
            execution_binding=binding,
        )
    state = await repo.read_scope(reserved.scope)
    assert state.active[0].state == "reserved" and state.ledger_revision == 2
    async with ledger_database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservationTransition).filter_by(
                    reservation_id=reserved.reservation_id
                )
            )
        ).all()
        assert len(rows) == 1 and rows[0].to_state == "reserved"
