"""Real PostgreSQL admission: synthetic replay is never an owned R7 issuer."""

from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.recheck import RecordedRecheckAssessment
from app.trade_qualification.reservations import QualificationLedgerError
from app.trade_qualification.submission_intent import _document
from tests.integration import test_qualification_ledger_repository as shared
from tests.unit.qualification_range_v5_fixtures import range_v5_ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
database = shared.database


@pytest.fixture(params=("long", "short"))
def chain(request, database, monkeypatch):
    return range_v5_ledger_fixture(
        request.param, monkeypatch, account_id=f"123456789{uuid4().int % 10**12:012d}"
    )[:2]


async def test_computational_pass_cannot_create_durable_reservation(database, chain):
    fixture, _ = chain
    repo, clock = await shared.initialize(database, fixture)
    recorded = _document(
        fixture.request.replay_binding.recheck_json, RecordedRecheckAssessment
    )
    assert recorded.computational_checks_passed
    assert recorded.runtime_admissible is False
    assert recorded.source_authenticity_verified is False
    for restarted in (repo, QualificationLedgerRepository(database[1], clock=clock)):
        with pytest.raises(
            QualificationLedgerError, match="ledger_recheck_runtime_authority_missing"
        ):
            await restarted.reserve(fixture.request)
    state = await repo.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active
    assert (
        await repo.read_event_observation(
            fixture.request.scope, fixture.request.origin.original_event_key
        )
    ).matched is None


async def test_caller_pass_flag_and_valid_offline_replay_both_fail_closed(
    database, chain
):
    fixture, _ = chain
    repo, _ = await shared.initialize(database, fixture)
    forged = fixture.request.model_copy(
        update={
            "replay_binding": fixture.request.replay_binding.model_copy(
                update={"recheck_json": '{"passed":true}'}
            )
        }
    )
    with pytest.raises(QualificationLedgerError, match="ledger_range_replay_denied"):
        await repo.reserve(forged)
    with pytest.raises(
        QualificationLedgerError, match="ledger_recheck_runtime_authority_missing"
    ):
        await repo.reserve(fixture.request)
    state = await repo.read_scope(fixture.request.scope)
    assert state.ledger_revision == 1 and not state.active
