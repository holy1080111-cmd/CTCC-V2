"""Actual disposable-PG DB0048 tests; all source/account inputs are synthetic.

The primary migrated hermetic database retains accepted rows for the runner's
later real downgrade/retention experiment. Only child databases/roles created
by this module are removed. No native source, exchange or execution is tested.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import controlled_writer_coverage as coverage
from app.domain.source_primitives import utc_from_ns
from tests.controlled_writer_coverage_fixtures import (
    coverage_record_fixture,
    seed_controlled_writer_control,
    seed_controlled_writer_coverage,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]


def _hermetic_url(raw):
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "") is None
        or url.username != "ctcc"
        or url.password is not None
        or url.port != 5432
        or url.database != "ctcc"
        or url.query
    ):
        raise RuntimeError("coverage_hermetic_cluster_required")
    return url


@pytest.fixture
async def coverage_primary():
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip("explicit isolated migrated DATABASE_URL required")
    engine = create_async_engine(_hermetic_url(raw), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(
                text("SELECT current_setting('server_version_num')::int")
            )
            assert 170000 <= version < 180000
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0048"
            )
        yield engine
    finally:
        await engine.dispose()


def _migrate(connection, target, direction):
    """Run real Alembic RevisionStep/version bookkeeping without env mutation."""
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0048"
    resolver = (
        script._upgrade_revs if direction == "upgrade" else script._downgrade_revs
    )
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: resolver(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@pytest.fixture
async def coverage_child(coverage_primary):
    parent = coverage_primary
    name = "ctcc_cwc_" + uuid4().hex
    role = "ctcc_cwc_" + uuid4().hex
    assert re.fullmatch(r"ctcc_cwc_[a-f0-9]{32}", name)
    assert re.fullmatch(r"ctcc_cwc_[a-f0-9]{32}", role)
    child = None
    restricted = None
    created_database = False
    created_role = False
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
            await connection.run_sync(_migrate, "0048", "upgrade")
            await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
            created_role = True
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            for function in (
                "controlled_writer_coverage_v1_append",
                "controlled_writer_coverage_v1_read",
            ):
                signature = coverage.COVERAGE_FUNCTION_SIGNATURES[function]
                await connection.execute(
                    text(
                        f'GRANT EXECUTE ON FUNCTION public.{function}({signature}) TO "{role}"'
                    )
                )
        restricted = create_async_engine(
            parent.url.set(database=name, username=role), poolclass=NullPool
        )
        yield child, restricted
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


async def _append(connection, uid, fixture, expected_sequence=0, expected_sha256=None):
    return (
        await connection.execute(
            text(
                "SELECT * FROM public.controlled_writer_coverage_v1_append(:env,:uid,:seq,:sha,:state,:event,:audit)"
            ),
            {
                "env": "demo",
                "uid": uid,
                "seq": expected_sequence,
                "sha": expected_sha256,
                "state": fixture["state_raw"].encode("ascii"),
                "event": fixture["event_raw"].encode("ascii"),
                "audit": fixture["audit_raw"].encode("ascii"),
            },
        )
    ).one()


async def _head(connection, uid, sequence=None, sha256=None, mode=None):
    mode = (
        ("discover" if sequence is None and sha256 is None else "readback")
        if mode is None
        else mode
    )
    return await connection.scalar(
        text(
            "SELECT public.controlled_writer_coverage_v1_read('demo',:uid,:seq,:sha,:mode)"
        ),
        {"uid": uid, "seq": sequence, "sha": sha256, "mode": mode},
    )


async def test_function_only_append_read_and_restricted_table_denial(coverage_child):
    admin, restricted = coverage_child
    uid = "730048101"
    async with admin.begin() as connection:
        control, event = await seed_controlled_writer_control(connection, uid=uid)
    fixture = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    async with restricted.begin() as connection:
        result = await _append(connection, uid, fixture)
        assert tuple(result) == (1, fixture["event_sha256"])
    async with restricted.begin() as connection:
        observed = await _head(connection, uid, 1, fixture["event_sha256"])
        record = coverage._observation(control.scope, observed)
        assert record.state == "prepared" and record.execution_authority is False
        assert record.state_json == fixture["state_raw"]
    for statement in (
        "SELECT * FROM public.demo_controlled_writer_coverage_scopes",
        "UPDATE public.demo_controlled_writer_coverage_scopes SET state='active'",
        "DELETE FROM public.demo_controlled_writer_coverage_journal",
        "TRUNCATE public.demo_controlled_writer_coverage_journal",
        "ALTER TABLE public.demo_controlled_writer_coverage_journal DISABLE TRIGGER ALL",
    ):
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await connection.execute(text(statement))


async def test_cas_conflict_preserves_single_transition(coverage_child):
    admin, restricted = coverage_child
    uid = "730048102"
    async with admin.begin() as connection:
        control, event = await seed_controlled_writer_control(connection, uid=uid)
    first = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    second = coverage_record_fixture(
        uid=uid,
        now=control.updated_at,
        control=control,
        control_event_sha256=event,
        state_overrides={"epoch_id": uuid4().hex},
    )

    async def worker(fixture):
        try:
            async with restricted.begin() as connection:
                await connection.execute(text("SET LOCAL lock_timeout='1500ms'"))
                return tuple(await _append(connection, uid, fixture))
        except DBAPIError:
            return None

    results = await asyncio.gather(worker(first), worker(second))
    assert sum(result is not None for result in results) == 1
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.demo_controlled_writer_coverage_journal"
                )
            )
            == 1
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT journal_sequence FROM public.demo_controlled_writer_coverage_scopes"
                )
            )
            == 1
        )


async def test_immutable_journal_and_pair_guards(coverage_child):
    admin, _ = coverage_child
    async with admin.begin() as connection:
        await seed_controlled_writer_coverage(connection, uid="730048103")
    statements = (
        "UPDATE public.demo_controlled_writer_coverage_journal SET audit_json=audit_json",
        "DELETE FROM public.demo_controlled_writer_coverage_journal",
        "DELETE FROM public.demo_controlled_writer_coverage_scopes",
        "TRUNCATE public.demo_controlled_writer_coverage_journal",
        "TRUNCATE public.demo_controlled_writer_coverage_scopes CASCADE",
        "UPDATE public.demo_controlled_writer_coverage_scopes SET journal_sequence=journal_sequence+1",
    )
    for statement in statements:
        with pytest.raises(DBAPIError):
            async with admin.begin() as connection:
                await connection.execute(text(statement))
    async with admin.begin() as connection:
        catalog = await coverage.read_controlled_writer_coverage_catalog(connection)
        assert all(row["enabled"] == "A" for row in catalog["triggers"])
        assert (
            sum(
                row["constraint"] is True and row["deferred"] is True
                for row in catalog["triggers"]
            )
            == 2
        )


async def _snapshot(engine):
    async with engine.begin() as connection:
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        catalog = await coverage.read_controlled_writer_coverage_catalog(connection)
        retention = await coverage.read_controlled_writer_coverage_retention(connection)
        major = (
            int(
                await connection.scalar(
                    text("SELECT current_setting('server_version_num')")
                )
            )
            // 10000
        )
    return head, catalog, retention, major


def _export_migration_receipt(before, after):
    root = os.environ.get("CTCC_COVERAGE_RECEIPT_DIR")
    if root is None:
        return
    destination = Path(root)
    if (
        destination != Path("/validation-results")
        or destination.is_symlink()
        or not destination.is_dir()
    ):
        raise RuntimeError("coverage_receipt_export_root_invalid")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def projection(value):
        head, catalog, retention, _ = value
        return {
            "head": head,
            "rows_sha256": retention["rows_sha256"],
            "counts": retention["counts"],
            "catalog_sha256": hashlib.sha256(
                coverage.canonical(catalog).encode("ascii")
            ).hexdigest(),
        }

    receipt = {
        "schema_version": "ctcc.controlled_writer_coverage_migration_acceptance.v1",
        "manifest_sha256": digest(_ROOT / "MANIFEST.sha256"),
        "repository_sha256": digest(
            _ROOT / "app/database/repositories/controlled_writer_coverage.py"
        ),
        "migration_sha256": digest(
            _ROOT / "migrations/versions/0048_controlled_writer_coverage.py"
        ),
        "server_major": before[3],
        "before": projection(before),
        "after": projection(after),
        "refusal_code": "controlled_writer_coverage_downgrade_requires_empty",
        "downgrade_target": "0047",
        "native_baseline_admission": False,
        "execution_authority": False,
    }
    raw = coverage.canonical(receipt).encode("ascii") + b"\n"
    target = destination / "controlled-writer-coverage-migration.json"
    with target.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    assert target.read_bytes() == raw


async def test_nonempty_0048_downgrade_refuses_and_retains_exact_catalog(
    coverage_primary,
):
    # These rows deliberately remain in the primary harness-owned database.
    async with coverage_primary.begin() as connection:
        await seed_controlled_writer_coverage(
            connection, uid=str(730048000000 + uuid4().int % 10**9)
        )
    before = await _snapshot(coverage_primary)
    assert before[0] == "0048" and before[3] == 17
    assert all(count > 0 for count in before[2]["counts"].values())
    with pytest.raises(
        DBAPIError, match="controlled_writer_coverage_downgrade_requires_empty"
    ):
        async with coverage_primary.begin() as connection:
            await connection.run_sync(_migrate, "0047", "downgrade")
    after = await _snapshot(coverage_primary)
    assert coverage.canonical(before) == coverage.canonical(after)
    _export_migration_receipt(before, after)


async def test_empty_0048_downgrade_and_reupgrade(coverage_child):
    admin, _ = coverage_child
    before = await _snapshot(admin)
    assert before[0] == "0048" and all(
        count == 0 for count in before[2]["counts"].values()
    )
    async with admin.begin() as connection:
        await connection.run_sync(_migrate, "0047", "downgrade")
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0047"
        )
        for table in coverage.COVERAGE_TABLES:
            assert (
                await connection.scalar(
                    text("SELECT to_regclass(:name)"), {"name": "public." + table}
                )
                is None
            )
    async with admin.begin() as connection:
        await connection.run_sync(_migrate, "0048", "upgrade")
    after = await _snapshot(admin)
    assert after[0] == "0048"
    coverage.verify_controlled_writer_coverage_catalog(
        after[1], require_native_baseline=False
    )
    assert before[2] == after[2]


async def test_catalog_capture_retains_pending_native_baseline(coverage_child):
    admin, _ = coverage_child
    async with admin.begin() as connection:
        catalog = await coverage.read_controlled_writer_coverage_catalog(connection)
    raw_before = coverage.canonical(catalog)
    # Actual PG values from all eight SQL OID projections, never coerced here.
    projected_oids = {
        "role.oid": [catalog["role"]["oid"]],
        "tables.oid": [row["oid"] for row in catalog["tables"]],
        "tables.owner_oid": [row["owner_oid"] for row in catalog["tables"]],
        "functions.oid": [row["oid"] for row in catalog["functions"]],
        "functions.namespace_oid": [
            row["namespace_oid"] for row in catalog["functions"]
        ],
        "functions.owner_oid": [row["owner_oid"] for row in catalog["functions"]],
        "triggers.function_oid": [row["function_oid"] for row in catalog["triggers"]],
        "triggers.function_namespace_oid": [
            row["function_namespace_oid"] for row in catalog["triggers"]
        ],
    }
    assert [len(values) for values in projected_oids.values()] == [
        1,
        2,
        2,
        6,
        6,
        6,
        8,
        8,
    ]
    assert sum(len(values) for values in projected_oids.values()) == 39
    for field, values in projected_oids.items():
        assert all(
            type(value) is int and 1 <= value <= 2**32 - 1 for value in values
        ), field
    expected = coverage.verify_controlled_writer_coverage_catalog(
        catalog, require_native_baseline=False
    )
    assert expected["native_baseline_status"] == coverage.PG17_DEPARSED_BASELINE_STATUS
    assert expected["native_deparser_baseline"] == coverage.PG17_DEPARSED_BASELINE
    assert expected["native_source_acceptance"] is False
    if coverage.PG17_DEPARSED_BASELINE is None:
        with pytest.raises(
            coverage.ControlledWriterCoverageError, match="native_baseline_unsealed"
        ):
            coverage.verify_controlled_writer_coverage_catalog(catalog)
    else:
        coverage.verify_controlled_writer_coverage_catalog(catalog)
    assert catalog["functions"] and catalog["constraints"] and catalog["indexes"]
    assert all(
        "source" in row and "definition" in row and "acl" in row
        for row in catalog["functions"]
    )
    assert coverage.canonical(catalog) == raw_before


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-top",
        "duplicate-pins",
        "duplicate-stamp",
        "duplicate-event",
        "duplicate-audit-array",
        "escaped-duplicate",
        "bool-as-int",
        "noncanonical",
        "wrong-uid-type",
    ],
)
async def test_direct_sql_malformed_or_duplicate_wire_denies(coverage_child, mutation):
    admin, restricted = coverage_child
    uid = "730048109"
    async with admin.begin() as connection:
        control, event = await seed_controlled_writer_control(connection, uid=uid)
    fixture = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    if mutation == "duplicate-top":
        fixture["state_raw"] = '{"account_id":"' + uid + '",' + fixture["state_raw"][1:]
    elif mutation == "duplicate-pins":
        fixture["state_raw"] = fixture["state_raw"].replace(
            '"control_pins":{', '"control_pins":{"config_sha256":"' + "1" * 64 + '",', 1
        )
    elif mutation == "duplicate-stamp":
        fixture["state_raw"] = fixture["state_raw"].replace(
            '"original_native_anchor":{', '"original_native_anchor":{"utc_ns":1,', 1
        )
    elif mutation == "duplicate-event":
        fixture["event_raw"] = '{"action":"prepared",' + fixture["event_raw"][1:]
    elif mutation == "duplicate-audit-array":
        fixture["audit_raw"] = '{"nested":[{"a":1,"a":1}],' + fixture["audit_raw"][1:]
        state = json.loads(fixture["state_raw"])
        state["audit_sha256"] = hashlib.sha256(
            fixture["audit_raw"].encode("ascii")
        ).hexdigest()
        fixture["state_raw"] = coverage.canonical(state)
    elif mutation == "escaped-duplicate":
        fixture["state_raw"] = (
            '{"\\u0061ccount_id":"' + uid + '",' + fixture["state_raw"][1:]
        )
    elif mutation == "bool-as-int":
        fixture["state_raw"] = fixture["state_raw"].replace(
            '"generation":1', '"generation":true', 1
        )
    elif mutation == "wrong-uid-type":
        fixture["state_raw"] = fixture["state_raw"].replace(
            '"account_id":"' + uid + '"', '"account_id":' + uid, 1
        )
    else:
        fixture["state_raw"] = fixture["state_raw"].replace("{", "{ ", 1)
    if mutation != "duplicate-event":
        event = json.loads(fixture["event_raw"])
        event["state_sha256"] = hashlib.sha256(
            fixture["state_raw"].encode("ascii")
        ).hexdigest()
        event["audit_sha256"] = hashlib.sha256(
            fixture["audit_raw"].encode("ascii")
        ).hexdigest()
        fixture["event_raw"] = coverage.canonical(event)
    duplicate = mutation in (
        "duplicate-top",
        "duplicate-pins",
        "duplicate-stamp",
        "duplicate-event",
        "duplicate-audit-array",
        "escaped-duplicate",
    )
    with pytest.raises(
        DBAPIError,
        match="controlled_writer_coverage_duplicate_or_malformed"
        if duplicate
        else None,
    ):
        async with restricted.begin() as connection:
            await _append(connection, uid, fixture)
    async with admin.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM public.demo_controlled_writer_coverage_journal"
                )
            )
            == 0
        )


async def test_read_selector_modes_never_reclassify_cas_mismatch_as_absence(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048110"
    async with admin.begin() as connection:
        await seed_controlled_writer_control(connection, uid=uid)
    async with restricted.begin() as connection:
        assert await _head(connection, uid) == {
            "head": None,
            "records": [],
            "previous_terminal_anchor": None,
        }
    for sequence, sha256, mode in (
        (1, None, "readback"),
        (None, "1" * 64, "readback"),
        (0, "1" * 64, "append"),
        (1, "1" * 64, "readback"),
        (None, None, "unknown"),
        (0, None, "readback"),
    ):
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await _head(connection, uid, sequence, sha256, mode)
    async with restricted.begin() as connection:
        assert await _head(connection, uid, 0, None, "append") == {
            "head": None,
            "records": [],
            "previous_terminal_anchor": None,
        }


async def test_read_modes_hold_real_uid_control_head_and_late_table_locks(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048111"
    async with admin.begin() as connection:
        fixture = await seed_controlled_writer_coverage(connection, uid=uid)
    async with restricted.begin() as connection:
        await _head(connection, uid)
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
                )
            )
            > 0
        )
        for table in ("demo_account_controls", coverage.COVERAGE_TABLES[0]):
            with pytest.raises(DBAPIError):
                async with admin.begin() as competitor:
                    await competitor.execute(
                        text(
                            f"SELECT 1 FROM public.{table} WHERE environment='demo' AND account_id=:uid FOR UPDATE NOWAIT"
                        ),
                        {"uid": uid},
                    )
        await _head(connection, uid, 1, fixture["event_sha256"], "readback")
        locks = (
            await connection.execute(
                text("""
          SELECT c.relname,l.mode FROM pg_catalog.pg_locks l JOIN pg_catalog.pg_class c ON c.oid=l.relation
          WHERE l.pid=pg_backend_pid() AND l.granted AND c.relname IN
          ('demo_controlled_writer_coverage_scopes','demo_controlled_writer_coverage_journal')
        """)
            )
        ).all()
        for table in coverage.COVERAGE_TABLES:
            assert (table, "ShareLock") in locks
            assert (table, "RowExclusiveLock") not in locks
        with pytest.raises(DBAPIError):
            async with admin.begin() as competitor:
                await competitor.execute(
                    text(
                        "LOCK TABLE public.demo_controlled_writer_coverage_journal IN ROW EXCLUSIVE MODE NOWAIT"
                    )
                )
    # The old readback transaction has ended; its table/row locks are gone.
    async with admin.begin() as connection:
        await connection.execute(
            text(
                "LOCK TABLE public.demo_controlled_writer_coverage_journal IN ROW EXCLUSIVE MODE NOWAIT"
            )
        )
    async with restricted.begin() as connection:
        await _head(connection, uid, 1, fixture["event_sha256"], "append")
        locks = (
            await connection.execute(
                text("""
          SELECT c.relname,l.mode FROM pg_catalog.pg_locks l JOIN pg_catalog.pg_class c ON c.oid=l.relation
          WHERE l.pid=pg_backend_pid() AND l.granted AND c.relname IN
          ('demo_controlled_writer_coverage_scopes','demo_controlled_writer_coverage_journal')
        """)
            )
        ).all()
        for table in coverage.COVERAGE_TABLES:
            assert (table, "RowExclusiveLock") in locks
            assert (table, "ShareLock") not in locks


async def test_late_mode_table_conflict_refuses_without_retry_or_journal_change(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048112"
    async with admin.begin() as connection:
        fixture = await seed_controlled_writer_coverage(connection, uid=uid)
    before = await _snapshot(admin)
    async with admin.begin() as blocker:
        await blocker.execute(
            text(
                "LOCK TABLE public.demo_controlled_writer_coverage_journal IN SHARE MODE NOWAIT"
            )
        )
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await connection.execute(text("SET LOCAL statement_timeout='1000ms'"))
                await _head(connection, uid, 1, fixture["event_sha256"], "append")
    after = await _snapshot(admin)
    assert coverage.canonical(before) == coverage.canonical(after)


async def test_exact_ascii_unicode_escape_canonical_audit_bytes_round_trip(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048113"
    async with admin.begin() as connection:
        control, event = await seed_controlled_writer_control(connection, uid=uid)
    fixture = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    audit = json.loads(fixture["audit_raw"])
    audit["legacy"] = {
        "synthetic_text": '\u00e9 \U0001f600 \b\f\n\r\t"\\',
        "nested": [1, True, None],
    }
    fixture = coverage_record_fixture(
        uid=uid,
        now=control.updated_at,
        control=control,
        control_event_sha256=event,
        audit_raw=coverage.canonical(audit),
    )
    async with restricted.begin() as connection:
        await _append(connection, uid, fixture)
    async with restricted.begin() as connection:
        observed = await _head(connection, uid, 1, fixture["event_sha256"])
        assert observed["records"][0]["audit_json"] == fixture["audit_raw"]
        assert "\\ud83d\\ude00" in fixture["audit_raw"]


async def test_first_invocation_uses_fixed_function_path_despite_temporary_shadowing(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048114"
    async with admin.begin() as connection:
        fixture = await seed_controlled_writer_coverage(connection, uid=uid)
    # The functions have not yet run on this restricted connection. Names are
    # fixed adversarial fixture objects, never caller-generated SQL identifiers.
    async with restricted.begin() as connection:
        await connection.execute(
            text("CREATE TEMP TABLE demo_account_controls(account_id text)")
        )
        await connection.execute(
            text(
                "CREATE TEMP TABLE demo_controlled_writer_coverage_scopes(account_id text)"
            )
        )
        await connection.execute(
            text(
                "CREATE TEMP TABLE demo_controlled_writer_coverage_journal(account_id text)"
            )
        )
        await connection.execute(text("CREATE DOMAIN pg_temp.jsonb AS text"))
        await connection.execute(
            text("SET LOCAL search_path TO pg_temp, public, pg_catalog")
        )
        observed = await _head(connection, uid, 1, fixture["event_sha256"], "readback")
        assert observed["head"]["account_id"] == uid
        assert observed["head"]["state_json"] == fixture["state_raw"]
        functions = (
            (
                await connection.execute(
                    text("""
          SELECT proconfig FROM pg_catalog.pg_proc WHERE pronamespace='public'::regnamespace
            AND proname LIKE 'controlled_writer_coverage_v1_%'
        """)
                )
            )
            .scalars()
            .all()
        )
        assert len(functions) == 6
        assert all(
            value == ["search_path=pg_catalog, public, pg_temp"] for value in functions
        )


async def test_restart_revocation_retains_original_clock_domain_and_denies_regressed_utc(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048115"
    async with admin.begin() as connection:
        original = await seed_controlled_writer_coverage(connection, uid=uid)
    before = original["state"]
    # A simulated new boot's lower monotonic origin is deliberately never copied
    # into the old epoch. Only actual current UTC dates this safety transition.
    simulated_new_boot_monotonic = 0
    assert simulated_new_boot_monotonic < before["transition_stamp"]["monotonic_ns"]
    actual_utc = datetime.now(UTC)
    actual_control_observation = {
        "revision": before["control_revision"],
        "owner_epoch": before["control_owner_epoch"],
        "event_sha256": before["control_event_sha256"],
        "state_sha256": before["control_state_sha256"],
        "emergency_stop": True,
        "arm_request_id": None,
    }
    revoked = coverage._revocation_document(
        before,
        now=actual_utc,
        reason="restart_reconciliation_required",
        observed=actual_control_observation,
    )
    assert revoked["transition_stamp"] == before["transition_stamp"]
    assert revoked["original_native_anchor"] == before["original_native_anchor"]
    assert revoked["process_domain_nonce"] == before["process_domain_nonce"]
    for malformed in (
        {
            **revoked,
            "transition_stamp": {
                **revoked["transition_stamp"],
                "monotonic_ns": simulated_new_boot_monotonic,
            },
        },
        {
            **revoked,
            "updated_at": (
                datetime.fromisoformat(before["updated_at"]) - timedelta(microseconds=1)
            ).isoformat(),
        },
    ):
        bad = coverage_record_fixture(
            uid=uid,
            state_overrides=revoked,
            previous_sha256=original["event_sha256"],
            audit_raw=original["audit_raw"],
        )
        # Malformed raw bytes deliberately bypass only the pure fixture builder;
        # direct SQL must still reject them with recomputed otherwise-valid hashes.
        bad["state_raw"] = coverage.canonical(malformed)
        event = json.loads(bad["event_raw"])
        event["state_sha256"] = hashlib.sha256(
            bad["state_raw"].encode("ascii")
        ).hexdigest()
        event["occurred_at"] = malformed["updated_at"]
        bad["event_raw"] = coverage.canonical(event)
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await _append(connection, uid, bad, 1, original["event_sha256"])
    accepted = coverage_record_fixture(
        uid=uid,
        state_overrides=revoked,
        previous_sha256=original["event_sha256"],
        audit_raw=original["audit_raw"],
    )
    async with restricted.begin() as connection:
        await _append(connection, uid, accepted, 1, original["event_sha256"])
    async with restricted.begin() as connection:
        document = await _head(connection, uid, 2, accepted["event_sha256"], "readback")
        observed = coverage._observation(coverage.ControlScope("demo", uid), document)
    assert observed.state == "revoked" and observed.execution_authority is False
    assert observed.state_document["updated_at"] == actual_utc.isoformat()
    assert (
        observed.state_document["source_current_expires_at"]
        == before["source_current_expires_at"]
    )


async def test_active_transition_preserves_fixed_source_and_duplicate_active_denies(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048116"
    async with admin.begin() as connection:
        prepared = await seed_controlled_writer_coverage(connection, uid=uid)
    now = datetime.now(UTC)
    utc_ns = int(now.timestamp()) * 1_000_000_000 + now.microsecond * 1000
    old_stamp = prepared["state"]["transition_stamp"]
    # Explicit synthetic monotonic-domain payload for SQL transition semantics;
    # this is not a native clock receipt or proof of source authenticity.
    stamp = {
        "utc_ns": utc_ns,
        "monotonic_ns": old_stamp["monotonic_ns"] + utc_ns - old_stamp["utc_ns"],
    }
    active = coverage_record_fixture(
        uid=uid,
        state_overrides={
            **prepared["state"],
            "state": "active",
            "journal_sequence": 2,
            "updated_at": now.isoformat(),
            "transition_stamp": stamp,
        },
        previous_sha256=prepared["event_sha256"],
        audit_raw=prepared["audit_raw"],
    )
    malformed = {**active}
    state = {**active["state"], "source_bundle_sha256": "f" * 64}
    malformed["state_raw"] = coverage.canonical(state)
    event = json.loads(active["event_raw"])
    event["state_sha256"] = hashlib.sha256(
        malformed["state_raw"].encode("ascii")
    ).hexdigest()
    malformed["event_raw"] = coverage.canonical(event)
    with pytest.raises(DBAPIError, match="controlled_writer_coverage_transition"):
        async with restricted.begin() as connection:
            await _append(connection, uid, malformed, 1, prepared["event_sha256"])
    async with restricted.begin() as connection:
        await _append(connection, uid, active, 1, prepared["event_sha256"])
    duplicate = coverage_record_fixture(
        uid=uid,
        state_overrides={**active["state"], "journal_sequence": 3},
        previous_sha256=active["event_sha256"],
        audit_raw=active["audit_raw"],
    )
    with pytest.raises(DBAPIError):
        async with restricted.begin() as connection:
            await _append(connection, uid, duplicate, 2, active["event_sha256"])
    async with restricted.begin() as connection:
        snapshot = await _head(connection, uid, 2, active["event_sha256"], "readback")
        observed = coverage._observation(coverage.ControlScope("demo", uid), snapshot)
    assert observed.state == "active" and observed.audit_json == prepared["audit_raw"]
    assert all(
        observed.state_document[name] is False
        for name in coverage.FALSE_AUTHORITY_FIELDS
    )


async def test_sql_transition_uses_original_conservative_nanosecond_utc_conversion(
    coverage_child,
):
    admin, restricted = coverage_child
    uid = "730048117"
    async with admin.begin() as connection:
        control, event = await seed_controlled_writer_control(connection, uid=uid)
    fixture = coverage_record_fixture(
        uid=uid, now=control.updated_at, control=control, control_event_sha256=event
    )
    stamp = {
        name: value + 1 for name, value in fixture["state"]["transition_stamp"].items()
    }
    projected = utc_from_ns(stamp["utc_ns"])
    assert projected == control.updated_at + timedelta(microseconds=1)
    fixture = coverage_record_fixture(
        uid=uid,
        now=control.updated_at,
        control=control,
        control_event_sha256=event,
        state_overrides={
            "transition_stamp": stamp,
            "updated_at": projected.isoformat(),
        },
    )
    async with restricted.begin() as connection:
        await _append(connection, uid, fixture)
    async with restricted.begin() as connection:
        observed = await _head(connection, uid, 1, fixture["event_sha256"], "readback")
        assert observed["records"][0]["state_json"] == fixture["state_raw"]


async def _parent_control_snapshot(engine):
    async with engine.begin() as connection:
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        return {
            "controls": await connection.scalar(
                text("""
              SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY environment,account_id),'[]'::jsonb)
              FROM public.demo_account_controls r
            """)
            ),
            "journal": await connection.scalar(
                text("""
              SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY environment,account_id,control_revision),'[]'::jsonb)
              FROM public.demo_control_journal r
            """)
            ),
        }


async def test_parent_control_writer_denies_0048_downgrade_nowait_without_partial_change(
    coverage_child,
):
    admin, _ = coverage_child
    async with admin.begin() as connection:
        await seed_controlled_writer_coverage(connection, uid="730048118")
    before = await _snapshot(admin)
    parent_before = await _parent_control_snapshot(admin)
    async with admin.connect() as writer:
        writer_transaction = await writer.begin()
        try:
            # Real valid uncommitted parent writes acquire the conflicting lock.
            # Their rollback below leaves the previously retained parent intact.
            await seed_controlled_writer_control(writer, uid="730048119")
            writer_pid = await writer.scalar(text("SELECT pg_backend_pid()"))
            assert (
                await writer.scalar(
                    text("""
              SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks
                WHERE pid=pg_backend_pid() AND granted AND mode='RowExclusiveLock'
                  AND relation='public.demo_account_controls'::regclass)
            """)
                )
                is True
            )
            with pytest.raises(DBAPIError) as denied:
                async with admin.begin() as migration:
                    assert (
                        await migration.scalar(text("SELECT pg_backend_pid()"))
                        != writer_pid
                    )
                    await migration.execute(text("SET LOCAL lock_timeout='1500ms'"))
                    await migration.execute(
                        text("SET LOCAL statement_timeout='2000ms'")
                    )
                    await migration.run_sync(_migrate, "0047", "downgrade")
            assert denied.value.orig.sqlstate == "55P03"
            message = str(denied.value.orig)
            assert (
                'could not obtain lock on relation "demo_account_controls"' in message
            )
            assert "lock timeout" not in message and "statement timeout" not in message
        finally:
            await writer_transaction.rollback()
    assert coverage.canonical(await _snapshot(admin)) == coverage.canonical(before)
    assert coverage.canonical(
        await _parent_control_snapshot(admin)
    ) == coverage.canonical(parent_before)


async def test_other_schema_same_name_trigger_function_is_rejected_before_native_schema_seal(
    coverage_child,
):
    admin, _ = coverage_child
    before = await _snapshot(admin)
    shadow_schema = "ctcc_cwc_shadow_" + uuid4().hex
    assert re.fullmatch(r"ctcc_cwc_shadow_[a-f0-9]{32}", shadow_schema)
    async with admin.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(f'CREATE SCHEMA "{shadow_schema}"'))
            await connection.execute(
                text(f"""
              CREATE FUNCTION "{shadow_schema}".controlled_writer_coverage_v1_scope_guard()
              RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$
            """)
            )
            await connection.execute(
                text(
                    "DROP TRIGGER cwc_scope_guard ON public.demo_controlled_writer_coverage_scopes"
                )
            )
            await connection.execute(
                text(f"""
              CREATE TRIGGER cwc_scope_guard BEFORE INSERT OR UPDATE
              ON public.demo_controlled_writer_coverage_scopes FOR EACH ROW
              EXECUTE FUNCTION "{shadow_schema}".controlled_writer_coverage_v1_scope_guard()
            """)
            )
            await connection.execute(
                text(
                    "ALTER TABLE public.demo_controlled_writer_coverage_scopes ENABLE ALWAYS TRIGGER cwc_scope_guard"
                )
            )
            drift = await coverage.read_controlled_writer_coverage_catalog(connection)
            assert coverage.canonical(drift["functions"]) == coverage.canonical(
                before[1]["functions"]
            )
            assert coverage.canonical(drift["constraints"]) == coverage.canonical(
                before[1]["constraints"]
            )
            assert coverage.canonical(drift["indexes"]) == coverage.canonical(
                before[1]["indexes"]
            )
            original = next(
                row for row in before[1]["triggers"] if row["name"] == "cwc_scope_guard"
            )
            substituted = next(
                row for row in drift["triggers"] if row["name"] == "cwc_scope_guard"
            )
            assert substituted["function"] == original["function"]
            assert substituted["function_oid"] != original["function_oid"]
            assert (
                substituted["function_namespace_oid"]
                != original["function_namespace_oid"]
            )
            assert substituted["function_namespace"] == shadow_schema
            identity_and_rendering = {
                "function_oid",
                "function_namespace_oid",
                "function_namespace",
                "definition",
            }
            assert coverage.canonical(
                {
                    key: value
                    for key, value in substituted.items()
                    if key not in identity_and_rendering
                }
            ) == coverage.canonical(
                {
                    key: value
                    for key, value in original.items()
                    if key not in identity_and_rendering
                }
            )
            # Both checks use this actual same-transaction catalog. The True
            # branch is the native schema gate, not native role/owner acceptance;
            # a second backend cannot observe uncommitted fixture-only DDL.
            for native_schema_gate in (False, True):
                with pytest.raises(
                    coverage.ControlledWriterCoverageError,
                    match="controlled_writer_coverage_trigger_identity_drift",
                ):
                    coverage.verify_controlled_writer_coverage_catalog(
                        drift, require_native_baseline=native_schema_gate
                    )
        finally:
            # Roll back only this owned child's adversarial schema transaction.
            # Do not commit a removed guard or alter any production declaration.
            await transaction.rollback()
    assert coverage.canonical(await _snapshot(admin)) == coverage.canonical(before)
