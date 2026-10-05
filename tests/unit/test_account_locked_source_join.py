"""Synthetic B1 and lock scheduling; no PostgreSQL or private OKX acceptance."""

import asyncio
import json
from datetime import timedelta

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint
from app.trade_qualification import account_capture_journal as journal
from tests.unit.test_account_current_history_join import recorded_current
from tests.unit.test_account_current_source_verifier import SCOPE, recorded
from tests.unit.test_qualification_account_capture import NOW
from tests.unit.test_qualification_bootstrap_runtime import unknown_state


class _Begin:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, kind, value, traceback):
        if self.session.owns_lock:
            if self.session.sequence == 1 and kind is None:
                self.session.state["revision_sha"] = "f" * 64
            self.session.lock.release()
        return False


class _Session:
    def __init__(self, lock, sequence, state):
        self.lock, self.sequence, self.state = lock, sequence, state
        self.owns_lock = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, traceback):
        return False

    def begin(self):
        return _Begin(self)


async def _fixture(monkeypatch):
    old = await recorded(monkeypatch)
    fresh, _ = await recorded_current(monkeypatch)
    old_id = journal.checked_event(old[0].event)["capture_id"]
    fresh_id = journal.checked_event(fresh[0].event)["capture_id"]
    revision_sha = journal.checked_event(fresh[0].event)["data"][
        "local_checkpoint_sha256"
    ]
    lock = asyncio.Lock()
    state = {"revision_sha": revision_sha, "sessions": 0}

    def sessions():
        state["sessions"] += 1
        return _Session(lock, state["sessions"], state)

    repository = AccountCaptureJournalRepository(sessions, clock=lambda: NOW)
    first_reading = asyncio.Event()
    release_first = asyncio.Event()
    second_waiting = asyncio.Event()
    calls = []

    async def locked(session, scope):
        assert scope == SCOPE
        if lock.locked():
            second_waiting.set()
        await lock.acquire()
        session.owns_lock = True
        calls.append((session.sequence, "lock"))
        return object()

    async def read(session, scope, capture_id):
        assert session.owns_lock and scope == SCOPE
        calls.append((session.sequence, capture_id))
        if session.sequence == 1 and capture_id == old_id:
            first_reading.set()
            await release_first.wait()
        return {old_id: old, fresh_id: fresh}[capture_id]

    async def checkpoint(session, scope, row):
        assert session.owns_lock and scope == SCOPE and row is not None
        calls.append((session.sequence, "checkpoint"))
        at = NOW + timedelta(seconds=2)
        return LedgerBootstrapCheckpoint(unknown_state(), at, at, state["revision_sha"])

    monkeypatch.setattr(repository, "_lock", locked)
    monkeypatch.setattr(repository, "_read_chain_locked", read)
    monkeypatch.setattr(repository.ledger, "_bootstrap_checkpoint", checkpoint)
    return (
        repository,
        old_id,
        fresh_id,
        state,
        calls,
        first_reading,
        release_first,
        second_waiting,
    )


@pytest.mark.asyncio
async def test_locked_source_join_rebinds_original_chains_and_keeps_deny(monkeypatch):
    repo, old_id, fresh_id, state, calls, entered, release, _ = await _fixture(
        monkeypatch
    )
    task = asyncio.create_task(
        repo.read_locked_current_history_join(
            SCOPE, history_capture_id=old_id, current_capture_id=fresh_id
        )
    )
    await entered.wait()
    release.set()
    result = await task
    data = json.loads(result.receipt_json)
    assert data["schema_version"] == "ctcc.demo_account_locked_source_join.v2"
    assert data["local_revision_readback_verified"] is True
    assert data["recorded_local_checkpoint_sha256"] == data["db_local_state_sha256"]
    assert data["db_account_revision"] == data["db_ledger_revision"] == 0
    assert data["account_complete"] is result.account_complete is False
    assert data["execution_authority"] is result.execution_authority is False
    assert data["account_revision_published"] is False
    assert data["snapshot"] is None and data["admission"] == "DENY"
    recorded = data["recorded_pre_lock_blocking_reasons"]
    locked = data["locked_readback_blocking_reasons"]
    assert "current_local_revision_readback_required" in recorded
    assert "current_local_revision_readback_required" not in locked
    assert set(locked) == set(recorded) - {"current_local_revision_readback_required"}
    assert "history_tail_not_atomically_closed" in locked
    assert calls == [(1, "lock"), (1, old_id), (1, fresh_id), (1, "checkpoint")]
    assert state["revision_sha"] == "f" * 64  # changed after the bounded read


@pytest.mark.asyncio
async def test_concurrent_locked_join_rejects_revision_changed_after_first_read(
    monkeypatch,
):
    repo, old_id, fresh_id, _state, calls, entered, release, waiting = await _fixture(
        monkeypatch
    )
    first = asyncio.create_task(
        repo.read_locked_current_history_join(
            SCOPE, history_capture_id=old_id, current_capture_id=fresh_id
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        repo.read_locked_current_history_join(
            SCOPE, history_capture_id=old_id, current_capture_id=fresh_id
        )
    )
    await waiting.wait()
    assert calls == [(1, "lock"), (1, old_id)]
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert outcomes[0].account_complete is False
    assert type(outcomes[1]) is journal.AccountJournalError
    assert str(outcomes[1]) == "journal_join_local_revision_changed"
    assert calls[1:4] == [(1, old_id), (1, fresh_id), (1, "checkpoint")]
    assert calls[4:] == [
        (2, "lock"),
        (2, old_id),
        (2, fresh_id),
        (2, "checkpoint"),
    ]


@pytest.mark.asyncio
async def test_locked_join_rejects_capture_id_reuse_before_any_db_read(monkeypatch):
    (
        repo,
        old_id,
        _fresh_id,
        state,
        calls,
        _entered,
        _release,
        _waiting,
    ) = await _fixture(monkeypatch)
    with pytest.raises(
        journal.AccountJournalError, match="journal_join_capture_ids_invalid"
    ):
        await repo.read_locked_current_history_join(
            SCOPE, history_capture_id=old_id, current_capture_id=old_id
        )
    assert state["sessions"] == 0 and not calls
