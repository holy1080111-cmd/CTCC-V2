"""Prepared genuine PostgreSQL migration/locks/retention tests; never orders.

All source and account records seeded here are explicitly synthetic. Real SQL
can validate custody and bootstrapping, not native account/history authenticity.
"""

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models.qualification_ledger import QualificationAccountScope
from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import controlled_writer_coverage_catalog_v1 as previous
from app.domain import prospective_event_cutover_catalog_v2 as catalog
from app.domain import prospective_event_cutover_v2 as records
from app.trade_qualification.reservations import LedgerScope, QualificationLedgerError
from tests.controlled_writer_coverage_fixtures import (
    coverage_record_fixture,
    seed_controlled_writer_coverage,
)
from tests.integration.test_controlled_writer_coverage_repository import _hermetic_url
from tests.integration.test_qualification_ledger_repository import refresh
from tests.prospective_cutover_fixtures import cutover_record_fixture, stamp
from tests.unit.qualification_ledger_fixtures import ledger_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]


def _migrate(connection, target, direction):
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0051"
    resolver = (
        script._upgrade_revs if direction == "upgrade" else script._downgrade_revs
    )
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: resolver(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@asynccontextmanager
async def _owned_child(parent):
    name, role = "ctcc_cwc51_" + uuid4().hex, "ctcc_cwc51_" + uuid4().hex
    assert re.fullmatch(r"ctcc_cwc51_[a-f0-9]{32}", name)
    assert re.fullmatch(r"ctcc_cwc51_[a-f0-9]{32}", role)
    child = restricted = None
    created_database = created_role = False
    try:
        async with parent.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            assert await connection.scalar(text("SELECT current_database()")) == "ctcc"
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created_database = True
        child = create_async_engine(parent.url.set(database=name), poolclass=NullPool)
        async with child.begin() as connection:
            await connection.run_sync(_migrate, "0051", "upgrade")
            await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
            created_role = True
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            signatures = {
                "controlled_writer_coverage_v1_append": "text,text,bigint,text,bytea,bytea,bytea",
                "controlled_writer_coverage_v1_read": "text,text,bigint,text,text",
                **{
                    key: value
                    for key, value in catalog.FUNCTION_SIGNATURES.items()
                    if key.endswith(("_append", "_read"))
                },
            }
            for function, signature in signatures.items():
                await connection.execute(
                    text(
                        f'GRANT EXECUTE ON FUNCTION public.{function}({signature}) TO "{role}"'
                    )
                )
        restricted = create_async_engine(
            parent.url.set(database=name, username=role), poolclass=NullPool
        )
        yield child, restricted, role
    finally:
        if restricted is not None:
            await restricted.dispose()
        if child is not None:
            await child.dispose()
        if created_database:
            async with parent.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                assert (
                    await connection.scalar(text("SELECT current_database()")) == "ctcc"
                )
                assert parent.url.database != name
                await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        if created_role:
            async with parent.begin() as connection:
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))


@pytest.fixture
async def child():
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip("explicit isolated migrated DATABASE_URL required")
    parent = create_async_engine(_hermetic_url(raw), poolclass=NullPool)
    try:
        async with parent.connect() as connection:
            assert (
                170000
                <= await connection.scalar(
                    text("SELECT current_setting('server_version_num')::int")
                )
                < 180000
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0051"
            )
        async with _owned_child(parent) as value:
            yield value
    finally:
        await parent.dispose()


async def _settings(connection):
    for sql in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL synchronous_commit='on'",
    ):
        await connection.execute(text(sql))


async def _scan_in_transaction(sessions, scope, clock):
    ledger = QualificationLedgerRepository(sessions, clock=clock)
    async with sessions() as session, session.begin():
        await _settings(session)
        selected = await ledger._locked(session, scope)
        scan = await ledger._inspect_prospective_event_journal_locked(
            session, scope, selected
        )
    return scan


