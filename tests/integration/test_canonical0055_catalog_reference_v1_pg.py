"""One actual canonical55 structural DATA capture, never Paper admission.

Unlike the unchanged native-storage boolean fixtures, this new database is
built directly from empty through55 with migrations/env.py's real metadata
naming convention. The historical0050 SUPER requirement is honored and
reported; no restricted/runtime owner or credential proof is inferred.
"""

from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from scripts import canonical0055_catalog_reference_v1 as reference
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import native_owner_heap_am_v1 as storage
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _upgrade_canonical55(connection):
    import app.database.models  # noqa: F401 - same registration as migrations/env.py
    from app.database.base import Base

    scope = scopes.database0055_source_scope(reference.ROOT)
    config = Config(str(reference.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(reference.ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    assert graph.get_heads() == [scope.actual_source_head] == ["0058"]
    assert not MigrationContext.configure(connection).get_current_heads()
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: graph._upgrade_revs("0055", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0055",)
    assert (
        scopes.verify_database_scope(connection, reference.ROOT, scope)["database_head"]
        == "0055"
    )


@pytest.fixture
async def _canonical_fresh55():
    url, tls = _isolated_tls_database_config()
    suffix = uuid4().hex[:16]
    owner_name = "ctcc_ref55_owner_" + suffix
    database_name = "ctcc_ref55_database_" + suffix
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner, role_created, database_created = None, False, False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            # Immutable0050 explicitly requires a SUPER owner. Keep that
            # physical fact visible rather than relabeling a later actor.
            await connection.exec_driver_sql(
                'CREATE ROLE "'
                + owner_name
                + '" LOGIN SUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS'
            )
            role_created = True
            await connection.exec_driver_sql(
                'CREATE DATABASE "'
                + database_name
                + '" OWNER "'
                + owner_name
                + '" TEMPLATE template0'
            )
            database_created = True
            cluster = await connection.scalar(
                text(
                    "SELECT system_identifier::pg_catalog.text FROM pg_catalog.pg_control_system()"
                )
            )
        assert type(cluster) is str and cluster.isascii() and cluster.isdecimal()
        owner = create_async_engine(
            url.set(database=database_name, username=owner_name, password=None),
            poolclass=NullPool,
            connect_args={
                "ssl": tls,
                "server_settings": {
                    "session_preload_libraries": "",
                    "local_preload_libraries": "",
                    "role": "none",
                    "search_path": "pg_catalog",
                    "timezone": "UTC",
                },
            },
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                "SET LOCAL search_path TO public, pg_catalog, pg_temp"
            )
            await connection.run_sync(_upgrade_canonical55)
        yield owner, reference.digest(cluster.encode("ascii"))
    finally:
        if owner is not None:
            await owner.dispose()
        try:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                if database_created:
                    await connection.exec_driver_sql(
                        'DROP DATABASE "' + database_name + '" WITH (FORCE)'
                    )
                if role_created:
                    await connection.exec_driver_sql('DROP ROLE "' + owner_name + '"')
        finally:
            await admin.dispose()


async def test_actual0055_canonical_full_catalog_reference_has_native_source_and_tls_proof(
    _canonical_fresh55,
):
    from app.domain import collector_current_0055_catalog_v1 as native55
    from scripts import native_owner_retained_rows_v2 as retained_rows
    from scripts import retained0017_catalog_baseline_v1 as catalog_sql
    from scripts import retained0055_negative_stop_reassert_v1 as negative_stop

    owner, cluster_sha256 = _canonical_fresh55
    raw_predicates = reference.raw_predicates_v1()
    async with owner.connect() as connection, connection.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='30000ms'",
            "SET LOCAL idle_in_transaction_session_timeout='30000ms'",
        ):
            await connection.exec_driver_sql(statement)
        await storage.lock_native_owner_marker_v1(connection)
        for statement in raw_predicates:
            assert await connection.scalar(text(statement)) is True
        names = tuple(
            (
                await connection.execute(
                    text(
                        "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname COLLATE pg_catalog.\"C\""
                    )
                )
            ).scalars()
        )
        assert "alembic_version" in names and 48 < len(names) <= 512
        assert all(
            type(name) is str and catalog_sql._NAME.fullmatch(name) is not None
            for name in names
        )
        await connection.exec_driver_sql(
            "LOCK TABLE "
            + ",".join('public."' + name + '"' for name in names)
            + " IN ACCESS SHARE MODE NOWAIT"
        )
        await storage.require_native_owner_storage_v1(connection)
        for statement in raw_predicates:
            assert await connection.scalar(text(statement)) is True
        native = await connection.scalar(text(native55.OWNER_CATALOG_SQL))
        assert native == await connection.scalar(text(native55.OWNER_CATALOG_SQL))
        observed = native55.verify_actual_owner_catalog(native)
        assert observed["native_definitions_sha256"] == native55._required_baseline()
        assert observed["native_baseline_admission"] is True
        # The DATA query uses catalog-first and records that actual profile;
        # the native55 observer above retains its own fixed source context.
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
        raw = await connection.scalar(text(reference.catalog_query_v1()))
        repeated = await connection.scalar(text(reference.catalog_query_v1()))
        assert raw == repeated
        row = (
            await connection.execute(
                text("""SELECT d.datname::pg_catalog.text,d.oid::pg_catalog.int8,
 r.rolname::pg_catalog.text,r.oid::pg_catalog.int8,d.datdba=r.oid,
 (SELECT c.oid::pg_catalog.int8 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='alembic_version'),
 pg_catalog.current_setting('server_version_num'),
 EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl),
 r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,
 (SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)
 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user
 WHERE d.datname=pg_catalog.current_database()""")
            )
        ).one()
        # Run the same framed language predicate and exact Stop/audit metadata
        # read as the isolated stopped-copy plan. No data mutation is needed.
        native_layouts = tuple(
            tuple(item)
            for item in (await connection.execute(retained_rows._LAYOUT_SQL)).all()
        )
        relations = tuple(item[:3] for item in native_layouts)
        layouts = retained_rows._checked_layouts(
            native_layouts, retained_rows._expected_relations(relations), row[3]
        )
        guard_digest = await negative_stop._checked_control_audit_metadata_v1(
            connection, layouts, row[3]
        )
        assert reference._HEX.fullmatch(guard_digest) is not None
        receipt = {
            "schema": reference.RECEIPT_SCHEMA,
            "database_target": "0055",
            "canonical_naming_convention": True,
            "raw_sha256": reference.digest(reference.canonical(raw)),
            "native_owner_sha256": reference.digest(reference.canonical(native)),
            "full_typed_catalog_query_sha256": reference.digest(
                reference.catalog_query_v1().encode("utf-8")
            ),
            "native_owner_query_sha256": reference.digest(
                native55.OWNER_CATALOG_SQL.encode("utf-8")
            ),
            "native55_observation": observed,
            "public_table_names": sorted(names),
            "physical": {
                "database": row[0],
                "database_oid": row[1],
                "owner": row[2],
                "owner_oid": row[3],
                "owner_is_database_owner": row[4],
                "marker_relation_oid": row[5],
                "server_version_num": row[6],
                "actual_tls": row[7],
                "owner_flags": list(row[8:15]),
                "owner_memberships": row[15],
                "cluster_system_identifier_sha256": cluster_sha256,
            },
            "schema_admission": False,
            "startup_admission": False,
            "paper_admission": False,
            "execution_authority": False,
            "saved_reference_is_authority": False,
        }
        reference.record_if_requested_v1(raw, native, receipt)
