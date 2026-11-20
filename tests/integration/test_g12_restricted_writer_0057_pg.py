"""Actual isolated TLS PostgreSQL successor tests; synthetic custody only.

Requires the explicit existing disposable TLS lab. No venue API is involved.
No running production database or installed credential is selected here.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain import g12_custody_catalog_0057_v1 as catalog
from app.trade_qualification.g12_invocation_custody import (
    G12InvocationCustodyRepository,
)
from scripts import current0057_native_g12_acl_v1 as profile
from tests.integration.test_g12_invocation_custody_repository import (
    _claim,
    _publication,
)
from tests.integration.test_gate3_fitted_candidate_custody_pg import (
    _isolated_tls_database_config,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _migrate(connection, target):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0057"]
    current = MigrationContext.configure(connection).get_current_heads()
    downgrade = current == ("0057",) and target == "0056"
    migration = MigrationContext.configure(
        connection,
        opts={
            "fn": lambda heads, _: (
                script._downgrade_revs(target, heads)
                if downgrade
                else script._upgrade_revs(target, heads)
            )
        },
    )
    with Operations.context(migration), migration.begin_transaction():
        migration.run_migrations()
    assert MigrationContext.configure(connection).get_current_heads() == (target,)


@pytest.fixture
async def _isolated_0057_g12_lab():
    url, tls = _isolated_tls_database_config()
    nonce = uuid4().hex[:12]
    database = "ctcc_g12_57_" + nonce
    names = tuple("ctcc_g12_" + label + "_" + nonce for label in ("w", "c", "f"))
    admin = create_async_engine(
        url, poolclass=NullPool, connect_args={"ssl": tls}, hide_parameters=True
    )
    owner = None
    writers = []
    created_roles = []
    created_db = False
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
            created_db = True
        owner = create_async_engine(
            url.set(database=database),
            poolclass=NullPool,
            connect_args={"ssl": tls},
            hide_parameters=True,
        )
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0057"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0057"))
        async with owner.begin() as connection:
            for name in names:
                await connection.exec_driver_sql(
                    f'CREATE ROLE "{name}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
                )
                created_roles.append(name)
                await connection.exec_driver_sql(
                    f'GRANT CONNECT ON DATABASE "{database}" TO "{name}"'
                )
            previous, delta = profile._plans(*names)
            for grant in previous + delta:
                await connection.exec_driver_sql(grant.sql())
        for name in names:
            writers.append(
                create_async_engine(
                    url.set(database=database, username=name),
                    poolclass=NullPool,
                    connect_args={"ssl": tls},
                    hide_parameters=True,
                )
            )
        yield owner, writers, names
    finally:
        for writer in writers:
            await writer.dispose()
        if owner is not None:
            await owner.dispose()
        try:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                if created_db:
                    await connection.exec_driver_sql(
                        f'DROP DATABASE "{database}" WITH (FORCE)'
                    )
                for name in created_roles:
                    await connection.exec_driver_sql(f'DROP ROLE "{name}"')
        finally:
            await admin.dispose()


async def _observe(engine, names, *, read_only=True):
    async with engine.connect() as connection, connection.begin():
        for sql in (
            "SET TRANSACTION READ ONLY" if read_only else "SET TRANSACTION READ WRITE",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL synchronous_commit='on'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await connection.exec_driver_sql(sql)
        oid = await connection.scalar(
            text(
                "SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user"
            )
        )
        result = await profile.observe_current0057_g12_writer_profile_v1(
            connection,
            writer=names[0],
            custody=names[1],
            frame_reader=names[2],
            expected_writer_oid=oid,
            read_only=read_only,
        )
        xid = await connection.scalar(
            text("SELECT pg_catalog.pg_current_xact_id()::text")
        )
        return result, xid


async def _counts(owner):
    async with owner.connect() as connection:
        return tuple(
            (
                await connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM public.g12_invocation_claims),(SELECT count(*) FROM public.g12_invocation_publications)"
                    )
                )
            ).one()
        )


async def test_actual0057_restricted_g12_commits_without_update_and_reopens(
    _isolated_0057_g12_lab,
):
    owner, engines, names = _isolated_0057_g12_lab
    writer = engines[0]
    first, xid1 = await _observe(writer, names)
    assert (await _observe(writer, names, read_only=False))[0] == first
    measured = []

    class MeasuredSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            sql = str(statement)
            if "public.g12_invocation_" in sql:
                xid = (
                    await super().execute(
                        text("SELECT pg_catalog.pg_current_xact_id()::text")
                    )
                ).scalar_one()
                measured.append((self, xid))
            return result

    factory = async_sessionmaker(writer, class_=MeasuredSession, expire_on_commit=False)
    repo = G12InvocationCustodyRepository(factory)
    claim = await repo.claim(_claim())
    publication = await repo.publication(claim, _publication(claim))
    # Exact original six claim/write/read stages used separate real sessions
    # and committed transaction IDs. These synthetic rows issue no authority.
    assert len(measured) == 6
    assert (
        len({id(item[0]) for item in measured})
        == len({item[1] for item in measured})
        == 6
    )
    reopened = G12InvocationCustodyRepository(factory)
    assert await reopened._read_claim(claim) == claim
    assert (
        await reopened.read_publication(claim, publication.publication_sha256)
        == publication
    )
    second, xid2 = await _observe(writer, names)
    assert xid1 != xid2 and second == first
    assert second["database_head"] == "0057" and second["grant_statements"] == 44
    assert second["execution_authority"] is False and second["admission"] == "DENY"
    assert await _counts(owner) == (1, 1)
    for table in catalog.TABLES:
        for operation in (
            f"UPDATE public.{table} SET invocation_id=invocation_id",
            f"DELETE FROM public.{table}",
            f"TRUNCATE public.{table}",
        ):
            with pytest.raises(DBAPIError):
                async with writer.begin() as connection:
                    await connection.exec_driver_sql(operation)
            assert await _counts(owner) == (1, 1)
        for engine in engines[1:]:
            with pytest.raises(DBAPIError):
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(f"SELECT * FROM public.{table}")
    with pytest.raises(DBAPIError):
        async with owner.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0056"))
    assert await _counts(owner) == (1, 1)
    final, xid3 = await _observe(writer, names)
    assert final == second and xid3 not in {xid1, xid2}


async def test_actual0057_profile_denies_each_revocation_extra_grant_and_disabled_guard(
    _isolated_0057_g12_lab,
):
    owner, engines, names = _isolated_0057_g12_lab
    writer = engines[0]
    accepted, _ = await _observe(writer, names)
    assert await _counts(owner) == (0, 0)
    for table in catalog.TABLES:
        for right in ("SELECT", "INSERT"):
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    f'REVOKE {right} ON public.{table} FROM "{names[0]}"'
                )
            with pytest.raises(profile.Current0057G12ProfileError):
                await _observe(writer, names)
            async with owner.begin() as connection:
                await connection.exec_driver_sql(
                    f'GRANT {right} ON public.{table} TO "{names[0]}"'
                )
            assert (await _observe(writer, names))[0] == accepted
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f'GRANT UPDATE ON public.{table} TO "{names[0]}"'
            )
        with pytest.raises(profile.Current0057G12ProfileError):
            await _observe(writer, names)
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f'REVOKE UPDATE ON public.{table} FROM "{names[0]}"'
            )
        assert (await _observe(writer, names))[0] == accepted
        trigger = table + "_immutable"
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"ALTER TABLE public.{table} DISABLE TRIGGER {trigger}"
            )
        with pytest.raises(profile.Current0057G12ProfileError):
            await _observe(writer, names)
        async with owner.begin() as connection:
            await connection.exec_driver_sql(
                f"ALTER TABLE public.{table} ENABLE TRIGGER {trigger}"
            )
        assert (await _observe(writer, names))[0] == accepted
    assert await _counts(owner) == (0, 0)
