"""Actual isolated PostgreSQL readback with synthetic owned HTTP acquisition.

No authenticated/network exchange access. Requires migrated explicit DATABASE_URL.
These cases are not an account-history or native TLS acceptance certificate.
"""

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
from app.trade_qualification import account_history_query_verifier as verifier
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit pytest fixture re-export
)
from tests.unit.test_qualification_account_capture import NOW, ms
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_materializer import source_pages
from tests.unit.test_qualification_account_runtime import regional

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def now():
    return datetime.now(UTC)


async def persisted_capture(monkeypatch, database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    current = now()
    anchor = current.replace(
        microsecond=current.microsecond // 1000 * 1000
    ) - timedelta(seconds=10)
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
        change=lambda stream, index, data: shift(data),
    )
    harness.clock = now
    selected = regional(
        expected_uid=uid,
        created_at=anchor,
        history_start=anchor - timedelta(days=7),
        history_end=anchor - timedelta(seconds=1),
    )
    session = ControlledDemoAccountSession(
        credentials=credentials(),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    ledger = QualificationLedgerRepository(database[1], clock=now)
    repo = AccountCaptureJournalRepository(database[1], clock=now)
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    before = await ledger.initialize_capture_scope(scope)
    result = await bootstrap.collect_bootstrap_query_verified(
        session,
        repository=ledger,
        journal_repository=repo,
        clock=now,
        barrier_completed_at=anchor - timedelta(seconds=2),
    )
    receipt = json.loads(result.receipt_json)
    chain = await AccountCaptureJournalRepository(database[1], clock=now).read_chain(
        scope, receipt["capture_id"]
    )
    pins = {
        "expected_head_sha256": journal.digest(chain[-1].event.event_json),
        "expected_plan_sha256": session._pin,
        "expected_packet_sha256": capture.freeze_demo_account_packet(
            result.recorded.bootstrap.packet, expected_plan_sha256=session._pin
        ).sha256,
        "expected_account_id": uid,
        "expected_settlement_currency": "USDT",
    }
    assert (await ledger.read_bootstrap_checkpoint(scope)).state == before.state
    harness.assert_closed()
    return result, chain, pins, repo, scope


async def test_b2_independent_postgresql_readback_replays_exact_bytes(
    monkeypatch, database
):
    result, chain, pins, _, _ = await persisted_capture(monkeypatch, database)
    assert (
        verifier.verify_history_query_chain(chain, **pins) == result.query_verification
    )
    assert result.account_complete is result.execution_authority is False
    assert all(item.db_recorded_at <= item.readback_at for item in chain)
    assert result.recorded.bootstrap.transport_provenance == "synthetic_transport"


async def test_b2_incomplete_read_view_preserves_postgresql_journal(
    monkeypatch, database
):
    _, chain, pins, repo, scope = await persisted_capture(monkeypatch, database)
    with pytest.raises(verifier.HistoryQueryVerificationError):
        verifier.verify_history_query_chain(chain[:-1], **pins)
    reloaded = await repo.read_chain(
        scope, journal.checked_event(chain[0].event)["capture_id"]
    )
    assert [item.event for item in reloaded] == [item.event for item in chain]
