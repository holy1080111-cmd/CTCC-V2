"""Real isolated57→58 owner DDL/rollback; no installed DB, credential or venue.

The public/private archive boundary is covered separately. These components use
the genuine TLS three-role lab, real catalog observations and intact migrations.
"""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models.demo_automation import DemoAutomationState
from app.database.repositories import native_legacy_control_genesis_0056_v1 as genesis
from app.database.repositories.demo_control import DemoControlRepository
from scripts import native_owner_upgrade_0058_v1 as subject
from tests.integration import test_native_demo_profile_0058_pg as lab

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_owner58_lab = lab._actual58_demo_lab


def _parent(connection, script):
    subject.profiles.require_migration(subject.profiles.DB0058, subject.ROOT)
    assert MigrationContext.configure(connection).get_current_heads() == ("0058",)
    migration = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._downgrade_revs("0057", heads)}
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0057",)


@pytest.fixture
async def _actual57_owner_profile_lab(_owner58_lab):
    owner, selected, names = _owner58_lab
    config = Config(str(subject.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(subject.ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    async with owner.begin() as connection:
        await connection.run_sync(_parent, script)
    seed_at = datetime.now(UTC) - timedelta(seconds=2)
    factory = async_sessionmaker(owner, autoflush=False, expire_on_commit=False)
    await DemoControlRepository(factory, clock=lambda: seed_at).latch_stop(
        subject.parent.control.ControlScope("demo", "730058"), command_id="a" * 64
    )
    shared = genesis._stopped_values(seed_at, "f" * 64)
    shared.update(
        control_revision=7,
        daily_pnl=Decimal("-12.5"),
        trades_today=13,
        consecutive_losses=3,
        peak_equity=Decimal(1234),
        realized_pnl_events=[{"net_pnl": "-12.5", "closed_at": seed_at.isoformat()}],
    )
    async with factory.begin() as session:
        session.add(DemoAutomationState(**shared))
    async with owner.connect() as connection, connection.begin():
        for sql in subject.parent.old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        actual = tuple((await connection.execute(subject.route._PROFILE_SQL)).one())
        oids = tuple(
            [
                await connection.scalar(
                    text(
                        "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:name"
                    ),
                    {"name": name},
                )
                for name in names
            ]
        )
    pins = subject.OwnerProfileUpgrade0058PinsV1(
        "a" * 40,
        "b" * 40,
        "c" * 64,
        subject.route.OwnerRoutePinsV1(
            subject.sha((subject.ROOT / "MANIFEST.sha256").read_bytes()),
            os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
            subject.sha(actual[11].encode("ascii")),
            owner.url.host,
            owner.url.port or 5432,
            actual[2],
            actual[0],
            actual[5],
            actual[4],
            "0057",
        ),
        names,
        oids,
    )
    yield owner, selected, pins, script


async def test_actual0057_owner58_rehearsal_and_commit_preserve_every_row_actor_and44_acl(
    _actual57_owner_profile_lab,
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before_identity, incarnation = await subject._role_observation(
        selected, owner, pins, "0057"
    )
    before = await subject._inventory(owner, pins)
    namespace_count = text("""SELECT count(*) FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='pg_catalog' AND p.proname='qualification_submission_binding'""")
    async with owner.connect() as connection:
        assert await connection.scalar(namespace_count) == 0
    try:
        final = await subject._commit_once(script, pins, owner, before)
    except subject.OwnerProfileUpgrade0058Refused as error:
        pytest.fail("owner58_declared_diagnostic_stage:" + error.stage)
    prepared = subject.route._source_for_profile(subject.profiles.DB0058)
    pin_context = type(
        "PinContext", (), {"migration_sha256": prepared.actual_migration_sha256}
    )()
    after_pins = replace(
        pins,
        route=replace(pins.route, database_head="0058"),
        installed_catalog_sha256=subject._installed_pin(pin_context, final["logical"]),
    )
    after_identity, after_incarnation = await subject._role_observation(
        selected, owner, after_pins, "0058"
    )
    assert after_incarnation == incarnation
    assert after_identity.role_oids == before_identity.role_oids == pins.role_oids
    assert after_identity.schema != before_identity.schema
    assert (
        after_identity.execution_authority is after_identity.startup_admission is False
    )
    assert await subject._inventory(owner, after_pins) == final
    async with owner.connect() as connection:
        assert await connection.scalar(namespace_count) == 0
    subject._preserved(before, final)
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
        ) == "0058"
        assert (
            await connection.scalar(
                subject.catalog.NEW_LIVE_ACCESS,
                {
                    "tables": list(subject.ADDED_TABLES),
                    "functions": list(subject._live_functions()),
                },
            )
        ) is True
        assert tuple(
            (
                await connection.execute(
                    text(
                        "SELECT daily_pnl,trades_today,consecutive_losses,peak_equity FROM public.demo_automation_state WHERE id=1"
                    )
                )
            ).one()
        ) == (Decimal("-12.5"), 13, 3, Decimal(1234))
    # A stale57 command is refused, without a second migration or grant.
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert await subject._inventory(owner, after_pins) == final


async def test_actual0057_owner58_final_ddl_failure_restores_original_head_rows_oids_and_acl(
    _actual57_owner_profile_lab, monkeypatch
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before = await subject._inventory(owner, pins)
    identity, _ = await subject._role_observation(selected, owner, pins, "0057")
    original = subject._migrate
    calls = []

    def fail_second(connection, current_script):
        calls.append("DDL")
        original(connection, current_script)
        if len(calls) == 2:
            raise RuntimeError("declared disposable-lab failure after final DDL")

    monkeypatch.setattr(subject, "_migrate", fail_second)
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert calls == ["DDL", "DDL"]
    assert await subject._inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0057")
    assert restored == identity


async def test_actual0057_owner58_unreviewed_live_access_rolls_back_without_granting_or_commit(
    _actual57_owner_profile_lab, monkeypatch
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    before = await subject._inventory(owner, pins)
    original = subject._migrate

    def unexpected_access(connection, current_script):
        original(connection, current_script)
        # Synthetic DDL fault injection only in this disposable owner fixture.
        connection.exec_driver_sql(
            'GRANT SELECT ON public.native_live_controls_v1 TO "'
            + pins.role_names[0]
            + '"'
        )

    monkeypatch.setattr(subject, "_migrate", unexpected_access)
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert await subject._inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0057")
    assert restored.role_oids == pins.role_oids


@pytest.mark.parametrize("failure_pass", [1, 2])
async def test_actual0057_owner58_null_bitmap_refuses_rehearsal_or_final_null_flip_and_restores_all_state(
    _actual57_owner_profile_lab, monkeypatch, failure_pass
):
    owner, selected, pins, script = _actual57_owner_profile_lab
    audit_id = uuid4()
    async with owner.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO public.audit_logs(id,actor,action,resource_type,resource_id,\"before\") VALUES(:id,'declared_lab','retained_null','synthetic','owner-row',NULL)"
            ),
            {"id": audit_id},
        )
    before = await subject._inventory(owner, pins)
    identity, _ = await subject._role_observation(selected, owner, pins, "0057")
    original = subject._migrate
    calls = []
    differential = []

    def flip_on_selected_pass(connection, current_script):
        original(connection, current_script)
        calls.append(True)
        if len(calls) == failure_pass:
            prior = connection.execute(
                text("SELECT to_jsonb(r)::text FROM public.audit_logs r WHERE id=:id"),
                {"id": audit_id},
            ).scalar_one()
            connection.execute(
                text(
                    "UPDATE public.audit_logs SET \"before\"='null'::jsonb WHERE id=:id"
                ),
                {"id": audit_id},
            )
            current = connection.execute(
                text("SELECT to_jsonb(r)::text FROM public.audit_logs r WHERE id=:id"),
                {"id": audit_id},
            ).scalar_one()
            differential.append(prior == current)

    monkeypatch.setattr(subject, "_migrate", flip_on_selected_pass)
    with pytest.raises(subject.OwnerProfileUpgrade0058Refused):
        await subject._commit_once(script, pins, owner, before)
    assert len(calls) == failure_pass and differential == [True]
    assert await subject._inventory(owner, pins) == before
    restored, _ = await subject._role_observation(selected, owner, pins, "0057")
    assert restored == identity
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text('SELECT "before" IS NULL FROM public.audit_logs WHERE id=:id'),
                {"id": audit_id},
            )
            is True
        )


async def test_actual0057_shared_snapshot_rejects_user_domain_before_selecting_its_rows(
    _actual57_owner_profile_lab,
):
    owner, _selected, pins, _script = _actual57_owner_profile_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE DOMAIN public.unreviewed_row_output AS jsonb"
        )
        await connection.exec_driver_sql(
            'ALTER TABLE public.audit_logs ALTER COLUMN "before" TYPE public.unreviewed_row_output USING "before"::public.unreviewed_row_output'
        )
    with pytest.raises(subject.retained_rows.heap_am.NativeOwnerStorageRefused):
        await subject._inventory(owner, pins)


async def test_actual0057_shared_snapshot_never_calls_active_public_json_overload(
    _actual57_owner_profile_lab,
):
    owner, _selected, pins, _script = _actual57_owner_profile_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql("""CREATE FUNCTION public.to_jsonb(anyelement) RETURNS jsonb LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'declared_foreign_json_callback_invoked'; END $$""")
    async with owner.connect() as connection, connection.begin():
        for sql in subject.parent.old._READBACK_SETTINGS:
            await connection.exec_driver_sql(sql)
        await connection.exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
        savepoint = await connection.begin_nested()
        with pytest.raises(DBAPIError) as blocked:
            await connection.exec_driver_sql("SELECT to_jsonb(1)")
        assert blocked.value.orig.sqlstate == "P0001"
        await savepoint.rollback()
        relations = await subject.parent._relations(connection, lock=False)
        snapshot = await subject.retained_rows.snapshot_rows_v2(
            connection,
            relations,
            owner_oid=pins.route.owner_oid,
            marker_scope=subject.retained_rows.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
            max_rows=subject.parent._MAX_ROWS,
            max_table_bytes=subject.parent._MAX_ROW_BYTES,
            max_total_bytes=subject.parent._MAX_ROW_BYTES
            * subject.parent._MAX_RELATIONS,
        )
        assert snapshot["row_encoding"] == subject.retained_rows.ROW_ENCODING
        assert {row[0] for row in snapshot["rows"]} == {row[0] for row in relations} - {
            "alembic_version"
        }


async def test_actual0057_shared_snapshot_rejects_custom_table_am_before_row_or_storage_read(
    _actual57_owner_profile_lab,
):
    import re

    from sqlalchemy import event

    owner, _selected, pins, _script = _actual57_owner_profile_lab
    name = "ctcc_unreviewed_heap_" + uuid4().hex[:12]
    async with owner.begin() as connection:
        # Safe lab alias uses the genuine builtin handler; no unknown binary
        # is installed or executed. A matching handler cannot authorize an
        # unreviewed access-method OID/name, even when native row types match.
        await connection.exec_driver_sql(
            'CREATE ACCESS METHOD "'
            + name
            + '" TYPE TABLE HANDLER pg_catalog.heap_tableam_handler'
        )
        await connection.exec_driver_sql(
            'ALTER TABLE public.audit_logs SET ACCESS METHOD "' + name + '"'
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT c.relam<>2 FROM pg_catalog.pg_class c WHERE c.oid='public.audit_logs'::pg_catalog.regclass"
                )
            )
            is True
        )
        assert (
            await connection.scalar(text("SELECT count(*) FROM public.audit_logs")) == 0
        )
    opened = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if (
            re.search(r"\bFROM\s+public\.", statement, re.IGNORECASE)
            or "pg_relation_size(" in statement
        ):
            opened.append(True)

    event.listen(owner.sync_engine, "before_cursor_execute", watch)
    try:
        async with owner.connect() as connection, connection.begin():
            for sql in subject.parent.old._READBACK_SETTINGS:
                await connection.exec_driver_sql(sql)
            relations = await subject.parent._relations(connection, lock=False)
            with pytest.raises(subject.retained_rows.RetainedRowsV2Refused):
                await subject.retained_rows.snapshot_rows_v2(
                    connection,
                    relations,
                    owner_oid=pins.route.owner_oid,
                    marker_scope=subject.retained_rows.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
                    max_rows=subject.parent._MAX_ROWS,
                    max_table_bytes=subject.parent._MAX_ROW_BYTES,
                    max_total_bytes=subject.parent._MAX_ROW_BYTES
                    * subject.parent._MAX_RELATIONS,
                )
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", watch)
    assert opened == []


