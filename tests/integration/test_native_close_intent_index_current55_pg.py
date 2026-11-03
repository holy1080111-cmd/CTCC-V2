"""Actual55 metadata/migration and real once-index SQL mechanisms only.

Synthetic fixture rows do not mint native Source, close, report or write caps.
The exact full pipeline/native-role proof remains Root's separate obligation.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain import collector_current_0055_catalog_v1 as profile
from app.domain import native_close_intent_catalog_v55 as catalog
from app.domain import native_close_intent_schema_v55 as schema
from app.domain import native_leverage_configuration_catalog_v55 as configuration
from app.domain import native_leverage_configuration_enforcement_v55 as enforcement
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- Register shared pytest fixture.
)
from tests.controlled_current55_fixtures import migrate_current55 as _migrate

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _configuration_guard_oid(value, owner):
    observed = configuration.observe_catalog(
        value, owner=owner, actor=owner, owner_mode=True
    )
    return observed["function_oids"][enforcement.FUNCTION]


async def _observed_index(connection, value):
    owner = value["tables"][0]["owner"]
    actual = await connection.scalar(text(configuration.CATALOG_SQL))
    return catalog.observe_catalog(
        value,
        owner=owner,
        actor=owner,
        owner_mode=True,
        configuration_guard_oid=_configuration_guard_oid(actual, owner),
    )


async def test_actual_installed55_index_and_full_observer_marker_are_data(
    controlled_current55_database,
):
    async with (
        controlled_current55_database.connect() as connection,
        connection.begin(),
    ):
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        # The native observer deliberately requires a fixed trusted path.
        # Configure this actual owned transaction, then read it back before
        # asking the unchanged full catalog verifier to inspect anything.
        await connection.execute(
            text("SET LOCAL search_path = pg_catalog, public, pg_temp")
        )
        assert (
            await connection.scalar(text("SELECT current_setting('search_path')"))
            == "pg_catalog, public, pg_temp"
        )
        actual = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
        observed = profile.observe_actual_owner_catalog(actual)
        assert observed["native_close_intent_index_sha256"] == schema.INDEX_SPEC_SHA256
        assert observed["native_baseline_admission"] is False
        owner = actual["issued"]["owner"]
        result = catalog.observe_catalog(
            actual["native_close_intent55"],
            owner=owner,
            actor=owner,
            owner_mode=True,
            configuration_guard_oid=_configuration_guard_oid(
                actual["native_configuration55"], owner
            ),
        )
        assert result["predicate"] in schema.INDEX_SPEC_JSON.replace("\\'", "'")
        assert set(result) == {"index", "predicate", "triggers"}


@pytest.mark.parametrize("mutation", ("nonunique", "predicate", "disabled_append_only"))
async def test_actual_index_or_append_only_drift_is_not_observation(
    controlled_current55_database, mutation
):
    async with controlled_current55_database.connect() as connection:
        transaction = await connection.begin()
        try:
            baseline = await connection.scalar(text(catalog.CATALOG_SQL))
            owner = baseline["tables"][0]["owner"]
            actual_configuration = await connection.scalar(
                text(configuration.CATALOG_SQL)
            )
            guard_oid = _configuration_guard_oid(actual_configuration, owner)
            catalog.observe_catalog(
                baseline,
                owner=owner,
                actor=owner,
                owner_mode=True,
                configuration_guard_oid=guard_oid,
            )
            if mutation == "disabled_append_only":
                await connection.exec_driver_sql(
                    "ALTER TABLE public.qualification_submission_outcomes DISABLE TRIGGER qualification_submission_outcomes_immutable"
                )
            else:
                await connection.exec_driver_sql(
                    "DROP INDEX public.qualification_native_close_intent_once_v55"
                )
                statement = schema.INDEX_SQL
                if mutation == "nonunique":
                    statement = statement.replace(
                        "CREATE UNIQUE INDEX", "CREATE INDEX", 1
                    )
                else:
                    statement = statement.split(" WHERE ", 1)[0] + " WHERE true"
                await connection.exec_driver_sql(statement)
            actual = await connection.scalar(text(catalog.CATALOG_SQL))
            with pytest.raises(catalog.NativeCloseIntentCatalogError):
                catalog.observe_catalog(
                    actual,
                    owner=owner,
                    actor=owner,
                    owner_mode=True,
                    configuration_guard_oid=guard_oid,
                )
        finally:
            await transaction.rollback()


async def test_empty_down_reup_removes_then_restores_exact_once_index(
    controlled_current55_database,
):
    async with controlled_current55_database.begin() as connection:
        before = await connection.scalar(text(catalog.CATALOG_SQL))
        expected = await _observed_index(connection, before)
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
        assert (
            await connection.scalar(
                text("SELECT version_num FROM public.alembic_version")
            )
            == "0054"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.qualification_native_close_intent_once_v55') IS NULL"
                )
            )
            is True
        )
        await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        after = await connection.scalar(text(catalog.CATALOG_SQL))
        assert await _observed_index(connection, after) == expected


async def test_real_two_session_once_index_race_does_not_block_other_capture_versions(
    controlled_current55_database,
):
    # Only the relation/index names are mapped to a disposable mechanism table.
    # No actual ledger/outcome/issuer/Control guard is disabled or forged.
    engine = controlled_current55_database
    suffix = uuid4().hex
    table = "ctcc_close_once_" + suffix
    index = "ctcc_close_once_idx_" + suffix
    statement = schema.INDEX_SQL.replace(
        "qualification_native_close_intent_once_v55", index, 1
    ).replace("public.qualification_submission_outcomes", "public." + table, 1)
    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            'CREATE TABLE public."'
            + table
            + '" (reservation_id text NOT NULL,capture_json text NOT NULL)'
        )
        await connection.exec_driver_sql(statement)
    capture = '{"version":"ctcc.native_controlled_close_intent.v1"}'
    insert = text('INSERT INTO public."' + table + '" VALUES (:reservation,:capture)')
    observations = []
    both_connected = asyncio.Event()
    arrivals = 0

    async def attempt():
        nonlocal arrivals
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL statement_timeout='5000ms'"))
                observations.append(
                    tuple(
                        (
                            await connection.execute(
                                text("SELECT pg_backend_pid(),txid_current()::text")
                            )
                        ).one()
                    )
                )
                arrivals += 1
                if arrivals == 2:
                    both_connected.set()
                await asyncio.wait_for(both_connected.wait(), 5)
                await connection.execute(
                    insert, {"reservation": "same-synthetic-event", "capture": capture}
                )
            return "committed"
        except DBAPIError as error:
            return getattr(getattr(error, "orig", None), "sqlstate", None)

    try:
        results = await asyncio.gather(attempt(), attempt())
        assert sorted(results) == ["23505", "committed"]
        assert (
            len({value[0] for value in observations})
            == len({value[1] for value in observations})
            == 2
        )
        async with engine.begin() as connection:
            await connection.execute(
                insert, {"reservation": "different-event", "capture": capture}
            )
            other = '{"version":"ctcc.native_read_parts.v1"}'
            await connection.execute(
                insert, {"reservation": "same-synthetic-event", "capture": other}
            )
            await connection.execute(
                insert, {"reservation": "same-synthetic-event", "capture": other}
            )
            assert (
                await connection.scalar(
                    text('SELECT count(*) FROM public."' + table + '"')
                )
                == 4
            )
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql('DROP TABLE public."' + table + '"')


async def test_upgrade_refuses_existing_index_identity_before_any55_install(
    controlled_current55_database,
):
    engine = controlled_current55_database
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
        await connection.exec_driver_sql(
            schema.INDEX_SQL.replace("CREATE UNIQUE INDEX", "CREATE INDEX", 1)
        )
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(RuntimeError, match="native_close_index_already_exists"):
                await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        finally:
            await transaction.rollback()
    async with engine.begin() as connection:
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
        await connection.exec_driver_sql(
            "DROP INDEX public.qualification_native_close_intent_once_v55"
        )
        await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        value = await connection.scalar(text(catalog.CATALOG_SQL))
        await _observed_index(connection, value)
