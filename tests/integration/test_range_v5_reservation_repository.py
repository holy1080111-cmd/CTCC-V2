"""Real PostgreSQL only; synthetic market/account and memory G12 publication."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import QualificationLedgerError
from tests.integration import test_qualification_ledger_repository as shared
from tests.unit.qualification_range_v5_fixtures import range_v5_ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
database = shared.database


@pytest.fixture(params=("long", "short"))
def chain(request, database, monkeypatch):
    return range_v5_ledger_fixture(
        request.param, monkeypatch, account_id=f"123456789{uuid4().int % 10**12:012d}"
    )[:2]


async def test_v5_one_consumer_intent_and_separate_session_readback(database, chain):
    fixture, binding = chain
    repo, clock = await shared.initialize(database, fixture)
    reserved = await repo.reserve(fixture.request)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    results = await asyncio.gather(
        *(
            item.consume_with_submission_intent(
                reserved.scope,
                reserved.original_event_key,
                expected_revision=2,
                execution_binding=binding,
            )
            for item in (repo, restarted)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, QualificationLedgerError) for item in results) == 1
    records = [item for item in results if not isinstance(item, BaseException)]
    assert len(records) == 1
    assert (
        await restarted.read_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_sha256=records[0].sha256,
        )
        == records[0]
    )
    state = await restarted.read_scope(reserved.scope)
    assert len(state.active) == 1 and state.active[0].state == "consumed"
    assert not records[0].execution_authority


async def test_v5_missing_replay_never_inserts_and_binding_failure_retains_hold(
    database, chain
):
    fixture, binding = chain
    repo, _ = await shared.initialize(database, fixture)
    bad = fixture.request.model_copy(
        update={
            "replay_binding": fixture.request.replay_binding.model_copy(
                update={"recheck_json": '{"passed":true}'}
            )
        }
    )
    with pytest.raises(QualificationLedgerError, match="ledger_range_replay_denied"):
        await repo.reserve(bad)
    state = await repo.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active
    reserved = await repo.reserve(fixture.request)
    with pytest.raises(
        QualificationLedgerError, match="submit_range_replay_binding_changed"
    ):
        await repo.consume_with_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_revision=2,
            execution_binding=binding.model_copy(
                update={"reference_json": '{"passed":true}'}
            ),
        )
    state = await repo.read_scope(fixture.request.scope)
    assert state.ledger_revision == 2 and state.active == (reserved,)


async def test_v5_new_account_revision_cannot_reuse_old_source_binding(database, chain):
    fixture, binding = chain
    repo, clock = await shared.initialize(database, fixture)
    reserved = await repo.reserve(fixture.request)
    clock.value += timedelta(microseconds=1)
    claims = shared.refresh(fixture.claims, clock.value)
    state = await repo.reconcile_scope(claims, expected_revision=2)
    assert state.account_revision == 2
    with pytest.raises(QualificationLedgerError, match="ledger_revision_conflict"):
        await repo.consume_with_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_revision=state.ledger_revision,
            execution_binding=binding,
        )
    final = await repo.read_scope(fixture.request.scope)
    assert len(final.active) == 1 and final.active[0].state == "reserved"
