"""Isolated PostgreSQL V7 current index; all HTTP bytes are synthetic."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.database.models.account_current_observation import (
    DemoAccountCurrentObservation as Current,
)
from app.database.models.account_observation_index import DemoAccountObservationBatch
from app.database.repositories import account_current_observation as current_index
from app.database.repositories.account_capture_journal import (
    AccountCaptureJournalRepository,
)
from app.database.repositories.account_current_observation import (
    AccountCurrentObservationRepository,
    CurrentObservationError,
)
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.trade_qualification import account_bootstrap_runtime as bootstrap
from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.account_runtime import ControlledDemoAccountSession
from app.trade_qualification.reservations import LedgerScope
from tests.integration.test_durable_migration_downgrade import (
    migrate,
    shape,
)
from tests.integration.test_durable_migration_downgrade import (
    sandbox as sandbox,  # noqa: PLC0414 -- isolated child database fixture
)
from tests.integration.test_qualification_ledger_repository import (
    database as database,  # noqa: PLC0414 -- explicit isolated DB fixture
)
from tests.unit.test_account_current_source_v7 import (
    current_plan_v7,
    empty_v7_pages,
)
from tests.unit.test_qualification_account_capture import NOW, ms
from tests.unit.test_qualification_account_collector import Harness, credentials
from tests.unit.test_qualification_account_v4 import script

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def now():
    return datetime.now(UTC)


async def original_v7(monkeypatch, database):
    uid = f"123456789{uuid4().int % 10**12:012d}"
    anchor = now().replace(microsecond=0) - timedelta(seconds=10)
    shift_ms = int(ms(anchor)) - int(ms(NOW))

    def shift(value):
        if type(value) is list:
            return [shift(item) for item in value]
        if type(value) is dict:
            return {
                key: uid
                if key == "uid"
                else str(int(item) + shift_ms)
                if key in {"ts", "cTime", "uTime", "fillTime"}
                and type(item) is str
                and item.isdigit()
                else shift(item)
                for key, item in value.items()
            }
        return value

    selected = current_plan_v7(
        expected_uid=uid,
        created_at=anchor,
        history_start=anchor - timedelta(days=7),
        history_end=anchor - timedelta(seconds=1),
    )
    harness = Harness(
        monkeypatch,
        pages=empty_v7_pages(),
        change=lambda stream, index, data: shift(data),
    )
    harness.script = script(source=empty_v7_pages(), streams=capture.V7_CURRENT_STREAMS)
    harness.clock = now
    session = ControlledDemoAccountSession(
        credentials=credentials(session_binding_id=selected.session_binding_id),
        plan=selected,
        expected_plan_sha256=capture.plan_sha256(selected),
    )
    ledger = QualificationLedgerRepository(database[1], clock=now)
    original = AccountCaptureJournalRepository(database[1], clock=now)
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    await ledger.initialize_capture_scope(scope)
    result = await bootstrap.collect_bootstrap_recorded(
        session,
        repository=ledger,
        journal_repository=original,
        clock=now,
        barrier_completed_at=anchor - timedelta(seconds=2),
    )
    capture_id = json.loads(result.journal_receipt_json)["capture_id"]
    chain = await original.read_chain(scope, capture_id)
    harness.assert_closed()
    return scope, observed.source_reference(chain)


async def test_v7_current_append_readback_is_separate_from_history_and_idempotent(
    monkeypatch, database
):
    scope, reference = await original_v7(monkeypatch, database)
    repo = AccountCurrentObservationRepository(database[1], clock=now)
    first = await repo.append(scope, reference, expected_sequence=0)
    again = await repo.append(scope, reference, expected_sequence=0)
    assert first == again == await repo.read_one(scope, 1)
    assert first.account_complete is first.execution_authority is False
    assert first.admission == "DENY"
    assert (
        json.loads(first.receipt_json)["algo_type_coverage"][
            "all_eight_terminal_chains_replayed"
        ]
        is True
    )
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Current)
                .where(Current.account_id == scope.account_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DemoAccountObservationBatch)
                .where(DemoAccountObservationBatch.account_id == scope.account_id)
            )
            == 0
        )
    with pytest.raises(CurrentObservationError):
        await repo.append(scope, reference, expected_sequence=1)


async def test_v7_current_database_row_is_immutable(monkeypatch, database):
    scope, reference = await original_v7(monkeypatch, database)
    repo = AccountCurrentObservationRepository(database[1], clock=now)
    await repo.append(scope, reference, expected_sequence=0)
    async with database[1]() as session:
        with pytest.raises(DBAPIError):
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE demo_account_current_observations SET receipt_json='{}' WHERE capture_id=:capture_id"
                    ),
                    {"capture_id": reference.capture_id},
                )
    assert (await repo.read_one(scope, 1)).admission == "DENY"


async def test_v7_current_commit_survives_late_readback_failure(monkeypatch, database):
    scope, reference = await original_v7(monkeypatch, database)
    repo = AccountCurrentObservationRepository(database[1], clock=now)

    async def fail_readback(*args):
        raise RuntimeError("synthetic late failure")

    monkeypatch.setattr(repo, "read_one", fail_readback)
    with pytest.raises(CurrentObservationError):
        await repo.append(scope, reference, expected_sequence=0)
    fresh = AccountCurrentObservationRepository(database[1], clock=now)
    assert (await fresh.read_one(scope, 1)).admission == "DENY"


async def test_v7_current_two_workers_keep_one_durable_observation(
    monkeypatch, database
):
    scope, reference = await original_v7(monkeypatch, database)
    first, second = (
        AccountCurrentObservationRepository(database[1], clock=now) for _ in range(2)
    )
    results = await asyncio.gather(
        first.append(scope, reference, expected_sequence=0),
        second.append(scope, reference, expected_sequence=0),
    )
    assert results[0] == results[1]
    async with database[1]() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Current)
                .where(Current.account_id == scope.account_id)
            )
            == 1
        )


async def test_db0038_replaces_caller_time_with_native_database_receipt(
    monkeypatch, database
):
    scope, reference = await original_v7(monkeypatch, database)
    journal = AccountCaptureJournalRepository(database[1], clock=now)
    chain = await journal.read_chain(scope, reference.capture_id)
    document, receipt = current_index._derive(
        chain,
        reference,
        scope,
        sequence=1,
        previous_sha256=None,
        validated_at=now(),
    )
    forged = datetime(2099, 1, 1, tzinfo=UTC)
    async with database[1]() as session, session.begin():
        session.add(
            Current(
                environment=scope.environment,
                account_id=scope.account_id,
                settlement_currency=scope.settlement_currency,
                sequence=1,
                capture_id=reference.capture_id,
                source_sequence=len(chain),
                previous_sha256=None,
                event_sha256=current_index.journal.digest(document),
                document_json=document.decode("utf-8"),
                receipt_json=receipt.decode("utf-8"),
                db_recorded_at=forged,
            )
        )
    async with database[1]() as session:
        recorded_at = await session.scalar(
            select(Current.db_recorded_at).where(
                Current.capture_id == reference.capture_id
            )
        )
    assert chain[-1].db_recorded_at <= recorded_at < forged
    assert (
        await AccountCurrentObservationRepository(database[1], clock=now).read_one(
            scope, 1
        )
    ).execution_authority is False


@pytest.mark.parametrize("revision", ["0038"])
async def test_db0038_empty_downgrade_reupgrade_preserves_schema(sandbox, revision):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == before


@pytest.mark.parametrize("revision", ["0038"])
async def test_db0038_nonempty_downgrade_refuses_data_loss(
    monkeypatch, sandbox, revision
):
    scope, reference = await original_v7(monkeypatch, sandbox)
    saved = await AccountCurrentObservationRepository(sandbox[1], clock=now).append(
        scope, reference, expected_sequence=0
    )
    async with sandbox[0].begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.run_sync(migrate, revision, "downgrade")
    assert (
        await AccountCurrentObservationRepository(sandbox[1], clock=now).read_one(
            scope, 1
        )
    ) == saved
