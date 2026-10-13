"""DB0040 owned typed handoff with a synthetic Demo HTTP transport."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest

from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_observation_index_typed import (
    AccountObservationTypedIndexRepository,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification import account_observation_runtime as runtime
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_account_history_query_verifier_repository import now
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414
)
from tests.unit.test_qualification_account_capture import NOW, ms
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_one_owned_signed_b1_capture_commits_typed_b3_and_reads_it_back(
    monkeypatch, database
):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    anchor = now().replace(microsecond=0) - timedelta(seconds=10)
    shift_ms = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: str(int(item) + shift_ms)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else uid
                if key == "uid"
                else shift(item)
                for key, item in value.items()
            }
        return value

    harness = Harness(
        monkeypatch,
        pages=source_pages(),
        change=lambda _stream, _index, data: shift(data),
    )
    harness.clock = now
    plan = regional(
        expected_uid=uid,
        created_at=anchor,
        history_start=anchor - timedelta(days=6),
        history_end=anchor - timedelta(seconds=1),
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(),
        plan=plan,
        expected_plan_sha256=capture.plan_sha256(plan),
    )
    ledger = QualificationLedgerRepository(database[1], clock=now)
    journal = AccountCaptureJournalRepository(database[1], clock=now)
    typed = AccountObservationTypedIndexRepository(database[1], clock=now)
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    window = observed.ExecutionWindow(
        plan.history_start, plan.history_end - timedelta(seconds=600)
    )

    committed = await runtime.collect_observed_execution_window_typed_v1(
        session,
        repository=ledger,
        journal_repository=journal,
        observation_repository=typed,
        clock=now,
        barrier_completed_at=anchor - timedelta(seconds=2),
        window=window,
        expected_revision=0,
    )
    again = await AccountObservationTypedIndexRepository(database[1], clock=now).read(
        scope, after=0, limit=1
    )
    assert again == (committed,)
    assert json.loads(committed.replay.receipt_json)["schema_version"] == (
        "ctcc.observed_account_execution_window.v3"
    )
    assert committed.account_complete is committed.source_authenticity_verified is False
    assert committed.execution_authority is False and committed.admission == "DENY"
    assert session._used is True
    assert all(request.method == "GET" for request in harness.requests)
    harness.assert_closed()
