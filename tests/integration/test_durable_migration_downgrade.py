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
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.qualification_ledger import (
    QualificationAccountScope,
    QualificationReservation,
    QualificationReservationTransition,
)
from app.database.repositories.account_bill_archive_claim import (
    AccountBillArchiveClaimRepository,
)
from app.database.repositories.demo_control import DemoControlRepository
from app.database.repositories.submission_reporting import SubmissionReportingRepository
from app.trade_evidence import outbox
from app.trade_evidence.submission_reporting import CapturedSubmission
from app.trade_qualification.account_bill_archive_acquisition import (
    DiagnosticArchivePlan,
)
from app.trade_qualification.demo_control import ControlScope
from app.trade_qualification.reservations import (
    QualificationLedgerError,
    reservation_id,
)
from tests.durable_migration_fixtures import (
    DOWNGRADE_LOCKS,
    TABLES,
    RecordedDowngrade,
    load_migration,
)
from tests.integration import (
    test_account_ingestion_journal_repository as capture_fixtures,
)
from tests.integration import test_control_bound_ledger_repository as control_fixtures
from tests.integration import test_qualification_ledger_repository as ledger_fixtures
from tests.integration import test_submission_reporting_repository as report_fixtures
from tests.unit.qualification_execution_binding_fixtures import execution_binding
from tests.unit.qualification_ledger_fixtures import ledger_fixture
from tests.unit.qualification_range_v5_fixtures import range_v5_ledger_fixture
from tests.unit.test_demo_control import Clock

database = ledger_fixtures.database
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def assert_event_state_preserved(before, after):
    """A second DB read has fresh receipt times but must retain ledger state."""
    assert after.scope == before.scope
    assert after.original_event_key == before.original_event_key
    assert after.account_revision == before.account_revision
    assert after.ledger_revision == before.ledger_revision
    assert after.matched == before.matched
    assert after.request_started_at <= after.observed_at <= after.received_at
    assert after.monotonic_started_ns <= after.monotonic_received_ns
    assert after.monotonic_started_ns >= before.monotonic_started_ns


def migrate(connection, revision, direction):
    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


async def migrate_public_qualified_guard_in_sandbox(connection, direction):
    """Run 0027's actual SQL with public mapped only to disposable test schema."""

    class RecordedOperations:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(str(statement))

    module = load_migration("0027")
    recorded = RecordedOperations()
    module.op = recorded
    getattr(module, direction)()
    schema = (await connection.execute(text("SELECT current_schema()"))).scalar_one()
    assert re.fullmatch(r"ctcc_migration_test_[a-f0-9]{32}", schema)
    assert recorded.statements and all("public." in sql for sql in recorded.statements)
    for sql in recorded.statements:
        await connection.execute(text(sql.replace("public.", f'"{schema}".')))


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


@pytest.mark.parametrize("revision", ("0024",))
async def test_control_bound_reporting_function_downgrade_reupgrade_when_empty(
    sandbox, revision
):
    engine, _ = sandbox
    prior = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "upgrade")
    current = await shape(engine)
    assert current != prior
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "downgrade")
    assert await shape(engine) == prior
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "upgrade")
    assert await shape(engine) == current


@pytest.mark.parametrize("revision", ("0017",))
async def test_post_submit_closure_guard_empty_downgrade_restores_exact_shape(
    sandbox, revision
):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    guarded = await shape(engine)
    assert guarded != before
    async with engine.begin() as connection:
        await migrate_public_qualified_guard_in_sandbox(connection, "downgrade")
    assert await shape(engine) == before
    async with engine.begin() as connection:
        await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    assert await shape(engine) == guarded


@pytest.mark.parametrize("revision", ("0017",))
async def test_post_submit_closure_guard_refuses_nonempty_rollback_and_direct_update(
    sandbox, revision, durable_fixture
):
    engine, sessions = sandbox
    async with engine.begin() as connection:
        await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    repo, clock = await ledger_fixtures.initialize(sandbox, durable_fixture)
    hold = await repo.reserve(durable_fixture.request)
    await repo.consume_with_submission_intent(
        hold.scope, hold.original_event_key, expected_revision=2
    )
    before = await repo.read_scope(hold.scope)
    assert before.active[0].state == "consumed"
    clock.value += timedelta(seconds=1)
    claims = ledger_fixtures.refresh(durable_fixture.claims, clock.value)
    with pytest.raises(
        QualificationLedgerError, match="post_submit_closure_witness_missing"
    ):
        await repo.reconcile_reservation(
            hold.scope,
            hold.original_event_key,
            claims=claims,
            expected_revision=3,
        )
    with pytest.raises(DBAPIError, match="qualification_reservation_update_denied"):
        async with sessions() as session, session.begin():
            await session.execute(
                update(QualificationReservation)
                .where(QualificationReservation.reservation_id == hold.reservation_id)
                .values(
                    state="reconciled_flat",
                    state_revision=3,
                    updated_at=clock.value,
                )
            )
    guarded = await shape(engine)
    with pytest.raises(
        DBAPIError, match="post_submit_closure_guard_downgrade_requires_empty"
    ):
        async with engine.begin() as connection:
            await migrate_public_qualified_guard_in_sandbox(connection, "downgrade")
    assert await shape(engine) == guarded
    assert await repo.read_scope(hold.scope) == before


