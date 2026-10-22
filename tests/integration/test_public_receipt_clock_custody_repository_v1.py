"""Actual restricted PostgreSQL catalog acceptance; no clock or trading claim."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import public_receipt_clock_custody_v1 as custody
from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from tests.durable_migration_fixtures import load_migration
from tests.integration.test_public_runtime_bundle_custody_repository import (
    _hermetic_provisioning_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _apply_revision(connection, revision):
    migration = load_migration(revision)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


@dataclass(frozen=True)
class _Database:
    witness: PublicReceiptWitnessRepository
    observation: PublicReceiptPostReadRepository
    admin: object
    witness_role: str
    observer_role: str

    async def acquire(self):
        return await custody.acquire_clock_custody_deployment_v1(
            witness_repository=self.witness,
            observation_repository=self.observation,
        )

    async def recheck(self, owner):
        return await custody.recheck_clock_custody_deployment_v1(
            owner,
            witness_repository=self.witness,
            observation_repository=self.observation,
        )

    async def mutate(self, statement):
        async with self.admin.begin() as connection:
            await connection.execute(text(statement))


@pytest.fixture
async def clock_custody_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit hermetic migrated PostgreSQL DATABASE_URL required")
    # Reuse the existing exact disposable Docker-host/profile admission before
    # any engine, connection or DDL. This fixture cannot target the user's DB.
    provisioning_url = _hermetic_provisioning_url(raw_url)
    database = "ctcc_clock_" + uuid4().hex[:18]
    witness_role = "ctcc_wc_" + uuid4().hex[:18]
    observer_role = "ctcc_oc_" + uuid4().hex[:18]
    assert all(
        re.fullmatch(pattern, value)
        for pattern, value in (
            (r"ctcc_clock_[a-f0-9]{18}", database),
            (r"ctcc_wc_[a-f0-9]{18}", witness_role),
            (r"ctcc_oc_[a-f0-9]{18}", observer_role),
        )
    )
    provisioning = create_async_engine(provisioning_url, poolclass=NullPool)
    admin = witness_engine = observation_engine = None
    database_created = False
    created_roles = []
    try:
        async with provisioning.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            await connection.execute(text(f"CREATE DATABASE {database}"))
            database_created = True
        target_url = provisioning_url.set(database=database)
        admin = create_async_engine(target_url, poolclass=NullPool)
        async with admin.begin() as connection:
            assert await connection.scalar(text("SHOW server_version_num")) == "170011"
            for revision in ("0024", "0035", "0047"):
                await connection.run_sync(_apply_revision, revision)
        for role in (witness_role, observer_role):
            async with provisioning.begin() as connection:
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created_roles.append(role)
        async with admin.begin() as connection:
            for role in created_roles:
                await connection.execute(
                    text(f"GRANT USAGE ON SCHEMA public TO {role}")
                )
            for role, signatures in (
                (
                    witness_role,
                    (
                        "public_receipt_witness_append(jsonb)",
                        "public_receipt_witness_read(text)",
                    ),
                ),
                (
                    observer_role,
                    (
                        "public_receipt_post_read_append(text,bigint,text,text,text,text,text,text,text)",
                        "public_receipt_post_read_read(text,bigint)",
                    ),
                ),
            ):
                for signature in signatures:
                    await connection.execute(
                        text(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {role}")
                    )
        witness_engine = create_async_engine(
            target_url.set(username=witness_role, password=None), poolclass=NullPool
        )
        observation_engine = create_async_engine(
            target_url.set(username=observer_role, password=None), poolclass=NullPool
        )
        witness = PublicReceiptWitnessRepository(
            async_sessionmaker(witness_engine, expire_on_commit=False)
        )
        observation = PublicReceiptPostReadRepository(
            async_sessionmaker(observation_engine, expire_on_commit=False), witness
        )
        yield _Database(witness, observation, admin, witness_role, observer_role)
    finally:
        for engine in (observation_engine, witness_engine, admin):
            if engine is not None:
                await engine.dispose()
        if database_created:
            async with provisioning.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                await connection.execute(text(f"DROP DATABASE {database} WITH (FORCE)"))
        for role in reversed(created_roles):
            async with provisioning.begin() as connection:
                await connection.execute(text(f"DROP ROLE {role}"))
        await provisioning.dispose()


async def test_actual_catalog_acquire_and_independent_recheck(clock_custody_database):
    database = clock_custody_database
    owner = await database.acquire()
    first = custody.readback_clock_custody_deployment_v1(owner)
    second = await database.recheck(owner)
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert first.scopes[0].database_oid == first.scopes[1].database_oid
    assert first.scopes[0].server_started == first.scopes[1].server_started
    assert first.scopes[0].login_oid != first.scopes[1].login_oid
    assert first.absolute_utc_verified is False
    assert first.host_binary_attested is False
    assert first.independently_protected is False
    assert first.execution_authority is False


@pytest.mark.parametrize(
    "drift",
    (
        "trigger_disabled",
        "bounds_weakened",
        "public_function_execute",
        "direct_table_read",
        "schema_create",
        "wrong_role_execute",
        "search_path_changed",
        "function_body_changed",
        "unique_index_missing",
    ),
)
async def test_actual_catalog_drift_denies(clock_custody_database, drift):
    database = clock_custody_database
    # Establish a genuine positive baseline before introducing each independent
    # fault; a globally broken implementation cannot satisfy this negative test.
    owner = await database.acquire()
    assert await database.recheck(owner)
    mutations = {
        "trigger_disabled": "ALTER TABLE public.public_receipt_witness_revisions DISABLE TRIGGER USER",
        "bounds_weakened": "ALTER TABLE public.public_receipt_witness_revisions DROP CONSTRAINT ck_public_receipt_witness_revisions_native_identity_bounds",
        "public_function_execute": "GRANT EXECUTE ON FUNCTION public.public_receipt_witness_insert_guard() TO PUBLIC",
        "direct_table_read": f"GRANT SELECT ON public.public_receipt_post_read_observations TO {database.observer_role}",
        "schema_create": f"GRANT CREATE ON SCHEMA public TO {database.witness_role}",
        "wrong_role_execute": f"GRANT EXECUTE ON FUNCTION public.public_receipt_post_read_read(text,bigint) TO {database.witness_role}",
        "search_path_changed": "ALTER FUNCTION public.public_receipt_post_read_read(text,bigint) SET search_path TO pg_catalog",
        "function_body_changed": "CREATE OR REPLACE FUNCTION public.public_receipt_witness_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'synthetic_changed_guard'; END; $$ LANGUAGE plpgsql",
        "unique_index_missing": "DROP INDEX public.uq_public_receipt_witness_operation_start",
    }
    await database.mutate(mutations[drift])
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        await database.recheck(owner)
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody.readback_clock_custody_deployment_v1(owner)
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        await database.acquire()


async def test_actual_recheck_failure_remains_poisoned_after_repair(
    clock_custody_database,
):
    database = clock_custody_database
    owner = await database.acquire()
    await database.mutate(
        f"GRANT SELECT ON public.public_receipt_post_read_observations TO {database.observer_role}"
    )
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        await database.recheck(owner)
    await database.mutate(
        f"REVOKE SELECT ON public.public_receipt_post_read_observations FROM {database.observer_role}"
    )
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        await database.recheck(owner)


async def test_actual_owner_cannot_cross_async_task(clock_custody_database):
    database = clock_custody_database
    owner = await database.acquire()
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        await asyncio.create_task(database.recheck(owner))
    with pytest.raises(custody.PublicReceiptClockCustodyError):
        custody.readback_clock_custody_deployment_v1(owner)
