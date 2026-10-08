"""V7 exact-UID PostgreSQL locked readback; synthetic account HTTP only.

Requires an explicit isolated, migrated DATABASE_URL. It never calls OKX or
publishes a complete account snapshot or execution authority.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_current_history_join as joined
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- pytest fixture re-export
)
from tests.unit.test_account_current_source_v7 import current_plan_v7, empty_v7_pages
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import NOW, ms
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_v4 import plan as historical_plan
from tests.unit.test_qualification_account_v4 import script as account_script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _now():
    return datetime.now(UTC)


async def _captured_v7_pair(monkeypatch, database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    anchor = _now().replace(microsecond=0) - timedelta(seconds=10)
    shift_ms = int(ms(anchor)) - int(ms(NOW))

    def shifted(value):
        if type(value) is list:
            return [shifted(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + shift_ms)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else uid
                if key == "uid"
                else shifted(item)
                for key, item in value.items()
            }
        return value

    ledger = QualificationLedgerRepository(database[1], clock=_now)
    repo = AccountCaptureJournalRepository(database[1], clock=_now)
    await ledger.initialize_capture_scope(scope)
    shared = {
        "expected_uid": uid,
        "created_at": anchor,
        "history_start": anchor - timedelta(days=7),
        "history_end": anchor - timedelta(seconds=1),
    }
    ids = []
    for current_only in (False, True):
        selected = (
            current_plan_v7(**shared) if current_only else historical_plan(**shared)
        )
        pages = empty_v7_pages() if current_only else flat_pages()
        session = ControlledDemoAccountSession(
            credentials=credentials(session_binding_id=selected.session_binding_id),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )
        harness = Harness(
            monkeypatch, pages=pages, change=lambda _, __, data: shifted(data)
        )
        harness.script = account_script(
            source=pages,
            streams=capture.V7_CURRENT_STREAMS if current_only else None,
        )
        harness.clock = _now
        result = await bootstrap.collect_bootstrap_recorded(
            session,
            repository=ledger,
            journal_repository=repo,
            clock=_now,
            barrier_completed_at=(
                _now() - timedelta(milliseconds=10)
                if current_only
                else anchor - timedelta(seconds=2)
            ),
        )
        ids.append(json.loads(result.journal_receipt_json)["capture_id"])
        harness.assert_closed()
    return scope, repo, tuple(ids)


async def test_isolated_postgresql_v7_join_has_distinct_policy_receipt_and_no_authority(
    monkeypatch, database
):
    scope, repo, (history_id, current_id) = await _captured_v7_pair(
        monkeypatch, database
    )
    locked = await repo.read_locked_current_history_join(
        scope,
        history_capture_id=history_id,
        current_capture_id=current_id,
        expected_policy_sha256=joined.V7_POLICY_SHA256,
    )
    value = json.loads(locked.receipt_json)
    assert value["schema_version"] == "ctcc.demo_account_locked_source_join.v3"
    assert value["join_policy_sha256"] == joined.V7_POLICY_SHA256
    assert value["recorded_local_checkpoint_sha256"] == value["db_local_state_sha256"]
    assert value["local_revision_readback_verified"] is True
    assert value["exchange_atomic_revision_verified"] is False
    assert value["history_tail_closed"] is False
    assert value["flat_start_permission"] is False
    assert value["snapshot"] is None
    assert value["account_complete"] is locked.account_complete is False
    assert value["execution_authority"] is locked.execution_authority is False
    assert value["admission"] == "DENY"

    ordered = await repo.read_locked_current_history_join(
        scope,
        history_capture_id=history_id,
        current_capture_id=current_id,
        expected_policy_sha256=joined.V7_ORDERED_POLICY_SHA256,
    )
    ordered_value = json.loads(ordered.receipt_json)
    assert ordered_value["schema_version"] == "ctcc.demo_account_locked_source_join.v4"
    assert ordered_value["join_policy_sha256"] == joined.V7_ORDERED_POLICY_SHA256
    assert ordered_value["history_terminal_db_timestamp_before_current_request"] is True
    assert ordered_value["history_commit_before_current_request"] is False
    assert ordered_value["history_original_db_chain_sha256"]
    assert (
        ordered_value["history_journal_terminal_db_recorded_at"]
        < ordered_value["current_capture_started_at"]
    )
    assert ordered_value["account_complete"] is ordered.account_complete is False
    assert ordered_value["execution_authority"] is ordered.execution_authority is False
    assert ordered_value["admission"] == "DENY"


async def test_history_pre_read_waits_for_exact_uid_transaction_to_finish(
    monkeypatch, database
):
    """A pre-request read cannot see a V5 row still behind an open UID lock."""
    scope, repo, (history_id, _) = await _captured_v7_pair(monkeypatch, database)
    async with database[1]() as writer, writer.begin():
        await repo._lock(writer, scope)
        started = asyncio.Event()

        async def read_history():
            started.set()
            return await repo.read_chain(scope, history_id)

        waiting = asyncio.create_task(read_history())
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.05)
        assert not waiting.done()
    chain = await asyncio.wait_for(waiting, timeout=5)
    assert chain and chain[-1].event.event_json
