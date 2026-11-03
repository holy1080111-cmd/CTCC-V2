"""Actual54 custody metadata, retained raw bytes and drift rejection.

Stored component bytes are declared synthetic. No account/history/qualification
or real Demo/Live credit is created by these PostgreSQL tests.
"""

import hashlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories import public_runtime_bundle_custody as custody
from tests.current0054_fixtures import (
    current0054_database as current0054_database,  # noqa: PLC0414 -- explicit pytest fixture binding
)
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@asynccontextmanager
async def _repository(admin, *, metadata=True):
    async with collector(admin, grant=False) as (engine, _):
        role = engine.url.username
        try:
            async with admin.begin() as connection:
                if metadata:
                    await connection.execute(
                        text(f'GRANT SELECT ON public.alembic_version TO "{role}"')
                    )
                for signature in ("append(text,text,text,bytea)", "read(text)"):
                    await connection.execute(
                        text(
                            f'GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_{signature} TO "{role}"'
                        )
                    )
            repository = custody.RuntimeBundleCustodyRepository(
                async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
            )
            yield repository
        finally:
            async with admin.begin() as connection:
                for signature in ("append(text,text,text,bytea)", "read(text)"):
                    await connection.execute(
                        text(
                            f'REVOKE EXECUTE ON FUNCTION public.public_runtime_bundle_v1_{signature} FROM "{role}"'
                        )
                    )
                if metadata and await connection.scalar(
                    text(
                        "SELECT pg_catalog.to_regclass('public.alembic_version') IS NOT NULL"
                    )
                ):
                    await connection.execute(
                        text(f'REVOKE SELECT ON public.alembic_version FROM "{role}"')
                    )


def _declared_component_bundle():
    raw = b"declared-synthetic-current54-custody-component-only"
    return {
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "proof_envelope_sha256": hashlib.sha256(
            b"declared-synthetic-proof"
        ).hexdigest(),
        "bundle_raw": raw,
    }


async def test_actual54_custody_enumerates_both_FKs_and_retains_one_component_bundle(
    current0054_database,
):
    async with _repository(current0054_database) as repository:
        await repository.verify_deployment()
        bundle = _declared_component_bundle()
        receipt = await repository.seal_bundle(**bundle)
        readback = custody._consume_runtime_bundle_custody_receipt(
            receipt,
            expected_bundle_sha256=bundle["expected_sha256"],
            expected_proof_envelope_sha256=bundle["proof_envelope_sha256"],
        )
        assert readback.bundle_raw == bundle["bundle_raw"]
        assert readback.bundle_id == custody.runtime_bundle_id_v1(
            bundle_sha256=bundle["expected_sha256"],
            proof_envelope_sha256=bundle["proof_envelope_sha256"],
        )
        assert readback.execution_authority is False
        assert readback.historical_availability_verified is False
        assert readback.independently_protected is False


async def test_actual54_custody_missing_head_metadata_privilege_never_guesses_schema(
    current0054_database,
):
    async with _repository(current0054_database, metadata=False) as repository:
        with pytest.raises(custody.RuntimeBundleCustodyError):
            await repository.verify_deployment()


async def test_actual54_custody_wrong_actual_head_cannot_use53_profile(
    current0054_database,
):
    async with _repository(current0054_database) as repository:
        async with current0054_database.begin() as connection:
            await connection.execute(
                text("UPDATE public.alembic_version SET version_num='0053'")
            )
        with pytest.raises(custody.RuntimeBundleCustodyError):
            await repository.verify_deployment()


async def test_full_schema_without_version_table_is_not_standalone_component(
    current0054_database,
):
    async with _repository(current0054_database) as repository:
        async with current0054_database.begin() as connection:
            await connection.execute(text("DROP TABLE public.alembic_version"))
        with pytest.raises(custody.RuntimeBundleCustodyError):
            await repository.verify_deployment()


async def test_actual54_custody_extra_incoming_FK_is_never_filtered(
    current0054_database,
):
    async with _repository(current0054_database) as repository:
        async with current0054_database.begin() as connection:
            await connection.execute(
                text("""
                CREATE TABLE public.ctcc_custody_unapproved_reference(
                  id varchar(32) REFERENCES public.public_runtime_raw_bundles_v1(bundle_id)
                  ON DELETE RESTRICT ON UPDATE NO ACTION)
            """)
            )
        with pytest.raises(custody.RuntimeBundleCustodyError):
            await repository.verify_deployment()


async def test_actual54_custody_missing_one_relation_fails_closed(current0054_database):
    async with _repository(current0054_database) as repository:
        async with current0054_database.begin() as connection:
            await connection.execute(
                text(
                    "ALTER TABLE public.demo_collector_issued_account_revisions "
                    "DROP CONSTRAINT fk_collector_issued_hwm_custody"
                )
            )
        with pytest.raises(custody.RuntimeBundleCustodyError):
            await repository.verify_deployment()
