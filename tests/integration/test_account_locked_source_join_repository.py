"""Real isolated PostgreSQL UID lock/readback; synthetic account HTTP only.

Requires an explicit migrated DATABASE_URL. No authenticated OKX request,
exchange atomic-revision claim, portfolio authority or order write is made.
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
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit pytest fixture re-export
)
from tests.integration.test_qualification_ledger_repository import refresh
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.test_account_current_history_join import current_plan
from tests.unit.test_account_current_source_verifier import flat_pages
from tests.unit.test_qualification_account_capture import NOW, ms
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_v4 import plan as historical_plan
from tests.unit.test_qualification_account_v4 import script as account_script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def now():
    return datetime.now(UTC)


async def captured_pair(monkeypatch, database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    anchor = now().replace(microsecond=0) - timedelta(seconds=10)
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

    ledger = QualificationLedgerRepository(database[1], clock=now)
    repo = AccountCaptureJournalRepository(database[1], clock=now)
    await ledger.initialize_capture_scope(scope)
    shared = {
        "expected_uid": uid,
        "created_at": anchor,
        "history_start": anchor - timedelta(days=7),
        "history_end": anchor - timedelta(seconds=1),
    }
    ids = []
    for current_only in (False, True):
        selected = current_plan(**shared) if current_only else historical_plan(**shared)
        session = ControlledDemoAccountSession(
            credentials=credentials(session_binding_id=selected.session_binding_id),
            plan=selected,
            expected_plan_sha256=capture.plan_sha256(selected),
        )
        harness = Harness(
            monkeypatch,
            pages=flat_pages(),
            change=lambda stream, index, data: shifted(data),
        )
        harness.script = account_script(
            source=flat_pages(),
            streams=capture.V6_CURRENT_STREAMS if current_only else None,
        )
        harness.clock = now
        result = await bootstrap.collect_bootstrap_recorded(
            session,
            repository=ledger,
            journal_repository=repo,
            clock=now,
            barrier_completed_at=(now() - timedelta(milliseconds=10))
            if current_only
            else anchor - timedelta(seconds=2),
        )
        terminal = json.loads(result.journal_receipt_json)
        ids.append(terminal["capture_id"])
        harness.assert_closed()
    return scope, ledger, repo, tuple(ids)


async def test_isolated_postgresql_join_rebinds_current_and_history_to_local_revision(
    monkeypatch, database
):
    scope, ledger, repo, (history_id, current_id) = await captured_pair(
        monkeypatch, database
    )
    result = await repo.read_locked_current_history_join(
        scope, history_capture_id=history_id, current_capture_id=current_id
    )
    value = json.loads(result.receipt_json)
    assert value["local_revision_readback_verified"] is True
    assert value["recorded_local_checkpoint_sha256"] == value["db_local_state_sha256"]
    assert value["db_account_revision"] == value["db_ledger_revision"] == 0
    assert value["account_complete"] is result.account_complete is False
    assert value["execution_authority"] is result.execution_authority is False
    assert value["admission"] == "DENY" and value["snapshot"] is None
    assert (
        "current_local_revision_readback_required"
        in value["recorded_pre_lock_blocking_reasons"]
    )
    assert (
        "current_local_revision_readback_required"
        not in value["locked_readback_blocking_reasons"]
    )
    assert (
        "history_tail_not_atomically_closed"
        in value["locked_readback_blocking_reasons"]
    )
    assert (await ledger.read_bootstrap_checkpoint(scope)).state_sha256 == value[
        "db_local_state_sha256"
    ]


async def test_isolated_postgresql_join_rejects_later_local_revision(
    monkeypatch, database
):
    scope, ledger, repo, (history_id, current_id) = await captured_pair(
        monkeypatch, database
    )
    fixture = await asyncio.to_thread(ledger_fixture, account_id=scope.account_id)
    await ledger.reconcile_scope(refresh(fixture.claims, now()), expected_revision=0)
    with pytest.raises(
        journal.AccountJournalError, match="journal_join_local_revision_changed"
    ):
        await repo.read_locked_current_history_join(
            scope, history_capture_id=history_id, current_capture_id=current_id
        )
