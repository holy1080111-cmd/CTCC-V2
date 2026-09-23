"""Real PostgreSQL B1 persistence; explicit isolated DATABASE_URL, synthetic data.

No exchange IO, credentials or account authority. Process/DB restart validation
is a separate owned harness; these tests never relabel an engine dispose as one.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.database.models.account_capture_journal import DemoAccountCaptureEvent
from app.database.models.qualification_ledger import QualificationAccountScope
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification.reservations import LedgerScope
from tests.integration import test_qualification_ledger_repository as fixtures

database = fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def now():
    return datetime.now(UTC)


def event(
    capture_id, sequence, previous, kind, data, *, raw=None, outcome="in_progress"
):
    return journal._JournalEvent(
        journal._ISSUER,
        journal.canonical(
            {
                "version": "ctcc.demo_account_ingestion_event.v1",
                "capture_id": capture_id,
                "sequence": sequence,
                "previous_sha256": previous,
                "kind": kind,
                "outcome": outcome,
                "observed_at": now().isoformat(),
                "data": data,
                "raw_sha256": None if raw is None else journal.digest(raw),
                "packet_sha256": None,
                "account_complete": False,
                "execution_authority": False,
                "admission": "DENY",
            }
        ),
        raw,
    )


async def initialize(database):
    scope = LedgerScope(
        account_id=f"123456789{uuid4().int % 10**12:012d}", settlement_currency="USDT"
    )
    ledger = QualificationLedgerRepository(database[1], clock=now)
    checkpoint = await ledger.initialize_capture_scope(scope)
    repo = AccountCaptureJournalRepository(database[1], clock=now)
    capture_id = uuid4().hex
    start = event(
        capture_id,
        1,
        None,
        "capture_start",
        {
            "environment": "demo",
            "account_id": scope.account_id,
            "settlement_currency": "USDT",
            "local_checkpoint_sha256": checkpoint.state_sha256,
            "plan_sha256": "a" * 64,
            "raw_retention": "not_read",
            "source_state": "not_requested",
            # Synthetic historical-expiry fixture only, not owned acquisition proof.
            "recovery_not_before": now().isoformat(),
        },
    )
    return scope, ledger, repo, start


async def test_commit_separate_readback_restart_and_private_raw_identity(database):
    scope, ledger, repo, start = await initialize(database)
    before = await ledger.read_bootstrap_checkpoint(scope)
    first = await repo._append(scope, start)
    raw = b'{"code":"0","data":[{"uid":"123456789","equity":"12.5"}]}'
    saved = event(
        journal.checked_event(start)["capture_id"],
        2,
        journal.digest(start.event_json),
        "raw_finalized",
        {
            "raw_retention": "durable_secret_checked",
            "observed_bytes": len(raw),
            "source_state": "complete_page",
        },
        raw=raw,
    )
    second = await repo._append(scope, saved)
    assert first.db_recorded_at <= first.readback_at
    assert second.event.raw_body == raw
    restarted = AccountCaptureJournalRepository(database[1], clock=now)
    chain = await restarted.read_chain(
        scope, journal.checked_event(start)["capture_id"]
    )
    assert [item.event for item in chain] == [start, saved]
    assert all(b"123456789" not in item.receipt_json for item in chain)
    assert (await ledger.read_bootstrap_checkpoint(scope)).state == before.state


async def test_concurrent_exact_event_is_idempotent_conflicting_bytes_are_not(database):
    scope, _, repo, start = await initialize(database)
    other = AccountCaptureJournalRepository(database[1], clock=now)
    received = await asyncio.gather(
        repo._append(scope, start), other._append(scope, start)
    )
    assert received[0].event == received[1].event == start
    record = journal.checked_event(start)
    conflict = event(
        record["capture_id"],
        1,
        None,
        "capture_start",
        {**record["data"], "plan_sha256": "b" * 64},
    )
    with pytest.raises(journal.AccountJournalError, match="event_conflict"):
        await repo._append(scope, conflict)
    chain = await repo.read_chain(scope, record["capture_id"])
    assert len(chain) == 1 and chain[0].event == start


async def test_concurrent_different_captures_do_not_change_scope_revisions(database):
    scope, ledger, repo, first = await initialize(database)
    before = await ledger.read_bootstrap_checkpoint(scope)
    second = event(
        uuid4().hex, 1, None, "capture_start", journal.checked_event(first)["data"]
    )
    values = await asyncio.gather(
        repo._append(scope, first), repo._append(scope, second)
    )
    assert values[0].event != values[1].event
    after = await ledger.read_bootstrap_checkpoint(scope)
    assert before.state == after.state
    async with database[1]() as session:
        row = await session.get(
            QualificationAccountScope,
            (scope.environment, scope.account_id, scope.settlement_currency),
        )
        assert row.account_revision == row.ledger_revision == 0
        assert row.claims_json is row.claims_sha256 is None


@pytest.mark.parametrize("operation", ["update", "delete", "truncate"])
async def test_sql_journal_rows_are_immutable(database, operation):
    scope, _, repo, start = await initialize(database)
    await repo._append(scope, start)
    capture_id = journal.checked_event(start)["capture_id"]
    statements = {
        "update": update(DemoAccountCaptureEvent)
        .where(DemoAccountCaptureEvent.capture_id == capture_id)
        .values(event_json="{}"),
        "delete": delete(DemoAccountCaptureEvent).where(
            DemoAccountCaptureEvent.capture_id == capture_id
        ),
        "truncate": text("TRUNCATE demo_account_capture_events"),
    }
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            await session.execute(statements[operation])
    assert (await repo.read_chain(scope, capture_id))[0].event == start


async def test_commit_succeeds_but_lost_readback_never_returns_acceptance(
    database, monkeypatch
):
    scope, ledger, repo, start = await initialize(database)
    before = await ledger.read_bootstrap_checkpoint(scope)

    async def unavailable(*args):
        raise journal.AccountJournalError("journal_commit_readback_unknown")

    with monkeypatch.context() as patch:
        patch.setattr(AccountCaptureJournalRepository, "read_event", unavailable)
        with pytest.raises(journal.AccountJournalError, match="readback_unknown"):
            await repo._append(scope, start)
    reread = await repo.read_chain(scope, journal.checked_event(start)["capture_id"])
    assert len(reread) == 1 and reread[0].event == start
    assert (await ledger.read_bootstrap_checkpoint(scope)).state == before.state
    # DB-only idempotent append/readback does not repeat any account request.
    assert (await repo._append(scope, start)).event == start


async def test_cancel_after_durable_commit_preserves_event_and_unknown_owner(
    database, monkeypatch
):
    scope, _, repo, start = await initialize(database)

    async def cancelled(*args):
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(AccountCaptureJournalRepository, "read_event", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await repo._append(scope, start)
    result = await repo.recover_interrupted(
        scope,
        capture_id=journal.checked_event(start)["capture_id"],
        expected_head_sha256=journal.digest(start.event_json),
    )
    record = journal.checked_event(result.event)
    assert record["outcome"] == "interrupted_owner_unknown"
    assert record["data"]["owner_liveness"] == "unverified"
    assert record["data"]["process_death_confirmed"] is False
    with pytest.raises(journal.AccountJournalError, match="predecessor_conflict"):
        await repo._append(
            scope,
            event(
                record["capture_id"],
                3,
                journal.digest(result.event.event_json),
                "body_progress",
                {"raw_retention": "not_read"},
            ),
        )


async def test_scope_and_chain_forgery_cannot_rebind_persisted_capture(database):
    scope, _, repo, start = await initialize(database)
    await repo._append(scope, start)
    other_scope, _, _, _ = await initialize(database)
    with pytest.raises(journal.AccountJournalError, match="scope_mismatch"):
        await repo.read_chain(other_scope, journal.checked_event(start)["capture_id"])
    with pytest.raises(journal.AccountJournalError, match="predecessor_conflict"):
        await repo._append(
            scope,
            event(
                journal.checked_event(start)["capture_id"],
                2,
                "f" * 64,
                "body_progress",
                {"raw_retention": "not_read"},
            ),
        )


async def test_sql_trigger_rejects_direct_hash_or_false_authority_binding(database):
    scope, _, repo, start = await initialize(database)
    await repo._append(scope, start)
    record = journal.checked_event(start)
    forged = {
        **record,
        "sequence": 2,
        "previous_sha256": journal.digest(start.event_json),
        "kind": "body_progress",
        "execution_authority": True,
    }
    payload = journal.canonical(forged)
    with pytest.raises(DBAPIError):
        async with database[1]() as session, session.begin():
            session.add(
                DemoAccountCaptureEvent(
                    capture_id=record["capture_id"],
                    sequence=2,
                    environment="demo",
                    account_id=scope.account_id,
                    settlement_currency="USDT",
                    previous_sha256=journal.digest(start.event_json),
                    event_sha256=journal.digest(payload),
                    event_json=payload.decode(),
                    chain_bytes=len(start.event_json) + len(payload),
                    raw_body=None,
                    packet_payload=None,
                )
            )
            await session.flush()
    assert len(await repo.read_chain(scope, record["capture_id"])) == 1
