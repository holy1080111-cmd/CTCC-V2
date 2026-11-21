"""Two fresh source58 DATA references; no preserved state or Paper actor.

The unchanged canonical55 fixture owns each unique role/database and cleanup.
No schema/role/runtime permission is inferred for a later restricted actor.
"""

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from scripts import native_owner_heap_am_v1 as storage
from scripts import paper58_catalog_reference_v1 as reference
from tests.integration import test_canonical0055_catalog_reference_v1_pg as original55

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_canonical_fresh55 = original55._canonical_fresh55


def _upgrade_original55_to58(connection):
    import app.database.models  # noqa: F401 -- genuine migration naming context
    from app.database.base import Base

    root = reference.source.ROOT
    graph = ScriptDirectory(str(root / "migrations"))
    assert graph.get_heads() == ["0058"]
    assert MigrationContext.configure(connection).get_current_heads() == ("0055",)
    context = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: graph._upgrade_revs("0058", heads),
            "target_metadata": Base.metadata,
            "compare_type": True,
        },
    )
    # No migration helper, query result, role flag, row or source guard is
    # replaced. Original actual catalog-dependent preconditions remain on.
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0058",)


async def _native55_predecessor(owner):
    from app.domain import collector_current_0055_catalog_v1 as native55

    async with owner.connect() as connection, connection.begin():
        for query in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='30000ms'",
        ):
            await connection.exec_driver_sql(query)
        await storage.lock_native_owner_marker_v1(connection)
        for query in reference.raw_safety_queries_v1():
            assert await connection.scalar(text(query)) is True
        names = tuple(
            (
                await connection.execute(
                    text(
                        "SELECT c.relname::pg_catalog.text FROM pg_catalog.pg_class c "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relkind='r' "
                        'ORDER BY c.relname COLLATE pg_catalog."C"'
                    )
                )
            ).scalars()
        )
        assert 48 < len(names) <= 105
        assert all(
            type(name) is str and reference._NAME.fullmatch(name) for name in names
        )
        await connection.exec_driver_sql(
            "LOCK TABLE "
            + ",".join('public."' + name + '"' for name in names)
            + " IN ACCESS SHARE MODE NOWAIT"
        )
        await storage.require_native_owner_storage_v1(connection)
        for query in reference.raw_safety_queries_v1():
            assert await connection.scalar(text(query)) is True
        raw = await connection.scalar(text(native55.OWNER_CATALOG_SQL))
        assert raw == await connection.scalar(text(native55.OWNER_CATALOG_SQL))
        observed = native55.verify_actual_owner_catalog(raw)
        assert observed["native_definitions_sha256"] == native55._required_baseline()
        assert observed["native_baseline_admission"] is True
        return raw, observed


@pytest.mark.parametrize("label", ("a", "b"))
async def test_actual0058_fresh_structural_reference(label, _canonical_fresh55):
    owner, cluster_sha256 = _canonical_fresh55
    predecessor, observation = await _native55_predecessor(owner)
    async with owner.begin() as connection:
        await connection.exec_driver_sql(
            "SET LOCAL search_path TO public, pg_catalog, pg_temp"
        )
        await connection.run_sync(_upgrade_original55_to58)
    async with owner.connect() as connection, connection.begin():
        for query in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY",
            "SET LOCAL search_path TO pg_catalog",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='30000ms'",
            "SET LOCAL idle_in_transaction_session_timeout='30000ms'",
        ):
            await connection.exec_driver_sql(query)
        raw, safety = await reference.capture_fresh_catalog58_v1(connection)
    # The exporter/normalizer/pair contract is deliberately a NEW purpose.
    # Its implementation must be complete before this unsealed node can run.
    reference.record_reference_v1(
        label,
        raw,
        {
            "schema": reference.RECEIPT_SCHEMA,
            "fresh_fixture_only": True,
            "canonical55_fixture": original55.reference.MODULE,
            "original55_to58_migrations_unmodified": True,
            "fixture_only_stopped_genesis_inserted": False,
            "predecessor_native55_catalog": predecessor,
            "predecessor_native55_observation": observation,
            "cluster_system_identifier_sha256": cluster_sha256,
            "safety": safety,
            "preserved_data_opened": False,
            "owner58_actor_admission": False,
            "paper_actor_admission": False,
            "startup_admission": False,
            "execution_authority": False,
        },
    )
