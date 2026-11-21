"""Actual disposable TLS17 read-only inventory; collection is not SQL execution.

The source is current58 but the isolated database is genuinely migrated only to
0017. Synthetic retained risk/history and an unresolved legacy Live intent are
kept intact. No host database, exchange client, key, Arm or order is involved.
"""

import asyncio
import json
import os
import secrets
import sys
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import current0056_fitted_catalog_measurement_v1 as source_measurement
from scripts import retained0017_owner_plan_v1 as subject
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)
from tests.native_private_temp_v1 import _private_root_path

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _upgrade_original17(connection):
    config = Config(str(subject.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(subject.ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    assert graph.get_heads() == ["0058"]
    migration = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: graph._upgrade_revs("0017", heads)}
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0017",)


@pytest.fixture
async def _actual_retained17_lab():
    url, tls = _isolated_tls_database_config()
    name = "ctcc_retained17_" + uuid4().hex[:12]
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner, created = None, False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql('CREATE DATABASE "' + name + '"')
            created = True
        owner = create_async_engine(
            url.set(database=name),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_upgrade_original17)
            await connection.execute(
                text("""INSERT INTO public.demo_automation_state
 (id,armed,emergency_stop,locked,lock_reasons,session_date,baseline_equity,peak_equity,daily_pnl,trades_today,consecutive_losses,risk_peak_equity,realized_pnl_events)
 VALUES(1,false,true,true,'["retained_uncertain_requires_review"]',current_date,300,325,-12.5,13,3,330,
 '[{"net_pnl":"-12.5","source":"declared-synthetic-retained-history"}]')""")
            )
            await connection.execute(
                text("""INSERT INTO public.okx_live_execution_intents
 (idempotency_key,request_hash,action,status,instrument_id,client_order_id,detail_codes)
 VALUES('syntheticretainedintent01',:hash,'place_order','ambiguous','BTC-USDT-SWAP','syntheticretainedclient01','["unknown_exchange_result"]')"""),
                {"hash": "a" * 64},
            )
            actor = tuple((await connection.execute(subject.route._PROFILE_SQL)).one())
        pins = subject.RetainedOwnerPins0017V1(
            "a" * 40,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            subject.RetainedOwnerRoute0017V1(
                "e" * 64,
                "f" * 64,
                subject._sha(actor[11].encode("ascii")),
                owner.url.host,
                owner.url.port or 5432,
                actor[2],
                actor[0],
                actor[5],
                actor[4],
            ),
        )
        yield owner, pins, subject._source_plan()
    finally:
        if owner is not None:
            await owner.dispose()
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execution_options(isolation_level="AUTOCOMMIT")
                    await connection.exec_driver_sql(
                        'DROP DATABASE "' + name + '" WITH (FORCE)'
                    )
        finally:
            await admin.dispose()


async def _read(owner, pins, plan):
    async with owner.connect() as connection:
        transaction = await connection.begin()
        try:
            for statement in subject._READ_SETTINGS:
                await connection.exec_driver_sql(statement)
            return await subject._snapshot(connection, pins, plan)
        finally:
            await transaction.rollback()


async def test_actual0017_custom_marker_am_refuses_before_head_row_or_storage_read(
    _actual_retained17_lab,
):
    import re

    from sqlalchemy import event

    owner, pins, plan = _actual_retained17_lab
    name = "ctcc_unreviewed_marker_" + uuid4().hex[:12]
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            'CREATE ACCESS METHOD "'
            + name
            + '" TYPE TABLE HANDLER pg_catalog.heap_tableam_handler'
        )
        await connection.exec_driver_sql(
            'ALTER TABLE public.alembic_version SET ACCESS METHOD "' + name + '"'
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
        with pytest.raises(subject.Retained0017PlanRefused):
            await _read(owner, pins, plan)
    finally:
        event.remove(owner.sync_engine, "before_cursor_execute", watch)
    assert opened == []


async def test_actual0017_two_readonly_snapshots_preserve_all_rows_oids_sequences_and_unknown_intent(
    _actual_retained17_lab,
):
    owner, pins, plan = _actual_retained17_lab
    first = await _read(owner, pins, plan)
    second = await _read(owner, pins, plan)
    value = subject._join(first, second, pins, plan)
    assert value["two_reads_equal"] is True
    assert (
        value["exchange_flat"] is None and value["schema_upgrade_authorized"] is False
    )
    tables = {row[0]: row for row in first["tables"]}
    assert set(tables) == set(plan["minimum17_columns"])
    assert tables["demo_automation_state"][3] == 1
    assert tables["okx_live_execution_intents"][3] == 1
    assert first["sequences"]
    async with owner.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0017"
        )
        assert (
            await connection.scalar(
                text("SELECT status FROM public.okx_live_execution_intents")
            )
            == "ambiguous"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT operator_reconciled_at FROM public.okx_live_execution_intents"
                )
            )
            is None
        )
        assert tuple(
            (
                await connection.execute(
                    text(
                        "SELECT daily_pnl,trades_today,consecutive_losses,risk_peak_equity FROM public.demo_automation_state"
                    )
                )
            ).one()
        ) == (-12.5, 13, 3, 330)
    reviewed = replace(pins, expected_catalog_sha256=first["catalog_sha256"])
    assert (
        subject._join(first, second, reviewed, plan)["expected_catalog_pin_matched"]
        is True
    )