@pytest.mark.parametrize("foreign", ["index_am", "operator_class", "predicate"])
async def test_actual0057_native_btree_closure_refuses_before_any_index_callback_or_app_read(
    _actual57_owner_profile_lab, foreign
):
    """Natural DDL on an empty table; no foreign binary or system-row edit."""
    import re

    from sqlalchemy import event

    owner, _selected, pins, _script = _actual57_owner_profile_lab
    nonce = uuid4().hex[:12]
    method = "ctcc_declared_btree_" + nonce
    opclass = "ctcc_declared_text_ops_" + nonce
    canary = "ctcc_declared_compare_" + nonce
    async with owner.begin() as connection:
        assert (
            await connection.scalar(text("SELECT count(*) FROM public.audit_logs")) == 0
        )
        if foreign == "index_am":
            # A new AM identity is refused even with the genuine builtin
            # btree handler and comparator. No unknown shared library exists.
            await connection.exec_driver_sql(
                'CREATE ACCESS METHOD "'
                + method
                + '" TYPE INDEX HANDLER pg_catalog.bthandler'
            )
            comparator = "pg_catalog.bttextcmp"
        else:
            method = "btree"
            await connection.exec_driver_sql(
                'CREATE FUNCTION public."'
                + canary
                + '"(text,text) RETURNS integer LANGUAGE plpgsql IMMUTABLE STRICT AS $$ '
                "BEGIN RAISE EXCEPTION 'declared_index_callback_must_not_run'; END $$"
            )
            comparator = 'public."' + canary + '"'
            savepoint = await connection.begin_nested()
            with pytest.raises(DBAPIError) as called:
                await connection.exec_driver_sql("SELECT " + comparator + "('a','b')")
            assert called.value.orig.sqlstate == "P0001"
            await savepoint.rollback()
        if foreign == "predicate":
            await connection.exec_driver_sql(
                'CREATE INDEX "ctcc_declared_index_'
                + nonce
                + '" ON public.audit_logs(actor) WHERE '
                + comparator
                + "(actor::pg_catalog.text,'x')=0"
            )
        else:
            await connection.exec_driver_sql(
                'CREATE OPERATOR CLASS public."'
                + opclass
                + '" FOR TYPE text USING "'
                + method
                + '" AS '
                "OPERATOR 1 pg_catalog.<(text,text), OPERATOR 2 pg_catalog.<=(text,text), "
                "OPERATOR 3 pg_catalog.=(text,text), OPERATOR 4 pg_catalog.>=(text,text), "
                "OPERATOR 5 pg_catalog.>(text,text), FUNCTION 1 "
                + comparator
                + "(text,text)"
            )
            await connection.exec_driver_sql(
                'CREATE INDEX "ctcc_declared_index_'
                + nonce
                + '" ON public.audit_logs USING "'
                + method
                + '"(actor public."'
                + opclass
                + '")'
            )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid WHERE c.relname=:name"
                ),
                {"name": "ctcc_declared_index_" + nonce},
            )
            == 1
        )
    opened = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\bFROM\s+public\.", statement, re.IGNORECASE) or any(
            token in statement
            for token in ("pg_relation_size(", "pg_get_indexdef(", "pg_get_expr(")
        ):
            opened.append(True)

    event.listen(owner.sync_engine, "before_cursor_execute", watch)
    try:
        with pytest.raises(subject.retained_rows.heap_am.NativeOwnerStorageRefused):
            await subject._inventory(owner, pins)
        # Direct shared codec does not depend on an owner wrapper for safety.
        async with owner.connect() as connection, connection.begin():
            for sql in subject.parent.old._READBACK_SETTINGS:
                await connection.exec_driver_sql(sql)
            relations = await subject.parent._relations(connection, lock=False)
            with pytest.raises(subject.retained_rows.RetainedRowsV2Refused):
                await subject.retained_rows.snapshot_rows_v2(
                    connection,
                    relations,
                    owner_oid=pins.route.owner_oid,
                    marker_scope=subject.retained_rows.MarkerScopeV2.EXCLUDE_UPGRADE_MARKER,
                    max_rows=subject.parent._MAX_ROWS,
                    max_table_bytes=subject.parent._MAX_ROW_BYTES,
                    max_total_bytes=subject.parent._MAX_ROW_BYTES
                    * subject.parent._MAX_RELATIONS,
                )
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", watch)
    assert opened == []


