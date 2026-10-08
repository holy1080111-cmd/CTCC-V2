"""Synthetic V7 DB-lock scheduling; never a PostgreSQL or OKX acceptance."""

import asyncio
import json
from datetime import timedelta

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE, recorded
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_bootstrap_runtime import unknown_state


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, _kind, _value, _traceback):
        if self.session.owns_lock:
            self.session.lock.release()
        return False


class _Session:
    def __init__(self, lock):
        self.lock = lock
        self.owns_lock = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, _kind, _value, _traceback):
        return False

    def begin(self):
        return _Transaction(self)


async def _fixture(monkeypatch, *, checkpoint_changed=False):
    history = await recorded(monkeypatch)
    fresh, _ = await recorded_v7(monkeypatch)
    history_id = journal.checked_event(history[0].event)["capture_id"]
    current_id = journal.checked_event(fresh[0].event)["capture_id"]
    checkpoint_sha256 = journal.checked_event(fresh[0].event)["data"][
        "local_checkpoint_sha256"
    ]
    lock = asyncio.Lock()
    calls = []
    sessions = []

    def session_factory():
        session = _Session(lock)
        sessions.append(session)
        return session

    repo = AccountCaptureJournalRepository(session_factory, clock=lambda: NOW)

    async def take_lock(session, scope):
        assert scope == SCOPE
        await lock.acquire()
        session.owns_lock = True
        calls.append("uid_lock")
        return object()

    async def read_chain(session, scope, capture_id):
        assert session.owns_lock and scope == SCOPE
        calls.append(capture_id)
        return {history_id: history, current_id: fresh}[capture_id]

    async def checkpoint(session, scope, account):
        assert session.owns_lock and scope == SCOPE and account is not None
        calls.append("checkpoint")
        at = NOW + timedelta(seconds=4)
        return LedgerBootstrapCheckpoint(
            unknown_state(),
            at,
            at,
            "f" * 64 if checkpoint_changed else checkpoint_sha256,
        )

    monkeypatch.setattr(repo, "_lock", take_lock)
    monkeypatch.setattr(repo, "_read_chain_locked", read_chain)
    monkeypatch.setattr(repo.ledger, "_bootstrap_checkpoint", checkpoint)
    return repo, history, fresh, history_id, current_id, calls, sessions


@pytest.mark.asyncio
async def test_v7_locked_join_rereads_both_b1_chains_under_uid_lock_and_denies(
    monkeypatch,
):
    repo, history, fresh, history_id, current_id, calls, sessions = await _fixture(
        monkeypatch
    )
    pure = joined.join_recorded_account_sources(
        history_chain=history,
        history_reference=observed.source_reference(history),
        current_chain=fresh,
        current_reference=observed.source_reference(fresh),
        scope=SCOPE,
        validated_at=NOW + timedelta(seconds=4),
        expected_policy_sha256=joined.V7_POLICY_SHA256,
    )
    original = json.loads(pure.receipt_json)
    locked = await repo.read_locked_current_history_join(
        SCOPE,
        history_capture_id=history_id,
        current_capture_id=current_id,
        expected_policy_sha256=joined.V7_POLICY_SHA256,
    )
    value = json.loads(locked.receipt_json)
    assert calls == ["uid_lock", history_id, current_id, "checkpoint"]
    assert len(sessions) == 1 and not sessions[0].lock.locked()
    assert value["schema_version"] == "ctcc.demo_account_locked_source_join.v3"
    assert value["join_policy_sha256"] == joined.V7_POLICY_SHA256
    assert value["join_receipt_sha256"] == pure.receipt_sha256
    assert value["current_source_policy_sha256"] == current.V7_POLICY_SHA256
    assert value["history_source_reference"] == original["history_source_reference"]
    assert value["current_source_reference"] == original["current_source_reference"]
    assert value["recorded_pre_lock_blocking_reasons"] == original["blocking_reasons"]
    assert value["locked_readback_blocking_reasons"] == sorted(
        set(original["blocking_reasons"]) - {"current_local_revision_readback_required"}
    )
    assert (
        "history_tail_not_atomically_closed"
        in value["locked_readback_blocking_reasons"]
    )
    assert value["recorded_local_checkpoint_sha256"] == value["db_local_state_sha256"]
    assert value["db_account_revision"] == value["db_ledger_revision"] == 0
    assert value["db_active_hold_count"] == 0
    assert value["local_revision_readback_verified"] is True
    assert value["exchange_atomic_revision_verified"] is False
    assert value["history_tail_closed"] is False
    assert value["flat_start_permission"] is False
    assert value["snapshot"] is None
    assert value["account_complete"] is locked.account_complete is False
    assert value["account_revision_published"] is False
    assert value["execution_authority"] is locked.execution_authority is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_locked_join_requires_explicit_policy_and_rejects_unknown_before_db(
    monkeypatch,
):
    repo, _history, _fresh, history_id, current_id, calls, sessions = await _fixture(
        monkeypatch
    )
    with pytest.raises(
        journal.AccountJournalError, match="journal_join_policy_invalid"
    ):
        await repo.read_locked_current_history_join(
            SCOPE,
            history_capture_id=history_id,
            current_capture_id=current_id,
            expected_policy_sha256="0" * 64,
        )
    assert calls == [] and sessions == []
    with pytest.raises(joined.AccountSourceJoinError):
        await repo.read_locked_current_history_join(
            SCOPE, history_capture_id=history_id, current_capture_id=current_id
        )
    assert calls == ["uid_lock", history_id, current_id, "checkpoint"]


@pytest.mark.asyncio
async def test_v7_locked_join_rejects_changed_local_revision_without_receipt(
    monkeypatch,
):
    repo, _history, _fresh, history_id, current_id, calls, _sessions = await _fixture(
        monkeypatch, checkpoint_changed=True
    )
    with pytest.raises(
        journal.AccountJournalError, match="journal_join_local_revision_changed"
    ):
        await repo.read_locked_current_history_join(
            SCOPE,
            history_capture_id=history_id,
            current_capture_id=current_id,
            expected_policy_sha256=joined.V7_POLICY_SHA256,
        )
    assert calls == ["uid_lock", history_id, current_id, "checkpoint"]