async def test_actual0017_outer_cli_replays_admitted_source_private_pins_tls_and_receipt(
    _actual_retained17_lab, tmp_path
):
    owner, pins, plan = _actual_retained17_lab
    identity, files, source_map = source_measurement._read_hermetic_inputs()
    source = source_measurement.source_identity(identity, files, source_map)
    archive = Path("/ctcc-admitted-test-source.tar")
    assert (
        archive.is_file()
        and subject._sha(archive.read_bytes()) == identity["archive_sha256"]
    )
    first = await _read(owner, pins, plan)
    pins = replace(
        pins,
        source_commit=source["commit"],
        source_tree=source["tree"],
        source_archive_sha256=identity["archive_sha256"],
        preserved_backup_sha256=subject._sha(
            b"declared synthetic retained17 lab, not a restored host backup"
        ),
        expected_catalog_sha256=first["catalog_sha256"],
        route=replace(
            pins.route,
            source_manifest_sha256=source["manifest_sha256"],
            ca_sha256=os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_SHA256"],
        ),
    )
    private = _private_root_path(tmp_path)
    password = secrets.token_hex(
        24
    )  # Explicit isolated trust-auth fixture, not password authentication evidence.
    dsn = private / "owner-dsn"
    dsn.write_text(
        owner.url.set(port=pins.route.port, password=password).render_as_string(
            hide_password=False
        ),
        encoding="utf-8",
    )
    dsn.chmod(0o600)
    raw = subject._canonical({"schema": subject.PINS_SCHEMA, **asdict(pins)})
    pins_file = private / "reviewed-pins.json"
    pins_file.write_bytes(raw)
    pins_file.chmod(0o600)
    assert subject._load_pins(pins_file, subject._sha(raw)) == pins
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-B",
        "-m",
        "scripts.retained0017_owner_plan_v1",
        "--inspect-retained-clone",
        "--reviewed-pins-file",
        str(pins_file),
        "--reviewed-pins-sha256",
        subject._sha(raw),
        "--owner-dsn-file",
        str(dsn),
        "--ca-file",
        os.environ["CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE"],
        "--source-archive-file",
        str(archive),
        "--private-receipt-directory",
        str(private),
        cwd=subject.ROOT,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert password.encode() not in stdout + stderr
    assert owner.url.host.encode() not in stdout + stderr
    assert process.returncode == 2, (process.returncode, stdout.decode()[-2000:])
    public = json.loads(stdout)
    assert (
        public["status"] == "OBSERVED_MATCH_DATA" and public["database_head"] == "0017"
    )
    assert public["exchange_flat"] is None and public["execution_authority"] is False
    digest = public["private_receipt_sha256"]
    retained = (private / ("retained0017-plan-" + digest + ".json")).read_bytes()
    assert subject._sha(retained) == digest
    receipt = json.loads(retained)
    assert receipt["source_commit"] == identity["commit"]
    assert receipt["source_tree"] == identity["tree"]
    assert receipt["snapshot"] == first
    assert receipt["independent_reads"] == 2
    assert receipt["backup_to_database_lineage_verified"] is False
    assert await _read(owner, pins, plan) == first


