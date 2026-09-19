"""Real PG transaction/readback with native synthetic G12/history source replay."""

import asyncio
import json
from uuid import uuid4

import pytest

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification.reservations import QualificationLedgerError
from tests.integration import test_qualification_ledger_repository as shared
from tests.unit.qualification_execution_binding_fixtures import (
    execution_binding,
    repin_json,
)
from tests.unit.qualification_history_intent_fixtures import history_ledger_fixture

database = shared.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(params=((2, "long"), (2, "short"), (3, "long"), (3, "short")))
def chain(request, tmp_path, database):
    # database fixture skips before expensive native publication if no explicit URL.
    version, direction = request.param
    return history_ledger_fixture(
        tmp_path,
        version=version,
        direction=direction,
        account_id=f"123456789{uuid4().int % 10**12:012d}",
    )


async def test_history_intent_atomic_single_consumer_and_new_session_readback(
    database, chain
):
    repo, clock = await shared.initialize(database, chain)
    reserved = await repo.reserve(chain.request)
    binding = await asyncio.to_thread(execution_binding, chain)
    restarted = QualificationLedgerRepository(database[1], clock=clock)
    results = await asyncio.gather(
        *(
            instance.consume_with_submission_intent(
                reserved.scope,
                reserved.original_event_key,
                expected_revision=2,
                execution_binding=binding,
            )
            for instance in (repo, restarted)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(item, QualificationLedgerError) for item in results) == 1
    accepted = [item for item in results if not isinstance(item, BaseException)]
    assert len(accepted) == 1
    record = accepted[0]
    assert (
        await restarted.read_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_sha256=record.sha256,
        )
        == record
    )
    state = await restarted.read_scope(reserved.scope)
    assert len(state.active) == 1 and state.active[0].state == "consumed"
    body = json.loads(record.canonical_json)
    assert body["candidate_entry"] == str(
        chain.request.origin.candidate.candidate_entry
    )
    assert not record.execution_authority and not record.order_retry_authority


async def test_missing_history_policy_marker_rolls_back_consume_and_intent(
    database, chain
):
    repo, _ = await shared.initialize(database, chain)
    reserved = await repo.reserve(chain.request)
    binding = await asyncio.to_thread(execution_binding, chain)
    original = json.loads(binding.original_inputs_json)
    original["policy"]["prefix"].pop("contract_version")
    changed, _ = repin_json(original)
    with pytest.raises(QualificationLedgerError):
        await repo.consume_with_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_revision=2,
            execution_binding=binding.model_copy(
                update={"original_inputs_json": changed}
            ),
        )
    state = await repo.read_scope(reserved.scope)
    assert state.ledger_revision == 2 and state.active == (reserved,)
    with pytest.raises(QualificationLedgerError, match="submit_intent_missing"):
        await repo.read_submission_intent(
            reserved.scope,
            reserved.original_event_key,
            expected_sha256="f" * 64,
        )
