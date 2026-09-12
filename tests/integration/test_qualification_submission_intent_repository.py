"""Actual isolated PostgreSQL commits/locks; all source claims are synthetic."""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database.models.qualification_ledger import QualificationReservationTransition
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import QualificationLedgerError
from tests.integration import test_qualification_ledger_repository as fixtures

database = fixtures.database
fixture = fixtures.fixture
initialize = fixtures.initialize

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("fixture", ("long", "short"), indirect=True)
async def test_atomic_consume_intent_committed_and_independent_restart_readback(
    database, fixture
):
    repo, clock = await initialize(database, fixture)
    reserved = await repo.reserve(fixture.request)
    result = await repo.consume_with_submission_intent(
        reserved.scope, reserved.original_event_key, expected_revision=2
    )
    body = json.loads(result.canonical_json)
    assert body["consumed_receipt"]["state"] == "consumed"
    assert body["consumed_receipt"]["ledger_revision"] == 3
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    assert (
        await restarted.read_submission_intent(
            reserved.scope, reserved.original_event_key, expected_sha256=result.sha256
        )
        == result
    )
    async with database[1]() as session:
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


async def test_concurrent_consumers_only_one_committed_intent(database, fixture):
    repo, clock = await initialize(database, fixture)
    reserved = await repo.reserve(fixture.request)
    other = QualificationLedgerRepository(database[1], clock=clock)
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
    database, fixture, monkeypatch
):
    repo, _clock = await initialize(database, fixture)
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
    async with database[1]() as session:
        rows = (
            await session.scalars(
                select(QualificationReservationTransition).filter_by(
                    reservation_id=reserved.reservation_id
                )
            )
        ).all()
        assert len(rows) == 1 and rows[0].to_state == "reserved"


async def test_readback_failure_does_not_roll_back_or_retry_committed_consumption(
    database, fixture, monkeypatch
):
    repo, clock = await initialize(database, fixture)
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
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    with pytest.raises(QualificationLedgerError, match="transition_denied"):
        await restarted.consume_with_submission_intent(
            reserved.scope, reserved.original_event_key, expected_revision=3
        )


async def test_legacy_consumption_cannot_backfill_an_intent(database, fixture):
    repo, _ = await initialize(database, fixture)
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
    database, fixture
):
    repo, clock = await initialize(database, fixture)
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