async def _synthetic_cutover_raw(admin, restricted):
    """Valid original SQL/control lineage, with explicitly synthetic account proof."""
    uid = "730051" + str(uuid4().int % 10**12).zfill(12)
    async with admin.begin() as connection:
        prepared = await seed_controlled_writer_coverage(connection, uid=uid)
        await connection.execute(
            text("""
          INSERT INTO public.qualification_account_scopes(environment,account_id,settlement_currency,account_revision,ledger_revision)
          VALUES('demo',:uid,'USDT',0,0)
        """),
            {"uid": uid},
        )
    state = dict(prepared["state"])
    now = datetime.now(UTC)
    ns = stamp(now, 0)["utc_ns"]
    state.update(
        state="active",
        journal_sequence=2,
        updated_at=now.isoformat(),
        transition_stamp={
            "utc_ns": ns,
            "monotonic_ns": state["transition_stamp"]["monotonic_ns"] + 1_000_000,
        },
    )
    active = coverage_record_fixture(
        uid=uid,
        now=now,
        state_overrides=state,
        previous_sha256=prepared["event_sha256"],
        audit_raw=prepared["audit_raw"],
    )
    async with restricted.begin() as connection:
        await _settings(connection)
        await connection.scalar(
            text("""
          SELECT public.controlled_writer_coverage_v1_append('demo',:uid,1,:previous,:state,:event,:audit)
        """),
            {
                "uid": uid,
                "previous": prepared["event_sha256"],
                "state": active["state_raw"].encode("ascii"),
                "event": active["event_raw"].encode("ascii"),
                "audit": active["audit_raw"].encode("ascii"),
            },
        )
    # Genuine separate-session V1 readback precedes the measured start marker.
    async with restricted.begin() as connection:
        await _settings(connection)
        head = await connection.scalar(
            text(
                "SELECT public.controlled_writer_coverage_v1_read('demo',:uid,2,:expected,'readback')"
            ),
            {"uid": uid, "expected": active["event_sha256"]},
        )
        assert head["head"]["journal_sha256"] == active["event_sha256"]
    post_active = stamp(datetime.now(UTC), time.monotonic_ns())
    sessions = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    scan = await _scan_in_transaction(sessions, scope, lambda: datetime.now(UTC))
    start = stamp(datetime.now(UTC), time.monotonic_ns())
    observed = SimpleNamespace(
        epoch_id=state["epoch_id"],
        generation=1,
        journal_sequence=2,
        journal_sha256=active["event_sha256"],
        state_sha256=records.digest(active["state_raw"].encode("ascii")),
        audit_sha256=records.digest(active["audit_raw"].encode("ascii")),
    )
    raw = cutover_record_fixture(
        template=state,
        active=observed,
        post_active=post_active,
        start=start,
        scan=json.loads(scan.document_json),
    )
    return uid, raw


async def _append(restricted, uid, raw):
    value = records.decode(raw)
    async with restricted.begin() as connection:
        await _settings(connection)
        return await connection.scalar(
            text(
                "SELECT public.controlled_writer_coverage_v2_cutover_append('demo',:uid,:epoch,:raw)"
            ),
            {"uid": uid, "epoch": value["epoch_id"], "raw": raw},
        )


async def test_fresh0051_preserves_parent_catalog_and_denies_unsealed_runtime(child):
    admin, restricted, _ = child
    async with admin.begin() as connection:
        await _settings(connection)
        previous.verify_controlled_writer_coverage_catalog(
            await connection.scalar(text(previous._CATALOG_SQL))
        )
        observed = await connection.scalar(text(catalog.CATALOG_SQL))
        catalog.verify_catalog(observed, require_native_baseline=False)
        with pytest.raises(
            records.ProspectiveCutoverError, match="native_baseline_pending"
        ):
            catalog.verify_catalog(observed)
    async with restricted.begin() as connection:
        await _settings(connection)
        observed = await connection.scalar(text(catalog.CATALOG_SQL))
        catalog.verify_catalog(observed, require_native_baseline=False)
        catalog.verify_runtime_role(observed)


