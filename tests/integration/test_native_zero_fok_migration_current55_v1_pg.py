"""Future real PG17 migration/metadata mechanics; no native source or seal.

The fixture owns a fresh database. Component positives are metadata observations,
not a trading/closure owner, and remain native_baseline_admission=False.
"""

import re
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.domain import collector_current_0055_catalog_v1 as profile
from app.domain import native_filled_flat_closure_schema_v55 as schema
from tests.controlled_current55_fixtures import (
    ROOT,
)
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- preserve the actual shared fixture registration.
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _migrate(connection, target, *, down=False):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0055"
    direction = script._downgrade_revs if down else script._upgrade_revs
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: direction(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


async def _body(connection):
    return await connection.scalar(
        text(
            "SELECT encode(sha256(convert_to(p.prosrc,'UTF8')),'hex') "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname='qualification_reservation_update'"
        )
    )


async def test_fresh55_closure_metadata_is_observed_without_native_admission(
    controlled_current55_database,
):
    async with (
        controlled_current55_database.connect() as connection,
        connection.begin(),
    ):
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        actual = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
        result = profile.observe_actual_owner_catalog(actual)
        assert result["database_head"] == "0055"
        assert result["native_baseline_admission"] is False
        assert result["native_closure_body_sha256"] == schema.CURRENT_BODY_SHA256
        component = actual["native_closure55"]
        owner = component["tables"][0]["owner"]
        assert profile._closure55(component, owner=owner)["function"]
        assert await _body(connection) == schema.CURRENT_BODY_SHA256


async def test_down_reupgrade_restores_exact0027_and_reinstalls_exact55(
    controlled_current55_database,
):
    engine = controlled_current55_database
    async with engine.begin() as connection:
        assert await _body(connection) == schema.CURRENT_BODY_SHA256
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0054"
        )
        assert await _body(connection) == schema.ORIGINAL_BODY_SHA256
        await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0055"
        )
        assert await _body(connection) == schema.CURRENT_BODY_SHA256


@pytest.mark.parametrize("mutation", ("old_body", "security_definer", "disable_guard"))
async def test_actual55_component_rejects_body_and_guard_drift(
    controlled_current55_database, mutation
):
    # DDL is rolled back in this same owned connection after the observation.
    async with controlled_current55_database.connect() as connection:
        transaction = await connection.begin()
        try:
            if mutation == "old_body":
                await connection.exec_driver_sql(schema.ORIGINAL_SQL)
            elif mutation == "security_definer":
                await connection.exec_driver_sql(
                    "ALTER FUNCTION public.qualification_reservation_update() SECURITY DEFINER"
                )
            else:
                await connection.exec_driver_sql(
                    "ALTER TABLE public.qualification_reservations DISABLE TRIGGER qualification_reservation_update_guard"
                )
            actual = await connection.scalar(text(profile._CLOSURE_CATALOG_SQL))
            owner = actual["tables"][0]["owner"]
            with pytest.raises(profile.Current0055CatalogError):
                profile._closure55(actual, owner=owner)
        finally:
            await transaction.rollback()


async def test_actual_redundant_execute_grant_is_catalog_drift(
    controlled_current55_database,
):
    engine = controlled_current55_database
    role = "ctcc_close55_" + uuid4().hex
    assert re.fullmatch(r"ctcc_close55_[a-f0-9]{32}", role)
    created = False
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN')
            created = True
            await connection.exec_driver_sql(
                f'GRANT EXECUTE ON FUNCTION public.qualification_reservation_update() TO "{role}"'
            )
        async with engine.connect() as connection, connection.begin():
            actual = await connection.scalar(text(profile._CLOSURE_CATALOG_SQL))
            with pytest.raises(profile.Current0055CatalogError):
                profile._closure55(actual, owner=actual["tables"][0]["owner"])
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.exec_driver_sql(
                    f'REVOKE ALL ON FUNCTION public.qualification_reservation_update() FROM "{role}"'
                )
                await connection.exec_driver_sql(f'DROP ROLE "{role}"')


async def test_upgrade_refuses_actual_changed0027_before_any55_install(
    controlled_current55_database,
):
    engine = controlled_current55_database
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
        await connection.exec_driver_sql(
            "ALTER FUNCTION public.qualification_reservation_update() SECURITY DEFINER"
        )
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(
                RuntimeError, match="operator_0055_native_closure_function_drift"
            ):
                await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        finally:
            await transaction.rollback()
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0054"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.demo_control_clear_acknowledgements_v2') IS NULL"
                )
            )
            is True
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*)=0 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname='account_history_partition_current55_v2_append'"
                )
            )
            is True
        )
