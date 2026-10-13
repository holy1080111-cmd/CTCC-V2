"""Isolated PostgreSQL B3/DB0038 join; account HTTP is synthetic only."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from app.database.repositories.account_current_history_observation import (
    AccountCurrentHistoryObservationRepository,
    CurrentHistoryObservationError,
)
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
)
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_locked_current_history_join_v7_repository import (
    _captured_v7_pair,
    _now,
)
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- isolated migrated PostgreSQL fixture
)
from tests.integration.test_qualification_ledger_repository import refresh
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _indexed_pair(monkeypatch, database):
    scope, journal, (history_id, current_id) = await _captured_v7_pair(
        monkeypatch, database
    )
    history_chain = await journal.read_chain(scope, history_id)
    current_chain = await journal.read_chain(scope, current_id)
    history_reference = observed.source_reference(history_chain)
    current_reference = observed.source_reference(current_chain)
    query = json.loads(
        observed.query.verify_history_query_chain(
            history_chain, **observed._pins(history_reference, scope)
        ).receipt_json
    )
    window = observed.ExecutionWindow(
        datetime.fromisoformat(query["requested_start"]),
        datetime.fromisoformat(query["requested_end"]) - timedelta(seconds=600),
    )
    history_index = AccountObservationIndexRepository(database[1], clock=_now)
    current_index = AccountCurrentObservationRepository(database[1], clock=_now)
    history = await history_index.append(
        scope, history_reference, window, expected_revision=0
    )
    current = await current_index.append(scope, current_reference, expected_sequence=0)
    return scope, history, current


async def test_b3_and_db0038_same_uid_join_preserves_unknowns(monkeypatch, database):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryObservationRepository(database[1], clock=_now)
    result = await repo.read_joined(
        scope,
        history_sequence=history.sequence,
        history_sha256=history.event_sha256,
        current_sequence=current.sequence,
        current_sha256=current.event_sha256,
    )
    value = json.loads(result.receipt_json)
    history_value = json.loads(history.replay.receipt_json)
    assert value["schema_version"] == "ctcc.demo_current_history_observation_join.v1"
    assert value["history_index_sha256"] == history.event_sha256
    assert value["current_index_sha256"] == current.event_sha256
    assert value["history_selected_head_replayed"] is True
    assert value["current_selected_head_replayed"] is True
    assert value["entire_history_index_replayed"] is False
    assert value["same_account_locked_head_binding"] is True
    assert set(history_value["blocking_reasons"]) <= set(value["blocking_reasons"])
    assert "history_tail_not_atomically_closed" in value["blocking_reasons"]
    assert "current_freshness_at_locked_readback_unproven" in value["blocking_reasons"]
    assert value["history_tail_closed"] is False
    assert value["source_authenticity_verified"] is False
    assert value["portfolio_risk_snapshot"] is None
    assert value["account_complete"] is result.account_complete is False
    assert value["flat_start_permission"] is result.flat_start_permission is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == result.admission == "DENY"
    assert (
        await repo.read_joined(
            scope,
            history_sequence=history.sequence,
            history_sha256=history.event_sha256,
            current_sequence=current.sequence,
            current_sha256=current.event_sha256,
        )
    ).receipt_json == result.receipt_json


async def test_b3_and_db0038_join_rejects_wrong_head_and_crossed_scope(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryObservationRepository(database[1], clock=_now)
    with pytest.raises(CurrentHistoryObservationError):
        await repo.read_joined(
            scope,
            history_sequence=history.sequence,
            history_sha256="0" * 64,
            current_sequence=current.sequence,
            current_sha256=current.event_sha256,
        )
    with pytest.raises(CurrentHistoryObservationError):
        await repo.read_joined(
            scope,
            history_sequence=history.sequence,
            history_sha256=history.event_sha256,
            current_sequence=current.sequence + 1,
            current_sha256=current.event_sha256,
        )
    with pytest.raises(CurrentHistoryObservationError):
        await repo.read_joined(
            LedgerScope(
                environment=scope.environment,
                account_id="987654321012345678901",
                settlement_currency=scope.settlement_currency,
            ),
            history_sequence=history.sequence,
            history_sha256=history.event_sha256,
            current_sequence=current.sequence,
            current_sha256=current.event_sha256,
        )


async def test_b3_and_db0038_join_rejects_old_pin_after_same_uid_history_append(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryObservationRepository(database[1], clock=_now)
    pins = {
        "history_sequence": history.sequence,
        "history_sha256": history.event_sha256,
        "current_sequence": current.sequence,
        "current_sha256": current.event_sha256,
    }
    assert (await repo.read_joined(scope, **pins)).admission == "DENY"

    history_index = AccountObservationIndexRepository(database[1], clock=_now)
    reference, window = await additional_capture(monkeypatch, history_index, scope, [])
    next_head = await history_index.append(
        scope, reference, window, expected_revision=history.sequence
    )
    assert next_head.sequence == history.sequence + 1
    assert next_head.event_sha256 != history.event_sha256
    assert (await history_index.read(scope, after=history.sequence, limit=1)) == (
        next_head,
    )
    assert (
        await AccountCurrentObservationRepository(database[1], clock=_now).read_one(
            scope, current.sequence
        )
    ) == current
    with pytest.raises(CurrentHistoryObservationError):
        await repo.read_joined(scope, **pins)


async def test_b3_and_db0038_join_rejects_changed_local_ledger_checkpoint(
    monkeypatch, database
):
    scope, history, current = await _indexed_pair(monkeypatch, database)
    repo = AccountCurrentHistoryObservationRepository(database[1], clock=_now)
    pins = {
        "history_sequence": history.sequence,
        "history_sha256": history.event_sha256,
        "current_sequence": current.sequence,
        "current_sha256": current.event_sha256,
    }
    assert (await repo.read_joined(scope, **pins)).admission == "DENY"
    ledger = QualificationLedgerRepository(database[1], clock=_now)
    before = await ledger.read_bootstrap_checkpoint(scope)
    fixture = await asyncio.to_thread(ledger_fixture, account_id=scope.account_id)
    reconciled = await ledger.reconcile_scope(
        refresh(fixture.claims, _now()), expected_revision=0
    )
    after = await ledger.read_bootstrap_checkpoint(scope)
    assert reconciled.account_revision == after.state.account_revision == 1
    assert after.state_sha256 != before.state_sha256
    with pytest.raises(CurrentHistoryObservationError):
        await repo.read_joined(scope, **pins)
