"""Actual isolated0058 TLS schema; synthetic missing targets, no account/order.

Collection alone is not execution evidence. This case requires the existing
explicit disposable TLS fixture and the reviewed combined0058 migration.
"""

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

from app.domain import native_protective_hold_schema_v58 as schema
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _upgrade(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("path_separator", "os")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0058"]
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0058", heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == ("0058",)


async def test_actual0058_original_oco_predicate_compiles_and_missing_targets_never_resolve():
    url, tls = _isolated_tls_database_config()
    database = "ctcc_hold58_" + uuid4().hex[:12]
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner = None
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql('CREATE DATABASE "' + database + '"')
            created = True
        owner = create_async_engine(
            url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(_upgrade)
        async with owner.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_ssl WHERE pid=pg_catalog.pg_backend_pid() AND ssl)"
                    )
                )
                is True
            )
            observed = (
                await connection.execute(
                    text(
                        "SELECT pg_catalog.oidvectortypes(proargtypes),pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(prosrc,'UTF8')),'hex'),prosecdef FROM pg_catalog.pg_proc WHERE oid='public.qualification_native_protective_flat_data_v58(text,timestamptz)'::regprocedure"
                    )
                )
            ).one()
            assert tuple(observed) == (
                schema.FUNCTION_IDENTITY,
                schema.PREDICATE_BODY_SHA256,
                False,
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT public.qualification_native_protective_flat_data_v58(:rid,clock_timestamp())"
                    ),
                    {"rid": "0" * 64},
                )
                is False
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT public.qualification_native_protective_flat_data_v58(NULL,NULL)"
                    )
                )
                is False
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM public.qualification_submission_outcomes"
                    )
                )
                == 0
            )
            indexes = (
                await connection.execute(
                    text(
                        "SELECT i.relname,x.indisunique FROM pg_catalog.pg_index x JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid WHERE i.relname IN ('qualification_native_protected_hold_once_v58','qualification_native_protective_flat_once_v58') ORDER BY i.relname"
                    )
                )
            ).all()
            assert indexes == [
                ("qualification_native_protected_hold_once_v58", True),
                ("qualification_native_protective_flat_once_v58", True),
            ]
    finally:
        if owner is not None:
            await owner.dispose()
        if created:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.exec_driver_sql(
                    'DROP DATABASE "' + database + '" WITH (FORCE)'
                )
        await admin.dispose()