async def test_actual0017_row_hash_encoding_distinguishes_sql_null_from_json_null(
    _actual_retained17_lab,
):
    owner, _pins, _plan = _actual_retained17_lab
    expression = subject._row_expression(["payload"])
    async with owner.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT to_jsonb(r)::text,"
                    + expression
                    + " FROM (VALUES(NULL::jsonb),('null'::jsonb)) r(payload)"
                )
            )
        ).all()
    assert len(rows) == 2 and rows[0][0] == rows[1][0]
    assert rows[0][1] != rows[1][1]
    assert subject._sha(rows[0][1].encode()) != subject._sha(rows[1][1].encode())


@pytest.mark.parametrize("head", ["0016", "0018", "0058"])
async def test_actual0017_readonly_inventory_refuses_changed_actual_head(
    _actual_retained17_lab, head
):
    owner, pins, plan = _actual_retained17_lab
    async with owner.begin() as connection:
        await connection.execute(
            text("UPDATE public.alembic_version SET version_num=:head"), {"head": head}
        )
    with pytest.raises(subject.archive.NativeOwnerUpgradeRefusal):
        await _read(owner, pins, plan)


async def test_actual0017_readonly_inventory_refuses_missing_original_risk_column(
    _actual_retained17_lab,
):
    owner, pins, plan = _actual_retained17_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.demo_automation_state DROP COLUMN risk_peak_equity"
        )
    with pytest.raises(subject.Retained0017PlanRefused):
        await _read(owner, pins, plan)


async def test_actual0017_head_view_is_refused_before_its_select_function_runs(
    _actual_retained17_lab,
):
    owner, pins, plan = _actual_retained17_lab
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "ALTER TABLE public.alembic_version RENAME TO retained_version_original"
        )
        await connection.exec_driver_sql("""CREATE FUNCTION public.retained_head_callback() RETURNS text LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'declared_test_callback_must_never_run'; END $$""")
        await connection.exec_driver_sql(
            "CREATE VIEW public.alembic_version AS SELECT public.retained_head_callback() AS version_num"
        )
    # A query of the fake version view would raise a real DBAPI error instead.
    # This fixed refusal proves catalog-only validation precedes its execution.
    with pytest.raises(subject.Retained0017PlanRefused):
        await _read(owner, pins, plan)


@pytest.mark.parametrize("outside", ["user_schema", "large_object"])
async def test_actual0017_readonly_inventory_refuses_unobserved_storage_scope(
    _actual_retained17_lab, outside
):
    owner, pins, plan = _actual_retained17_lab
    async with owner.begin() as connection:
        if outside == "user_schema":
            await connection.exec_driver_sql("CREATE SCHEMA retained_unreviewed")
        else:
            assert await connection.scalar(text("SELECT pg_catalog.lo_create(0)")) > 0
    with pytest.raises(subject.Retained0017PlanRefused):
        await _read(owner, pins, plan)


@pytest.mark.parametrize("drift", ["row", "sequence", "function_acl"])
async def test_actual0017_independent_reads_refuse_retained_state_or_catalog_drift(
    _actual_retained17_lab, drift
):
    owner, pins, plan = _actual_retained17_lab
    first = await _read(owner, pins, plan)
    async with owner.begin() as connection:
        if drift == "row":
            await connection.exec_driver_sql(
                "UPDATE public.demo_automation_state SET daily_pnl=-13.5"
            )
        elif drift == "sequence":
            name = first["sequences"][0][0]
            assert subject._NAME.fullmatch(name)
            await connection.exec_driver_sql(
                "SELECT nextval('public.\"" + name + "\"')"
            )
        else:
            await connection.exec_driver_sql(
                "REVOKE EXECUTE ON FUNCTION public.qualification_reservation_update() FROM PUBLIC"
            )
    second = await _read(owner, pins, plan)
    with pytest.raises(subject.Retained0017PlanRefused):
        subject._join(first, second, pins, plan)
