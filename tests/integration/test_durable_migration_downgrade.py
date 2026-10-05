"""Actual PostgreSQL DDL/lock/retention tests in disposable synthetic schemas.

Requires an explicitly supplied isolated DATABASE_URL. No SQLite substitute,
exchange connection, host restart, or deployed schema change is performed.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.account_bill_archive_claim import (
    AccountBillArchiveClaimRepository,
)
from app.database.repositories.demo_control import DemoControlRepository
from app.trade_qualification.account_bill_archive_acquisition import (
    DiagnosticArchivePlan,
)
from app.trade_qualification.demo_control import ControlScope
from tests.durable_migration_fixtures import (
    DOWNGRADE_LOCKS,
    TABLES,
    RecordedDowngrade,
    load_migration,
)
from tests.integration import (
    test_account_ingestion_journal_repository as capture_fixtures,
)
from tests.integration import test_qualification_ledger_repository as ledger_fixtures
from tests.integration import test_submission_reporting_repository as report_fixtures
from tests.unit.qualification_execution_binding_fixtures import execution_binding
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.test_demo_control import Clock

database = ledger_fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate(connection, revision, direction):
    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


@pytest.fixture
async def sandbox(database, revision):
    admin = database[0]
    assert admin.dialect.name == "postgresql"
    schema = "ctcc_migration_test_" + uuid4().hex
    assert re.fullmatch(r"ctcc_migration_test_[a-f0-9]{32}", schema)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        admin.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f'"{schema}",pg_catalog'}},
    )
    try:
        async with engine.begin() as connection:
            for current in TABLES:
                if current <= revision:
                    await connection.run_sync(migrate, current, "upgrade")
        yield (
            engine,
            async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
        )
    finally:
        await engine.dispose()
        # Only this generated synthetic namespace is disposable. Never use the
        # application's public tables for destructive migration acceptance.
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


async def shape(engine):
    async with engine.connect() as connection:
        columns = await connection.execute(
            text("""
                SELECT table_name,column_name,data_type,is_nullable,column_default
                FROM information_schema.columns WHERE table_schema=current_schema()
                ORDER BY table_name,ordinal_position
            """)
        )
        constraints = await connection.execute(
            text("""
                SELECT c.relname,k.conname,pg_get_constraintdef(k.oid)
                FROM pg_constraint k JOIN pg_class c ON k.conrelid=c.oid
                JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname=current_schema() ORDER BY c.relname,k.conname
            """)
        )
        indexes = await connection.execute(
            text("""
                SELECT tablename,indexname,indexdef FROM pg_indexes
                WHERE schemaname=current_schema() ORDER BY tablename,indexname
            """)
        )
        triggers = await connection.execute(
            text("""
                SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled
                FROM pg_trigger t JOIN pg_class c ON t.tgrelid=c.oid
                JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname=current_schema() AND NOT t.tgisinternal
                ORDER BY c.relname,t.tgname
            """)
        )
        functions = await connection.execute(
            text("""
                SELECT p.proname,pg_get_function_identity_arguments(p.oid),
                       pg_get_functiondef(p.oid)
                FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
                WHERE n.nspname=current_schema() AND p.prokind='f'
                ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)
            """)
        )
        return (
            columns.all(),
            constraints.all(),
            indexes.all(),
            triggers.all(),
            functions.all(),
        )


@pytest.mark.parametrize("revision", TABLES)
async def test_empty_actual_migration_downgrade_and_reupgrade_preserve_shape(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "downgrade")
    remaining = {row[0] for row in (await shape(engine))[0]}
    assert not remaining.intersection(TABLES[revision])
    async with engine.begin() as connection:
        await connection.run_sync(migrate, revision, "upgrade")
    assert await shape(engine) == before


LOCK_CASES = [
    (revision, table) for revision in TABLES for table in DOWNGRADE_LOCKS[revision]
]


@pytest.mark.parametrize("revision,table", LOCK_CASES)
async def test_active_writer_denies_downgrade_without_partial_drop(
    sandbox, revision, table
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as writer:
        await writer.execute(text(f'LOCK TABLE "{table}" IN ROW EXCLUSIVE MODE'))
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as migration:
                await migration.execute(text("SET LOCAL statement_timeout='2000ms'"))
                await migration.run_sync(migrate, revision, "downgrade")
        assert denied.value.orig.sqlstate == "55P03"  # lock_not_available
    assert await shape(engine) == before


@pytest.mark.parametrize("revision", TABLES)
async def test_writer_cannot_enter_between_empty_guard_and_drop(sandbox, revision):
    engine, _ = sandbox
    commands = RecordedDowngrade(revision).commands
    async with engine.begin() as migration:
        assert all(kind == "execute" for kind, _ in commands[:2])
        for _, statement in commands[:2]:
            await migration.execute(text(statement))
        # A writer starting after the guard must not acquire the table lock
        # required for INSERT. This is an actual separate PG transaction.
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as writer:
                await writer.execute(text("SET LOCAL lock_timeout='200ms'"))
                await writer.execute(
                    text(f'LOCK TABLE "{TABLES[revision][0]}" IN ROW EXCLUSIVE MODE')
                )
        assert denied.value.orig.sqlstate == "55P03"
        for kind, statement in commands[2:]:
            await migration.execute(
                text(f'DROP TABLE "{statement}"' if kind == "drop" else statement)
            )
    remaining = {row[0] for row in (await shape(engine))[0]}
    assert not remaining.intersection(TABLES[revision])


@pytest.fixture
def durable_fixture():
    # This builder owns asyncio.run; pytest resolves it outside the async test.
    return ledger_fixture(account_id=f"456789{uuid4().int % 10**14:014d}")


@pytest.mark.parametrize(
    "revision,record_type",
    [
        ("0017", "scope"),
        ("0017", "intent"),
        ("0018", "outcome"),
        ("0019", "stop"),
        ("0020", "capture_start"),
        ("0020", "capture_raw"),
        ("0022", "archive_claim"),
    ],
)
async def test_nonempty_downgrade_retains_exact_durable_records(
    sandbox, revision, record_type, durable_fixture
):
    engine, sessions = sandbox
    fixture = durable_fixture
    if record_type == "archive_claim":
        scope, _, _, _ = await capture_fixtures.initialize(sandbox)
        archive_repo = AccountBillArchiveClaimRepository(
            sessions, clock=lambda: datetime.now(UTC)
        )
        archive_plan = DiagnosticArchivePlan(
            expected_uid=scope.account_id,
            expected_main_uid=scope.account_id,
            session_binding_id="synthetic-archive-claim",
            registration_region="us_au",
            origin="https://us.okx.com",
            registration_evidence_sha256="a" * 64,
            year=2024,
            quarter=2,
            created_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        claimed = await archive_repo.claim_once(scope, archive_plan)
        claim_data = json.loads(claimed.receipt_json)
        expected = claim_data["claim_sha256"]

        async def readback():
            current = await archive_repo.read_claim(
                scope,
                year=archive_plan.year,
                quarter=archive_plan.quarter,
                expected_plan_sha256=claim_data["plan_sha256"],
                expected_claim_sha256=expected,
            )
            return json.loads(current.receipt_json)["claim_sha256"]

    elif record_type.startswith("capture_"):
        scope, ledger, repo, start = await capture_fixtures.initialize(sandbox)
        checkpoint = await ledger.read_bootstrap_checkpoint(scope)
        first = await repo._append(scope, start)
        capture_id = capture_fixtures.journal.checked_event(start)["capture_id"]
        if record_type == "capture_raw":
            raw = b'{"code":"0","data":[]}'
            await repo._append(
                scope,
                capture_fixtures.event(
                    capture_id,
                    2,
                    capture_fixtures.journal.digest(first.event.event_json),
                    "raw_finalized",
                    {
                        "raw_retention": "durable_secret_checked",
                        "observed_bytes": len(raw),
                        "source_state": "complete_page",
                    },
                    raw=raw,
                ),
            )

        async def readback():
            # Re-reading changes the observation time, never the committed event,
            # raw bytes, DB timestamp, or untouched qualification checkpoint.
            chain = await repo.read_chain(scope, capture_id)
            current = await ledger.read_bootstrap_checkpoint(scope)
            assert current.state == checkpoint.state
            return tuple((item.event, item.db_recorded_at) for item in chain)

        expected = await readback()
    elif record_type == "stop":
        scope = ControlScope("demo", fixture.request.scope.account_id)
        repo = DemoControlRepository(sessions, clock=Clock())
        expected = await repo.latch_stop(scope, command_id=uuid4().hex * 2)

        async def readback():
            return await repo.read(scope)

    elif record_type == "outcome":
        _, repo, _, hold, args = await report_fixtures.setup(sandbox, fixture)
        expected = await repo.record_observation(
            hold.scope, hold.original_event_key, **args
        )

        async def readback():
            return await repo.read_observation(
                hold.scope,
                hold.original_event_key,
                expected_outcome_id=expected.outcome_id,
            )

    else:
        repo, _ = await ledger_fixtures.initialize(sandbox, fixture)
        if record_type == "scope":
            expected = await repo.read_scope(fixture.request.scope)

            async def readback():
                return await repo.read_scope(fixture.request.scope)

        else:
            hold = await repo.reserve(fixture.request)
            expected = await repo.consume_with_submission_intent(
                hold.scope,
                hold.original_event_key,
                expected_revision=2,
                execution_binding=execution_binding(fixture),
            )

            async def readback():
                return await repo.read_submission_intent(
                    hold.scope,
                    hold.original_event_key,
                    expected_sha256=expected.sha256,
                )

    before = await shape(engine)
    with pytest.raises(DBAPIError, match="downgrade_requires_empty"):
        async with engine.begin() as migration:
            await migration.run_sync(migrate, revision, "downgrade")
    assert await shape(engine) == before
    assert await readback() == expected