async def test_real_sql_append_then_separate_readback_retains_unknown_financial_claims(
    child,
):
    admin, restricted, _ = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    expected = await _append(restricted, uid, raw)
    assert expected == records.digest(raw)
    async with restricted.begin() as connection:
        await _settings(connection)
        result = await connection.scalar(
            text(
                "SELECT public.controlled_writer_coverage_v2_cutover_read('demo',:uid,:epoch,:expected)"
            ),
            {
                "uid": uid,
                "epoch": records.decode(raw)["epoch_id"],
                "expected": expected,
            },
        )
        assert (
            result["record_json"] == raw.decode("ascii")
            and result["record_sha256"] == expected
        )
    async with admin.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT account_revision,ledger_revision,claims_json,claims_sha256 FROM public.qualification_account_scopes WHERE account_id=:uid"
                ),
                {"uid": uid},
            )
        ).one()
        assert tuple(row) == (0, 0, None, None)


async def test_two_workers_only_one_cutover_and_original_marker_survives_restart(child):
    admin, restricted, _ = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    results = await asyncio.gather(
        _append(restricted, uid, raw),
        _append(restricted, uid, raw),
        return_exceptions=True,
    )
    assert sum(type(value) is str for value in results) == 1
    assert sum(isinstance(value, DBAPIError) for value in results) == 1
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    f"SELECT count(*) FROM public.{catalog.TABLE} WHERE account_id=:uid"
                ),
                {"uid": uid},
            )
            == 1
        )


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_immutable_marker_cannot_be_rewritten_deleted_or_truncated(
    child, operation
):
    admin, restricted, _ = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    await _append(restricted, uid, raw)
    sql = {
        "UPDATE": f"UPDATE public.{catalog.TABLE} SET coverage_start_at=coverage_start_at-interval '1 hour'",
        "DELETE": f"DELETE FROM public.{catalog.TABLE}",
        "TRUNCATE": f"TRUNCATE public.{catalog.TABLE}",
    }[operation]
    with pytest.raises(DBAPIError, match="prospective_cutover_immutable"):
        async with admin.begin() as connection:
            await connection.execute(text(sql))
    async with admin.connect() as connection:
        assert await connection.scalar(
            text(
                f"SELECT record_json FROM public.{catalog.TABLE} WHERE account_id=:uid"
            ),
            {"uid": uid},
        ) == raw.decode("ascii")


async def test_readback_failure_retains_committed_marker(child):
    admin, restricted, role = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    expected = await _append(restricted, uid, raw)
    async with admin.begin() as connection:
        await connection.execute(
            text(
                f'REVOKE EXECUTE ON FUNCTION public.controlled_writer_coverage_v2_cutover_read(text,text,text,text) FROM "{role}"'
            )
        )
    with pytest.raises(DBAPIError):
        async with restricted.begin() as connection:
            await connection.scalar(
                text(
                    "SELECT public.controlled_writer_coverage_v2_cutover_read('demo',:uid,:epoch,:expected)"
                ),
                {
                    "uid": uid,
                    "epoch": records.decode(raw)["epoch_id"],
                    "expected": expected,
                },
            )
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    f"SELECT record_sha256 FROM public.{catalog.TABLE} WHERE account_id=:uid"
                ),
                {"uid": uid},
            )
            == expected
        )


async def test_changed_control_or_source_binding_cannot_insert_marker(child):
    admin, restricted, _ = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    changed = records.decode(raw)
    changed["control_revision"] += 1
    # Send malformed prospective bytes directly to the actual SQL guard. No
    # application validator or Boolean fixture can substitute for SQL rejection.
    with pytest.raises(DBAPIError):
        async with restricted.begin() as connection:
            await _settings(connection)
            await connection.scalar(
                text(
                    "SELECT public.controlled_writer_coverage_v2_cutover_append('demo',:uid,:epoch,:raw)"
                ),
                {
                    "uid": uid,
                    "epoch": changed["epoch_id"],
                    "raw": records.canonical(changed),
                },
            )
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(f"SELECT count(*) FROM public.{catalog.TABLE}")
            )
            == 0
        )


