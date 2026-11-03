"""Actual separate0053 app/raw-role catalog checks; component evidence only."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import (
    account_history_original_native_read_current0053_v1 as native,
)
from app.database.repositories import (
    collector_current_0053_multirole_catalog_v1 as profile,
)
from app.domain import account_revision_collector_lineage_schema_v1 as frozen
from app.domain import collector_current_0053_owner_installation_v1 as baseline
from app.domain.account_revision_collector_lineage_v1 import (
    CollectorRevisionLineageError,
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
)
from tests.original_frame_current_0053_fixtures import (
    original_frame_database_0053 as original_frame_database_0053,  # noqa: PLC0414 -- explicit pytest fixture binding
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _settings(session, *, readonly=False):
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        *(("SET TRANSACTION READ ONLY",) if readonly else ()),
        "SET LOCAL search_path TO pg_catalog, public, pg_temp",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL lock_timeout='1000ms'",
        "SET LOCAL statement_timeout='10000ms'",
    ):
        await session.execute(text(statement))


@asynccontextmanager
async def _roles(admin, *, metadata=True, lineage_grant=True):
    # These are newly created actual restricted native logins. The runtime
    # adapter derives their names from checked native creators, never this DTO.
    async with (
        collector(admin, grant=True) as (raw, raw_oid),
        collector(admin, grant=False) as (writer, writer_oid),
    ):
        raw_name, writer_name = raw.url.username, writer.url.username
        try:
            async with admin.begin() as connection:
                if metadata:
                    for role in (raw_name, writer_name):
                        await connection.execute(
                            text(f'GRANT SELECT ON public.alembic_version TO "{role}"')
                        )
                if lineage_grant:
                    await connection.execute(
                        text(
                            f'GRANT SELECT,INSERT ON public.demo_collector_current_revision_lineages TO "{writer_name}"'
                        )
                    )
            yield SimpleNamespace(
                admin=admin,
                raw=raw,
                writer=writer,
                raw_oid=raw_oid,
                writer_oid=writer_oid,
                raw_name=raw_name,
                writer_name=writer_name,
                raw_factory=async_sessionmaker(
                    raw, autoflush=False, expire_on_commit=False
                ),
                writer_factory=async_sessionmaker(
                    writer, autoflush=False, expire_on_commit=False
                ),
            )
        finally:
            async with admin.begin() as connection:
                if lineage_grant:
                    await connection.execute(
                        text(
                            f'REVOKE SELECT,INSERT ON public.demo_collector_current_revision_lineages FROM "{writer_name}"'
                        )
                    )
                if metadata and await connection.scalar(
                    text(
                        "SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL"
                    )
                ):
                    for role in (raw_name, writer_name):
                        await connection.execute(
                            text(
                                f'REVOKE SELECT ON public.alembic_version FROM "{role}"'
                            )
                        )


async def _actual_rows(roles, *, set_writer_role=False):
    async with roles.raw_factory() as raw, roles.writer_factory() as writer:
        await raw.begin()
        await writer.begin()
        await _settings(raw)
        await _settings(writer, readonly=True)
        if set_writer_role:
            await writer.execute(text(f'SET LOCAL ROLE "{roles.writer_name}"'))
        raw_row = (await raw.execute(text(profile.CATALOG_SQL))).scalar_one()
        writer_row = (await writer.execute(text(profile.CATALOG_SQL))).scalar_one()
    return raw_row, writer_row


async def _read(roles, *, writer_factory=None):
    session = roles.raw_factory()
    try:
        await session.begin()
        await _settings(session)
        return await native._catalog(
            session,
            native._Fence(datetime.now(UTC) + timedelta(seconds=60)),
            roles.raw_oid,
            writer_factory or roles.writer_factory,
            roles.raw_factory,
        )
    finally:
        await profile._close_owned(session)


async def test_actual0053_writer_catalog_passes_frozen_gate_while_raw_actor_remains_distinct(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        raw, writer = await _actual_rows(roles)
        assert raw["catalog"]["actor"] == roles.raw_oid
        assert writer["catalog"]["actor"] == roles.writer_oid
        assert raw["catalog"]["owner"] != roles.raw_oid != roles.writer_oid
        assert raw["catalog"]["bad_table_acl"] is True
        assert writer["catalog"]["bad_table_acl"] is False
        frozen.verify_catalog(writer["catalog"])
        with pytest.raises(CollectorRevisionLineageError):
            frozen.verify_catalog(raw["catalog"])
        assert (
            raw["native"]["database_head"]
            == writer["native"]["database_head"]
            == "0053"
        )
        assert (
            len(raw["native"]["ri_triggers"])
            == len(writer["native"]["ri_triggers"])
            == 16
        )
        owner_oid = raw["catalog"]["owner"]
        assert profile._native_context(
            raw["native"], role=raw["role"], read_only="off", owner=owner_oid
        ) == profile._native_context(
            writer["native"], role=writer["role"], read_only="on", owner=owner_oid
        )


async def test_real_current0053_multirole_reader_requires_new_seal_and_preserves_all_source_gates(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        if (
            baseline.PG17_NATIVE_BASELINE_STATUS
            == "PENDING_GENUINE_NATIVE_PG17_CAPTURE"
        ):
            with pytest.raises(profile.Current0053MultiRoleError):
                await _read(roles)
        else:
            raw = await _read(roles)
            observed = json.loads(raw)["current0053"]["profile"]
            assert observed["database_head"] == "0053"
            assert observed["actual_separate_roles_verified"] is True
            assert (
                observed["account_complete"] is observed["execution_authority"] is False
            )


async def test_current0053_raw_factory_cannot_be_used_as_lineage_writer_factory(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles, writer_factory=roles.raw_factory)


async def test_current0053_missing_actual_writer_lineage_grants_are_not_empty_account_evidence(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053, lineage_grant=False) as roles:
        raw, writer = await _actual_rows(roles)
        assert not any(grant["grantee"] == roles.writer_oid for grant in raw["acl"])
        assert (
            raw["catalog"]["bad_table_acl"]
            is writer["catalog"]["bad_table_acl"]
            is False
        )
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles)


async def test_current0053_missing_head_metadata_grant_fails_closed(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053, metadata=False) as roles:
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles)


async def test_current0053_extra_actual_lineage_grantee_is_rejected(
    original_frame_database_0053,
):
    async with (
        _roles(original_frame_database_0053) as roles,
        collector(roles.admin, grant=False) as (extra, _),
    ):
        try:
            async with roles.admin.begin() as connection:
                await connection.execute(
                    text(
                        f'GRANT SELECT ON public.demo_collector_current_revision_lineages TO "{extra.url.username}"'
                    )
                )
            _, writer = await _actual_rows(roles)
            assert writer["catalog"]["bad_table_acl"] is True
            with pytest.raises(CollectorRevisionLineageError):
                frozen.verify_catalog(writer["catalog"])
            with pytest.raises(profile.Current0053MultiRoleError):
                await _read(roles)
        finally:
            async with roles.admin.begin() as connection:
                await connection.execute(
                    text(
                        f'REVOKE SELECT ON public.demo_collector_current_revision_lineages FROM "{extra.url.username}"'
                    )
                )


async def test_current0053_actual_writer_set_role_cannot_be_hidden_by_same_role_name(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        raw, writer = await _actual_rows(roles, set_writer_role=True)
        assert writer["role"]["setting"] == roles.writer_name
        with pytest.raises(profile.Current0053MultiRoleError):
            profile.verify_actual_multirole_catalog(
                raw,
                writer,
                raw_name=roles.raw_name,
                writer_name=roles.writer_name,
                approved_raw_role_oid=roles.raw_oid,
                parent_owner_oid=raw["catalog"]["owner"],
            )


async def test_current0053_actual_migration_owner_cannot_be_lineage_app_writer(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        owner_factory = async_sessionmaker(
            roles.admin, autoflush=False, expire_on_commit=False
        )
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles, writer_factory=owner_factory)


async def test_current0053_actual_wrong_database_head_is_rejected_without_relabelling(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        async with roles.admin.begin() as connection:
            await connection.execute(
                text("UPDATE public.alembic_version SET version_num='0052'")
            )
        raw, writer = await _actual_rows(roles)
        assert (
            raw["native"]["database_head"]
            == writer["native"]["database_head"]
            == "0052"
        )
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles)


async def test_current0053_actual_missing_lineage_FK_cannot_pass_multirole_profile(
    original_frame_database_0053,
):
    async with _roles(original_frame_database_0053) as roles:
        async with roles.admin.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE public.demo_collector_current_revision_lineages "
                    "DROP CONSTRAINT fk_collector_current_custody"
                )
            )
        raw, writer = await _actual_rows(roles)
        assert (
            len(raw["native"]["ri_triggers"])
            == len(writer["native"]["ri_triggers"])
            == 12
        )
        assert (
            raw["native"]["custody_incoming"]
            is writer["native"]["custody_incoming"]
            is None
        )
        with pytest.raises(profile.Current0053MultiRoleError):
            await _read(roles)