@pytest.mark.parametrize("revision", ("0017",))
@pytest.mark.parametrize("legacy_state", ("consumed", "uncertain"))
async def test_post_submit_closure_guard_upgrade_refuses_legacy_terminal(
    sandbox, revision, durable_fixture, legacy_state
):
    engine, sessions = sandbox
    repo, clock = await ledger_fixtures.initialize(sandbox, durable_fixture)
    hold = await repo.reserve(durable_fixture.request)
    await repo.consume_with_submission_intent(
        hold.scope, hold.original_event_key, expected_revision=2
    )
    if legacy_state == "uncertain":
        await repo.mark_uncertain(
            hold.scope, hold.original_event_key, expected_revision=3
        )
    before = await repo.read_scope(hold.scope)
    assert before.active[0].state == legacy_state
    clock.value += timedelta(seconds=1)
    # Synthesize a pre-0027 terminal transition using the actual DB0017 SQL
    # guard and immutable journal. The current repository correctly denies it.
    async with sessions() as session, session.begin():
        record = await session.get(QualificationReservation, hold.reservation_id)
        scope = await session.get(
            QualificationAccountScope,
            (
                hold.scope.environment,
                hold.scope.account_id,
                hold.scope.settlement_currency,
            ),
        )
        assert record is not None and scope is not None
        record.state = "reconciled_flat"
        record.state_revision += 1
        record.updated_at = clock.value
        scope.ledger_revision += 1
        scope.updated_at = clock.value
        session.add(
            QualificationReservationTransition(
                reservation_id=hold.reservation_id,
                state_revision=record.state_revision,
                from_state=legacy_state,
                to_state="reconciled_flat",
                reason_code="synthetic_legacy_caller_flat",
                evidence_json=None,
                occurred_at=clock.value,
            )
        )
    legacy_shape = await shape(engine)
    legacy = await repo.read_event_observation(hold.scope, hold.original_event_key)
    assert legacy.matched is not None and legacy.matched.state == "reconciled_flat"
    with pytest.raises(
        DBAPIError, match="post_submit_closure_legacy_terminal_unresolved"
    ):
        async with engine.begin() as connection:
            await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    assert await shape(engine) == legacy_shape
    assert_event_state_preserved(
        legacy, await repo.read_event_observation(hold.scope, hold.original_event_key)
    )
    async with sessions() as session:
        transitions = (
            await session.scalars(
                select(QualificationReservationTransition)
                .filter_by(reservation_id=hold.reservation_id)
                .order_by(QualificationReservationTransition.state_revision)
            )
        ).all()
        assert [item.to_state for item in transitions] == (
            ["reserved", "consumed", "reconciled_flat"]
            if legacy_state == "consumed"
            else ["reserved", "consumed", "uncertain", "reconciled_flat"]
        )


@pytest.mark.parametrize("revision", ("0017",))
async def test_post_submit_closure_guard_upgrade_refuses_reserved_only_terminal(
    sandbox, revision, durable_fixture
):
    engine, _ = sandbox
    repo, clock = await ledger_fixtures.initialize(sandbox, durable_fixture)
    hold = await repo.reserve(durable_fixture.request)
    clock.value += timedelta(seconds=1)
    terminal = await repo.reconcile_reservation(
        hold.scope,
        hold.original_event_key,
        claims=ledger_fixtures.refresh(
            durable_fixture.claims, clock.value, armed=False
        ),
        expected_revision=2,
    )
    assert terminal.state == "reconciled_flat"
    before = await shape(engine)
    observation = await repo.read_event_observation(hold.scope, hold.original_event_key)
    with pytest.raises(
        DBAPIError, match="post_submit_closure_legacy_terminal_unresolved"
    ):
        async with engine.begin() as connection:
            await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    assert await shape(engine) == before
    assert_event_state_preserved(
        observation,
        await repo.read_event_observation(hold.scope, hold.original_event_key),
    )