async def test_actual0057_unused_custom_default_am_refuses_before_head_or_upgrade(
    _actual57_owner_profile_lab,
):
    """Existing relations stay native; only the future-DDL default drifts."""
    import re

    from sqlalchemy import event

    owner, _selected, pins, _script = _actual57_owner_profile_lab
    before = await subject._inventory(owner, pins)
    name = "ctcc_unused_heap_" + uuid4().hex[:12]
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            'CREATE ACCESS METHOD "'
            + name
            + '" TYPE TABLE HANDLER pg_catalog.heap_tableam_handler'
        )
        await connection.exec_driver_sql(
            'ALTER DATABASE "'
            + owner.url.database
            + '" SET default_table_access_method TO "'
            + name
            + '"'
        )
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT pg_catalog.current_setting('default_table_access_method')")
            )
            == name
        )
        assert (
            await connection.scalar(
                text("""SELECT NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class c
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 AND c.relkind='r' AND c.relam<>2)""")
            )
            is True
        )
    opened = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        if re.search(
            r"\bFROM\s+public\.", statement, re.IGNORECASE
        ) or statement.lstrip().upper().startswith(("CREATE TABLE", "ALTER TABLE")):
            opened.append(True)

    event.listen(owner.sync_engine, "before_cursor_execute", watch)
    try:
        with pytest.raises(subject.retained_rows.heap_am.NativeOwnerStorageRefused):
            await subject._inventory(owner, pins)
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", watch)
    assert (
        opened == []
        and before["snapshot"]["row_encoding"] == subject.retained_rows.ROW_ENCODING
    )
