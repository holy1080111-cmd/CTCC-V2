"""Synthetic V7 DB-lock scheduling; never a PostgreSQL or OKX acceptance."""

import asyncio
import json
from datetime import timedelta

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import LedgerBootstrapCheckpoint
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_funding_bill_audit as funding_bills
from app.trade_qualification import account_history_query_verifier as history_query
from app.trade_qualification import account_observation_index as observed
from tests.unit.test_account_current_source_v7 import recorded_v7
from tests.unit.test_account_current_source_verifier import SCOPE, flat_pages, recorded
from tests.unit.test_qualification_account_capture import INSTRUMENT, NOW, ms, row
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


async def _fixture(monkeypatch, *, checkpoint_changed=False, history_pages=None):
    history = await recorded(monkeypatch, pages=history_pages)
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
async def test_typed_v7_locked_join_pins_v2_query_but_does_not_claim_commit(
    monkeypatch,
):
    repo, history, fresh, history_id, current_id, calls, sessions = await _fixture(
        monkeypatch
    )
    pure = joined.join_recorded_account_sources_v2(
        history_chain=history,
        history_reference=observed.source_reference(history),
        current_chain=fresh,
        current_reference=observed.source_reference(fresh),
        scope=SCOPE,
        validated_at=NOW + timedelta(seconds=4),
    )
    pure_value = json.loads(pure.receipt_json)
    locked = await repo.read_locked_current_history_typed_join(
        SCOPE,
        history_capture_id=history_id,
        current_capture_id=current_id,
    )
    value = json.loads(locked.receipt_json)
    assert calls == ["uid_lock", history_id, current_id, "checkpoint"]
    assert len(sessions) == 1 and not sessions[0].lock.locked()
    assert value["schema_version"] == "ctcc.demo_account_locked_source_join.v6"
    assert value["join_policy_sha256"] == joined.V7_ORDERED_TYPED_POLICY_SHA256
    assert value["join_receipt_sha256"] == pure.receipt_sha256
    assert value["history_query_policy_sha256"] == history_query.V2_POLICY_SHA256
    assert (
        value["history_query_receipt_sha256"]
        == pure_value["history_query_receipt_sha256"]
    )
    assert value["history_commit_before_current_request"] is False
    assert value["history_tail_closed"] is False
    assert value["flat_start_permission"] is False
    assert value["account_complete"] is locked.account_complete is False
    assert value["execution_authority"] is locked.execution_authority is False
    assert value["admission"] == "DENY"


@pytest.mark.asyncio
async def test_v7_locked_funding_join_replays_original_bill_pages_under_uid_lock(
    monkeypatch,
):
    pages = flat_pages()
    bill = row(
        "bills_recent",
        "900",
        instId=INSTRUMENT,
        instType="SWAP",
        pnl="-0.03",
        balChg="-0.03",
    )
    pages["bills_recent"] = [[bill], []]
    pages["bills_archive"] = [[bill], []]
    repo, history, _fresh, history_id, current_id, calls, sessions = await _fixture(
        monkeypatch, history_pages=pages
    )
    payload = next(
        item.event.packet_payload
        for item in history
        if item.event.packet_payload is not None
    )
    reference = observed.source_reference(history)
    packet = capture.verify_demo_account_packet(
        payload,
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )
    for stream in ("bills_recent", "bills_archive"):
        bill_pages = [
            item for item in packet.observations if item.request.stream == stream
        ]
        assert len(bill_pages) == 2
        first, terminal = bill_pages
        assert first.request.endpoint == (
            "/api/v5/account/bills"
            if stream == "bills_recent"
            else "/api/v5/account/bills-archive"
        )
        assert dict(first.request.parameters) == {
            "begin": ms(packet.plan.history_start),
            "end": ms(packet.plan.history_end),
            "limit": "100",
        }
        assert dict(terminal.request.parameters) == {
            **dict(first.request.parameters),
            "after": "900",
        }
        assert first.terminal is False and terminal.terminal is True
        assert terminal.after == first.rows[-1].row_id
        assert terminal.previous_page_sha256 == first.receipt_sha256
    original = funding_bills.audit_funding_bill_consistency(
        history, reference=reference, scope=SCOPE
    )
    result = await repo.read_locked_current_history_funding_join(
        SCOPE,
        history_capture_id=history_id,
        current_capture_id=current_id,
    )
    value = json.loads(result.receipt_json)
    assert calls == ["uid_lock", history_id, current_id, "checkpoint"]
    assert len(sessions) == 1 and not sessions[0].lock.locked()
    assert value["schema_version"] == "ctcc.demo_account_locked_source_join.v5"
    assert value["funding_source_join_policy_sha256"] == (
        funding_bills.SOURCE_OWNED_JOIN_POLICY_SHA256
    )
    assert value["funding_bill_consistency_policy_sha256"] == (
        funding_bills.CONSISTENCY_POLICY_SHA256
    )
    assert value["funding_bill_consistency_receipt_sha256"] == original.receipt_sha256
    assert value["funding_accrual_at"] is value["net_funding_cashflow"] is None
    assert value["funding_history_complete"] is False
    assert value["source_authenticity_verified"] is False
    assert value["history_tail_closed"] is False
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY"
    assert SCOPE.account_id not in result.receipt_json.decode("utf-8")


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
