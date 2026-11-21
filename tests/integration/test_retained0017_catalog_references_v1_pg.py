"""Two fresh isolated source17 schemas, never a retained-host or venue proof.

Each parameter creates a different database and non-superuser owner. Required
source58 PG3 artifact replay compares both complete catalogs after both tests.
Neither a matching pair nor test output installs the product's literal seal.
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

from scripts import retained0017_reference_measurement_v1 as measurement
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _upgrade_source17(connection):
    import app.database.models  # noqa: F401 - matches migrations/env.py registration
    from app.database.base import Base
    from scripts import retained0017_catalog_baseline_v1 as comparator

    config = Config(str(comparator.ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(comparator.ROOT / "migrations"))
    graph = ScriptDirectory.from_config(config)
    assert graph.get_heads() == ["0058"]
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: graph._upgrade_revs("0017", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0017",)


@pytest.fixture(params=measurement.LABELS, ids=measurement.LABELS)
async def _independent_fresh_source17(request):
    url, tls = _isolated_tls_database_config()
    suffix = uuid4().hex[:16]
    owner_name = "ctcc_ref17_owner_" + suffix
    database_name = "ctcc_ref17_database_" + suffix
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner, role_created, database_created = None, False, False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(
                'CREATE ROLE "' + owner_name + '" LOGIN NOSUPERUSER NOCREATEDB '
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
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
        # This uses only the declared disposable TLS fixture's trust route.
        # It makes no password-authentication or production-role claim.
        owner = create_async_engine(
            url.set(database=database_name, username=owner_name, password=None),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_upgrade_source17)
        yield request.param, owner, measurement.digest(cluster.encode("ascii"))
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


async def test_actual0017_fresh_independent_catalog_reference_is_complete(
    _independent_fresh_source17,
):
    from scripts import retained0017_catalog_baseline_v1 as comparator

    label, owner, cluster_sha256 = _independent_fresh_source17
    async with owner.connect() as connection, connection.begin():
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
            "SET LOCAL search_path TO pg_catalog",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='30000ms'",
            "SET LOCAL idle_in_transaction_session_timeout='30000ms'",
        ):
            await connection.exec_driver_sql(statement)
        raw = await comparator.capture_catalog17_v1(connection)
        row = (
            await connection.execute(
                text("""SELECT d.datname::pg_catalog.text,d.oid::pg_catalog.int8,
 r.rolname::pg_catalog.text,r.oid::pg_catalog.int8,d.datdba=r.oid,
 r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,r.rolcanlogin,r.rolreplication,r.rolbypassrls,
 (SELECT pg_catalog.count(*) FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid),
 (SELECT c.oid::pg_catalog.int8 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='public' AND c.relname='alembic_version'),
 pg_catalog.current_setting('server_version_num'),
 EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)
 FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user
 WHERE d.datname=pg_catalog.current_database()""")
            )
        ).one()
        assert row[4] is True
        physical = {
            "database": row[0],
            "database_oid": row[1],
            "owner": row[2],
            "owner_oid": row[3],
            "owner_flags": dict(zip(measurement._FLAGS, row[5:12], strict=True)),
            "owner_memberships": row[12],
            "marker_relation_oid": row[13],
            "server_version_num": row[14],
            "actual_tls": row[15],
            "cluster_system_identifier_sha256": cluster_sha256,
        }
        measurement._physical(physical, raw)
        normalized = comparator.normalize_catalog17_v1(raw)
        source17 = comparator.source17_contract_v1()
        assert normalized["schema"] == "ctcc.retained0017.normalized_catalog.v1"
        assert (
            normalized["source17_prefix_sha256"] == source17["source17_prefix_sha256"]
        )
        assert raw["head"] == ["0017"]
        assert raw["profile"]["owner"] == physical["owner"]
        assert raw["profile"]["read_only"] == "on"
        assert raw["profile"]["tls"] is True
        # No test output mutates or substitutes the comparator's literal seal.
        measurement.record_reference_if_requested(
            label=label, document=raw, physical=physical
        )