async def test_private_bootstrap_scan_does_not_publish_claims_or_relax_old_public_reader(
    child,
):
    admin, _, _ = child
    sessions = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    uid = "730051" + str(uuid4().int % 10**12).zfill(12)
    scope = LedgerScope(account_id=uid, settlement_currency="USDT")
    ledger = QualificationLedgerRepository(sessions, clock=lambda: datetime.now(UTC))
    await ledger.initialize_capture_scope(scope)
    scan = await _scan_in_transaction(sessions, scope, lambda: datetime.now(UTC))
    assert scan.consumed_event_keys == frozenset()
    document = json.loads(scan.document_json)
    assert document["account_revision"] == document["ledger_revision"] == 0
    assert (
        document["claims_sha256"] is None and document["db_journal_complete"] is False
    )
    with pytest.raises(QualificationLedgerError, match="account_scope_unverified"):
        await ledger.inspect_consumed_event_journal(scope)
    async with sessions() as session:
        row = await session.scalar(
            select(QualificationAccountScope).filter_by(account_id=uid)
        )
        assert (
            row.account_revision == row.ledger_revision == 0
            and row.claims_json is None
            and row.claims_sha256 is None
        )


@pytest.fixture
def historical_fixture():
    return ledger_fixture(account_id="730051" + str(uuid4().int % 10**12).zfill(12))


async def test_uid_scan_keeps_expired_reconciled_flat_tombstone(
    child, historical_fixture
):
    admin, _, _ = child
    fixture = historical_fixture
    sessions = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    clock = SimpleNamespace(value=fixture.now)
    ledger = QualificationLedgerRepository(sessions, clock=lambda: clock.value)
    await ledger.reconcile_scope(fixture.claims, expected_revision=0)
    receipt = await ledger.reserve(fixture.request)
    clock.value += timedelta(seconds=1)
    await ledger.reconcile_reservation(
        receipt.scope,
        receipt.original_event_key,
        claims=refresh(fixture.claims, clock.value, armed=False),
        expected_revision=2,
    )
    clock.value = fixture.request.origin.deadline + timedelta(days=8)
    scan = await _scan_in_transaction(
        sessions, fixture.request.scope, lambda: clock.value
    )
    assert receipt.original_event_key in scan.consumed_event_keys
    assert json.loads(scan.document_json)["state_counts"]["reconciled_flat"] == 1
    with pytest.raises(QualificationLedgerError, match="event_already_recorded"):
        await ledger.reserve(fixture.request)


async def test_nonempty_downgrade_refuses_without_removing_marker_or_parent(child):
    admin, restricted, _ = child
    uid, raw = await _synthetic_cutover_raw(admin, restricted)
    await _append(restricted, uid, raw)
    with pytest.raises(
        DBAPIError, match="prospective_cutover_downgrade_requires_empty"
    ):
        async with admin.begin() as connection:
            await connection.run_sync(_migrate, "0050", "downgrade")
    async with admin.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0051"
        )
        assert await connection.scalar(
            text(f"SELECT record_json FROM public.{catalog.TABLE}")
        ) == raw.decode("ascii")
        previous.verify_controlled_writer_coverage_catalog(
            await connection.scalar(text(previous._CATALOG_SQL))
        )


async def test_empty_downgrade_reupgrade_retains_parent_native0050_catalog(child):
    admin, _, _ = child
    async with admin.begin() as connection:
        await connection.run_sync(_migrate, "0050", "downgrade")
    async with admin.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0050"
        )
        await connection.run_sync(_migrate, "0051", "upgrade")
    async with admin.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0051"
        )
        catalog.verify_catalog(
            await connection.scalar(text(catalog.CATALOG_SQL)),
            require_native_baseline=False,
        )