@pytest.mark.parametrize("revision", ("0017",))
async def test_post_submit_closure_guard_upgrade_refuses_incomplete_legacy_journal(
    sandbox, revision, durable_fixture
):
    engine, sessions = sandbox
    repo, clock = await ledger_fixtures.initialize(sandbox, durable_fixture)
    hold = await repo.reserve(durable_fixture.request)
    scope_key = (
        hold.scope.environment,
        hold.scope.account_id,
        hold.scope.settlement_currency,
    )
    # The old SQL trigger permits both state changes even when a direct writer
    # omits the consumed journal. No migration may treat that omission as proof
    # of a never-submitted cancellation.
    for state, state_revision in (("consumed", 2), ("reconciled_flat", 3)):
        clock.value += timedelta(seconds=1)
        async with sessions() as session, session.begin():
            record = await session.get(QualificationReservation, hold.reservation_id)
            scope = await session.get(QualificationAccountScope, scope_key)
            assert record is not None and scope is not None
            record.state = state
            record.state_revision = state_revision
            record.updated_at = clock.value
            scope.ledger_revision += 1
            scope.updated_at = clock.value
            if state == "reconciled_flat":
                session.add(
                    QualificationReservationTransition(
                        reservation_id=hold.reservation_id,
                        state_revision=state_revision,
                        from_state="consumed",
                        to_state="reconciled_flat",
                        reason_code="synthetic_incomplete_legacy_journal",
                        evidence_json=None,
                        occurred_at=clock.value,
                    )
                )
    async with sessions() as session:
        transitions = (
            await session.scalars(
                select(QualificationReservationTransition)
                .filter_by(reservation_id=hold.reservation_id)
                .order_by(QualificationReservationTransition.state_revision)
            )
        ).all()
        assert [item.to_state for item in transitions] == [
            "reserved",
            "reconciled_flat",
        ]
    before = await shape(engine)
    terminal = await repo.read_event_observation(hold.scope, hold.original_event_key)
    assert terminal.matched is not None and terminal.matched.state == "reconciled_flat"
    with pytest.raises(
        DBAPIError, match="post_submit_closure_legacy_terminal_unresolved"
    ):
        async with engine.begin() as connection:
            await migrate_public_qualified_guard_in_sandbox(connection, "upgrade")
    assert await shape(engine) == before
    assert_event_state_preserved(
        terminal, await repo.read_event_observation(hold.scope, hold.original_event_key)
    )


@pytest.fixture
def v2_reporting_fixture():
    # ledger_fixture uses asyncio.run, so construct it before the async test.
    return ledger_fixture(
        account_id=f"456789{uuid4().int % 10**14:014d}",
        report_id="synthetic-migration-v2-" + uuid4().hex,
    )


@pytest.mark.parametrize("revision", ("0024",))
async def test_control_bound_reporting_downgrade_preserves_existing_v2_outcome(
    sandbox, revision, v2_reporting_fixture
):
    engine, _ = sandbox
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "upgrade")
    fixture = v2_reporting_fixture
    _ledger, reporter, _clock, hold, args = await report_fixtures.setup(
        sandbox, fixture
    )
    observed = await reporter.record_observation(
        hold.scope, hold.original_event_key, **args
    )
    assert observed.status == "acknowledged"
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "downgrade")
    assert (
        await reporter.read_observation(
            hold.scope,
            hold.original_event_key,
            expected_outcome_id=observed.outcome_id,
        )
        == observed
    )
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "upgrade")
    assert (
        await reporter.read_observation(
            hold.scope,
            hold.original_event_key,
            expected_outcome_id=observed.outcome_id,
        )
        == observed
    )


@pytest.fixture
def control_bound_range_fixture(monkeypatch):
    # Build the synthetic source before the async test enters its event loop.
    return range_v5_ledger_fixture(
        "long", monkeypatch, account_id=f"456789{uuid4().int % 10**14:014d}"
    )[0]


@pytest.mark.parametrize("revision", ("0024",))
async def test_control_bound_reporting_downgrade_rejects_retained_v3_outcome(
    sandbox, revision, control_bound_range_fixture
):
    engine, _ = sandbox
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0025", "upgrade")
    fixture = control_bound_range_fixture
    setup = await control_fixtures.setup(sandbox, fixture)
    hold = await setup.reserve()
    outer = await setup.consume()
    body = json.loads(json.loads(outer.canonical_json)["intent_json"])
    setup.clock.value += timedelta(milliseconds=1)
    capture = CapturedSubmission(
        exchange_request_sha256=body["exchange_request_sha256"],
        request_started_at=fixture.now,
        headers_received_at=fixture.now,
        body_completed_at=setup.clock.value,
        completed_at=setup.clock.value,
        http_status=200,
        raw_body=json.dumps(
            {
                "code": "0",
                "data": [
                    {
                        "sCode": "0",
                        "ordId": "99887766",
                        "clOrdId": body["client_order_id"],
                    }
                ],
            }
        ).encode(),
        transport_complete=True,
    )
    reporter = SubmissionReportingRepository(sandbox[1], clock=setup.clock)
    observed = await reporter.record_observation(
        hold.scope,
        hold.original_event_key,
        expected_revision=3,
        intent_sha256=outer.sha256,
        capture=capture,
        queue_namespace="ctcc-migration-v3-" + uuid4().hex[:16],
        outbox_policy=outbox.OutboxPolicy(),
    )
    assert observed.intent_sha256 == outer.sha256
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="control_bound_reporting_downgrade_requires_no_outcomes"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0025", "downgrade")
    assert await shape(engine) == before
    assert (
        await reporter.read_observation(
            hold.scope,
            hold.original_event_key,
            expected_outcome_id=observed.outcome_id,
        )
        == observed
    )


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


