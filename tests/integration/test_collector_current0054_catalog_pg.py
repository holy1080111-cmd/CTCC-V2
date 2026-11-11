"""Real strict54 catalog/RI/role faults, not account or trading acceptance."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import collector_current_0054_catalog as writer_api
from app.domain import account_revision_collector_lineage_schema_v1 as frozen53
from app.domain import collector_current_0053_owner_installation_v1 as old53
from app.domain import collector_current_0054_catalog_v1 as policy
from scripts import current_contract_source_scopes_v1 as scopes
from tests.current0054_fixtures import (
    ROOT,
)
from tests.current0054_fixtures import (
    current0054_database as current0054_database,  # noqa: PLC0414 -- explicit pytest fixture binding
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
    settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _owner(admin):
    async with admin.connect() as connection:
        await settings(connection)
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        return await connection.scalar(text(policy.OWNER_CATALOG_SQL))


async def test_actual54_owner_has_exact16_18_and_custody4_native_pairs(
    current0054_database,
):
    scope = scopes.database0054_source_scope(ROOT)
    assert scope.actual_source_head in {"0054", "0055", "0056"}
    assert scope.database_target == "0054"
    value = await _owner(current0054_database)
    assert (
        value["issued"]["database_head"] == value["lineage"]["database_head"] == "0054"
    )
    assert len(value["issued"]["ri_triggers"]) == 16
    assert len(value["lineage"]["ri_triggers"]) == 18
    assert len(value["custody_internal"]) == 4
    observed = policy.observe_actual_owner_catalog(value)
    assert observed["database_head"] == "0054"
    assert observed["native_baseline_admission"] is False
    assert observed["account_complete"] is observed["execution_authority"] is False


async def test_actual54_cannot_requalify_strict53_owner_profile(current0054_database):
    value = await _owner(current0054_database)
    with pytest.raises(old53.Current0053OwnerError):
        old53.observe_owner_installation_catalog(value["lineage"])


async def test_formal54_requires_its_own_genuine_native_seal(current0054_database):
    value = await _owner(current0054_database)
    if policy.PG17_NATIVE_BASELINE_STATUS == "PENDING_GENUINE_NATIVE_PG17_CAPTURE":
        with pytest.raises(
            policy.Current0054CatalogError, match="genuine_native_baseline"
        ):
            policy.verify_actual_owner_catalog(value)
    else:
        admitted = policy.verify_actual_owner_catalog(value)
        assert admitted["native_baseline_admission"] is True
        assert admitted["account_complete"] is admitted["execution_authority"] is False


async def test_actual54_extra_custody_FK_is_not_filtered_out(current0054_database):
    async with current0054_database.begin() as connection:
        await connection.execute(
            text("""
            CREATE TABLE public.ctcc_extra_custody_reference(
              id varchar(32) REFERENCES public.public_runtime_raw_bundles_v1(bundle_id)
              ON DELETE RESTRICT ON UPDATE NO ACTION)
        """)
        )
    value = await _owner(current0054_database)
    assert len(value["custody_internal"]) == 6
    assert len(value["issued"]["custody_incoming"]) == 3
    with pytest.raises(policy.Current0054CatalogError):
        policy.observe_actual_owner_catalog(value)


async def test_actual54_missing_issuer_lineage_FK_rejects_before_native_gate(
    current0054_database,
):
    async with current0054_database.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE public.demo_collector_issued_account_revisions "
                "DROP CONSTRAINT fk_collector_issued_source_lineage"
            )
        )
    value = await _owner(current0054_database)
    assert len(value["issued"]["ri_triggers"]) == 12
    assert len(value["lineage"]["ri_triggers"]) == 16
    with pytest.raises(policy.Current0054CatalogError):
        policy.observe_actual_owner_catalog(value)


async def test_actual54_guard_body_replacement_is_denied(current0054_database):
    async with current0054_database.begin() as connection:
        await connection.execute(
            text("""
            CREATE OR REPLACE FUNCTION public.collector_issued_account_revision_guard_v1()
            RETURNS trigger AS $ctcc$ BEGIN RETURN NEW; END; $ctcc$
            LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp
        """)
        )
    with pytest.raises(policy.Current0054CatalogError):
        policy.observe_actual_owner_catalog(await _owner(current0054_database))


async def test_actual54_writer_has_real_actor_and_separate_native_gate(
    current0054_database,
):
    async with collector(current0054_database, grant=False) as (engine, oid):
        role = engine.url.username
        try:
            async with current0054_database.begin() as connection:
                for table in (
                    "demo_collector_current_revision_lineages",
                    "demo_collector_issued_account_revisions",
                ):
                    await connection.execute(
                        text(f'GRANT SELECT,INSERT ON public.{table} TO "{role}"')
                    )
                await connection.execute(
                    text(f'GRANT SELECT ON public.alembic_version TO "{role}"')
                )
            factory = async_sessionmaker(
                engine, autoflush=False, expire_on_commit=False
            )
            async with factory() as session:
                await session.begin()
                await settings(session)
                value = (
                    await session.execute(text(policy.ACTOR_CATALOG_SQL))
                ).scalar_one()
                assert (
                    value["role"]["oid"]
                    == value["issued"]["actor"]
                    == value["lineage"]["actor"]
                    == oid
                )
                assert value["issued"]["owner"] != oid
                frozen53.verify_catalog(value["lineage_base"])
                observed = policy._observed(value, owner_mode=False, read_only="off")
                assert observed["role_oid"] == oid
                assert (
                    observed["native_baseline_admission"]
                    is observed["account_complete"]
                    is False
                )
                if (
                    policy.PG17_NATIVE_BASELINE_STATUS
                    == "PENDING_GENUINE_NATIVE_PG17_CAPTURE"
                ):
                    with pytest.raises(policy.Current0054CatalogError):
                        await writer_api.verify_actual_issuer_catalog_v1(session)
                else:
                    accepted = await writer_api.verify_actual_issuer_catalog_v1(session)
                    assert accepted["role_oid"] == oid
                    assert (
                        accepted["account_complete"]
                        is accepted["execution_authority"]
                        is False
                    )
        finally:
            async with current0054_database.begin() as connection:
                for table in (
                    "demo_collector_current_revision_lineages",
                    "demo_collector_issued_account_revisions",
                ):
                    await connection.execute(
                        text(f'REVOKE SELECT,INSERT ON public.{table} FROM "{role}"')
                    )
                if await connection.scalar(
                    text(
                        "SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL"
                    )
                ):
                    await connection.execute(
                        text(f'REVOKE SELECT ON public.alembic_version FROM "{role}"')
                    )


async def test_actual54_public_table_grant_is_rejected(current0054_database):
    async with current0054_database.begin() as connection:
        await connection.execute(
            text(
                "GRANT SELECT ON public.demo_collector_issued_account_revisions TO PUBLIC"
            )
        )
    value = await _owner(current0054_database)
    assert value["issued"]["bad_table_acl"] is True
    with pytest.raises(policy.Current0054CatalogError):
        policy.observe_actual_owner_catalog(value)


async def test_actual54_writer_missing_metadata_privilege_has_fixed_failure(
    current0054_database,
):
    async with collector(current0054_database, grant=False) as (engine, _):
        factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
        async with factory() as session:
            await session.begin()
            await settings(session)
            with pytest.raises(policy.Current0054CatalogError) as caught:
                await writer_api.verify_actual_issuer_catalog_v1(session)
            assert caught.value.args == ("current0054_actual_catalog_unavailable",)


async def test_caller_passed_flag_never_gains54_native_admission(current0054_database):
    value = await _owner(current0054_database)
    value["passed"] = True
    with pytest.raises(policy.Current0054CatalogError):
        policy.observe_actual_owner_catalog(value)
