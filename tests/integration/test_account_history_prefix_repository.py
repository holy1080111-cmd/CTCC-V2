"""Real PostgreSQL B3-prefix replay; synthetic account HTTP is not admission."""

import json

import pytest

from app.database.repositories.account_history_prefix import (
    AccountHistoryPrefixError,
    AccountHistoryPrefixRepository,
)
from app.trade_qualification import account_observation_index as observed
from tests.integration.test_account_history_query_verifier_repository import now
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    stored,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_full_prefix_replays_committed_b3_and_preserves_denial(
    monkeypatch, database
):
    _, scope, reference, _, batch = await stored(monkeypatch, database)
    receipt = await AccountHistoryPrefixRepository(
        database[1], clock=now
    ).read_full_prefix(scope)
    value = json.loads(receipt.receipt_json)
    assert value["through_sequence"] == batch.sequence == 1
    assert value["head_sha256"] == batch.event_sha256
    assert value["last_source_reference"] == observed.reference_document(reference)
    assert value["entire_accepted_b3_index_prefix_replayed"] is True
    assert value["current_predecessor_sequence"] == 0
    assert value["current_predecessor_sha256"] is None
    assert value["account_complete"] is receipt.account_complete is False
    assert value["execution_authority"] is receipt.execution_authority is False
    assert value["admission"] == receipt.admission == "DENY"
    assert scope.account_id.encode() not in receipt.receipt_json


async def test_new_committed_b3_batch_during_prefix_replay_is_rejected(
    monkeypatch, database
):
    history, scope, _, _, _ = await stored(monkeypatch, database)
    prefix = AccountHistoryPrefixRepository(database[1], clock=now)
    original_read = prefix.history.read

    async def append_after_page(_scope, *, after, limit):
        page = await original_read(_scope, after=after, limit=limit)
        if after == 0:
            reference, window = await additional_capture(
                monkeypatch, history, scope, []
            )
            await history.append(scope, reference, window, expected_revision=1)
        return page

    monkeypatch.setattr(prefix.history, "read", append_after_page)
    with pytest.raises(AccountHistoryPrefixError, match="history_prefix_read_failed"):
        await prefix.read_full_prefix(scope)
    assert len(await history.read(scope)) == 2