@pytest.mark.parametrize("revision", ["0022"])
async def test_uid_event_upgrade_refuses_legacy_cross_currency_collision(
    sandbox, durable_fixture
):
    engine, sessions = sandbox
    fixture = durable_fixture
    repo, _ = await ledger_fixtures.initialize(sandbox, fixture)
    receipt = await repo.reserve(fixture.request)
    other_scope = receipt.scope.model_copy(update={"settlement_currency": "USDC"})
    async with sessions() as session, session.begin():
        await repo._locked(session, other_scope, create=True)
        original = await session.get(QualificationReservation, receipt.reservation_id)
        values = {
            column.name: getattr(original, column.name)
            for column in QualificationReservation.__table__.columns
        }
        values.update(
            reservation_id=reservation_id(other_scope, receipt.original_event_key),
            settlement_currency="USDC",
        )
        session.add(QualificationReservation(**values))
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="qualification_uid_event_duplicates_preexisting"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0023", "upgrade")
    assert await shape(engine) == before
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(QualificationReservation).filter_by(
                    environment="demo",
                    account_id=receipt.scope.account_id,
                    original_event_key=receipt.original_event_key,
                )
            )
        ).all()
        assert {row.settlement_currency for row in rows} == {"USDT", "USDC"}


@pytest.mark.parametrize("revision", ["0022"])
async def test_uid_event_upgrade_fails_closed_if_writer_holds_table(sandbox):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as writer:
        await writer.execute(
            text("LOCK TABLE qualification_reservations IN ROW EXCLUSIVE MODE")
        )
        with pytest.raises(DBAPIError) as denied:
            async with engine.begin() as migration:
                await migration.run_sync(migrate, "0023", "upgrade")
        assert denied.value.orig.sqlstate == "55P03"  # lock_not_available
        assert await shape(engine) == before
    async with engine.begin() as migration:
        await migration.run_sync(migrate, "0023", "upgrade")
    assert await shape(engine) != before


@pytest.mark.parametrize("revision", ["0022"])
async def test_uid_event_upgrade_downgrade_and_truncate_guards_are_atomic(sandbox):
    engine, _ = sandbox
    before = await shape(engine)
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0023", "upgrade")
    upgraded = await shape(engine)
    assert upgraded != before
    constraints = upgraded[1]
    assert any(
        name == "uq_qualification_reservations_uid_event"
        and "UNIQUE (environment, account_id, original_event_key)" in definition
        for _, name, definition in constraints
    )
    for table in (
        "qualification_account_scopes",
        "qualification_reservations",
        "qualification_reservation_transitions",
    ):
        with pytest.raises(DBAPIError, match="qualification_ledger_immutable"):
            async with engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        assert await shape(engine) == upgraded
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0023", "downgrade")
    assert await shape(engine) == before
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0023", "upgrade")
    assert await shape(engine) == upgraded


@pytest.mark.parametrize("revision", ["0022"])
async def test_uid_event_downgrade_retains_reconciled_tombstone(
    sandbox, durable_fixture
):
    engine, _ = sandbox
    async with engine.begin() as connection:
        await connection.run_sync(migrate, "0023", "upgrade")
    repo, clock = await ledger_fixtures.initialize(sandbox, durable_fixture)
    receipt = await repo.reserve(durable_fixture.request)
    clock.value += timedelta(microseconds=1)
    terminal = await repo.reconcile_reservation(
        receipt.scope,
        receipt.original_event_key,
        claims=ledger_fixtures.refresh(durable_fixture.claims, clock.value),
        expected_revision=2,
    )
    before = await shape(engine)
    with pytest.raises(
        DBAPIError, match="qualification_uid_event_downgrade_requires_empty"
    ):
        async with engine.begin() as connection:
            await connection.run_sync(migrate, "0023", "downgrade")
    assert await shape(engine) == before
    assert (
        await repo.read_event_observation(receipt.scope, receipt.original_event_key)
    ).matched == terminal
