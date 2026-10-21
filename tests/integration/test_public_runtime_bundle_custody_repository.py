"""DB0046 restricted-role acceptance on the disposable hermetic PostgreSQL DB.

No exchange/network clients, account credentials or order writes. The harness
owns this database and destroys it; this proves app-role retention, not WORM.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_runtime_bundle_custody import (
    OwnedRuntimeBundleCustodyReceipt,
    RuntimeBundleCustodyError,
    RuntimeBundleCustodyRepository,
    _consume_runtime_bundle_custody_receipt,
    runtime_bundle_id_v1,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _hermetic_provisioning_url(raw_url):
    """Reject non-hermetic clusters before creating any engine or connection."""
    try:
        url = make_url(raw_url)
        accepted = (
            url.drivername == "postgresql+asyncpg"
            and re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
            is not None
            and url.username == "ctcc"
            and url.password is None
            and url.port == 5432
            and url.database == "ctcc"
            and not url.query
        )
    except (ArgumentError, TypeError, ValueError):
        accepted = False
    if not accepted:
        raise RuntimeError("runtime_custody_hermetic_cluster_required")
    return url


@pytest.fixture
async def runtime_custody_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated migrated DATABASE_URL required")
    provisioning_url = _hermetic_provisioning_url(raw_url)
    provisioning_engine = create_async_engine(provisioning_url, poolclass=NullPool)
    database = f"ctcc_rt_{uuid4().hex[:18]}"
    admin_engine = None
    role = f"ctcc_rt_{uuid4().hex[:18]}"
    restricted_engine = None
    created = False
    database_created = False
    try:
        async with provisioning_engine.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            await connection.execute(text(f"CREATE DATABASE {database}"))
            database_created = True
        # Test only this standalone DDL in an isolated database. The full
        # supported-previous migration chain is the exact-head harness's job.
        target_url = provisioning_url.set(database=database)
        admin_engine = create_async_engine(target_url, poolclass=NullPool)
        async with admin_engine.begin() as connection:
            await connection.run_sync(_apply_migration, "upgrade")
            # Trust authentication is only the disposable CI fixture convention.
            # Production credentials and server/OS custody require deployment review.
            await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created = True
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            for signature in ("append(text,text,text,bytea)", "read(text)"):
                await connection.execute(
                    text(
                        "GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_"
                        f"{signature} TO {role}"
                    )
                )
        url = target_url.set(username=role, password=None)
        restricted_engine = create_async_engine(url, poolclass=NullPool)
        repository = RuntimeBundleCustodyRepository(
            async_sessionmaker(restricted_engine, expire_on_commit=False)
        )
        yield repository, restricted_engine, admin_engine
    finally:
        if restricted_engine is not None:
            await restricted_engine.dispose()
        if admin_engine is not None:
            await admin_engine.dispose()
        if database_created:
            async with provisioning_engine.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                await connection.execute(text(f"DROP DATABASE {database} WITH (FORCE)"))
        if created:
            async with provisioning_engine.begin() as connection:
                await connection.execute(text(f"DROP ROLE IF EXISTS {role}"))
        await provisioning_engine.dispose()


def _apply_migration(connection, direction):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0046_public_runtime_bundle_custody.py"
    )
    spec = importlib.util.spec_from_file_location(
        "runtime_custody_0046_integration", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


def bundle():
    raw = ('{"fixture_id":"' + uuid4().hex + '","private_raw_hex":"00ff"}').encode()
    return {
        "bundle_raw": raw,
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "proof_envelope_sha256": hashlib.sha256(
            b"non-authoritative-test-proof"
        ).hexdigest(),
    }


def consume(receipt, data):
    return _consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=data["expected_sha256"],
        expected_proof_envelope_sha256=data["proof_envelope_sha256"],
    )


async def test_migrated_restricted_role_commit_then_new_transaction_exact_readback(
    runtime_custody_database,
):
    repository, _engine, _admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    receipt = await repository.seal_bundle(**data)
    assert type(receipt) is OwnedRuntimeBundleCustodyReceipt
    observed = consume(receipt, data)
    assert observed.append_backend_pid != observed.readback_backend_pid
    assert observed.append_transaction_id != observed.readback_transaction_id
    assert observed.bundle_raw == data["bundle_raw"]
    assert observed.bundle_sha256 == data["expected_sha256"]
    assert observed.proof_envelope_sha256 == data["proof_envelope_sha256"]
    assert observed.recorded_at <= observed.database_readback_at
    assert not observed.independently_protected
    assert (
        not observed.historical_availability_verified
        and not observed.execution_authority
    )
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(receipt, data)
    replay = await repository.read_retained_bundle(
        bundle_id=observed.bundle_id,
        expected_sha256=data["expected_sha256"],
        proof_envelope_sha256=data["proof_envelope_sha256"],
    )
    assert replay == observed or replay.bundle_raw == observed.bundle_raw
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(replay, data)
    with pytest.raises(RuntimeBundleCustodyError, match="readback_mismatch"):
        await repository.read_retained_bundle(
            bundle_id=observed.bundle_id,
            expected_sha256=data["expected_sha256"],
            proof_envelope_sha256="f" * 64,
        )


@pytest.mark.parametrize("proof_conflict", [False, True])
async def test_duplicate_bytes_conflicting_proof_rejected_no_retry_and_one_row(
    runtime_custody_database,
    proof_conflict,
):
    repository, engine, admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    observed = consume(await repository.seal_bundle(**data), data)
    conflicting = (
        {**data, "proof_envelope_sha256": "f" * 64} if proof_conflict else dict(data)
    )
    with pytest.raises(RuntimeBundleCustodyError, match="commit_uncertain"):
        await repository.seal_bundle(**conflicting)
    with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
        await repository.seal_bundle(**bundle())
    async with admin.connect() as connection:
        count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM public.public_runtime_raw_bundles_v1 WHERE bundle_sha256=:sha"
                ),
                {"sha": data["expected_sha256"]},
            )
        ).scalar_one()
    assert count == 1
    new_owner = RuntimeBundleCustodyRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    await new_owner.verify_deployment()
    replay = await new_owner.read_retained_bundle(
        bundle_id=observed.bundle_id,
        expected_sha256=data["expected_sha256"],
        proof_envelope_sha256=data["proof_envelope_sha256"],
    )
    assert replay.bundle_raw == data["bundle_raw"]


async def test_privileged_migration_login_and_missing_preflight_rejected(
    runtime_custody_database,
):
    repository, _engine, admin = runtime_custody_database
    with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
        await repository.seal_bundle(**bundle())
    privileged = RuntimeBundleCustodyRepository(
        async_sessionmaker(admin, expire_on_commit=False)
    )
    with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
        await privileged.verify_deployment()


async def test_committed_append_with_unknown_readback_never_retries_or_issues_receipt(
    runtime_custody_database, monkeypatch, tmp_path
):
    repository, engine, admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    pins_path = tmp_path / "known-runtime-bundle-pins.json"
    pins_path.write_text(
        json.dumps(
            {key: data[key] for key in ("expected_sha256", "proof_envelope_sha256")}
        ),
        encoding="ascii",
    )

    async def unavailable(**_kwargs):
        raise RuntimeError("private connection detail must not escape")

    monkeypatch.setattr(repository, "_read", unavailable)
    with pytest.raises(
        RuntimeBundleCustodyError, match="^runtime_bundle_commit_uncertain$"
    ):
        await repository.seal_bundle(**data)
    with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
        await repository.seal_bundle(**bundle())
    async with admin.connect() as connection:
        count = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM public.public_runtime_raw_bundles_v1 WHERE bundle_sha256=:sha"
                ),
                {"sha": data["expected_sha256"]},
            )
        ).scalar_one()
    assert count == 1
    # The first owner never returned an ID. Recovery has only the two known
    # persisted digest pins and a fresh restricted-role connection owner.
    pins = json.loads(pins_path.read_text(encoding="ascii"))
    new_owner = RuntimeBundleCustodyRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    await new_owner.verify_deployment()
    recovered = await new_owner.read_retained_bundle_by_pins(**pins)
    assert recovered.bundle_id == runtime_bundle_id_v1(
        bundle_sha256=pins["expected_sha256"],
        proof_envelope_sha256=pins["proof_envelope_sha256"],
    )
    assert recovered.bundle_raw == data["bundle_raw"]
    assert not recovered.execution_authority and not recovered.independently_protected
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(recovered, data)
    with pytest.raises(RuntimeBundleCustodyError, match="readback_mismatch"):
        await new_owner.read_retained_bundle_by_pins(
            expected_sha256=pins["expected_sha256"], proof_envelope_sha256="f" * 64
        )
    # A read-only miss does not poison the fresh owner or issue any capability.
    assert (await new_owner.read_retained_bundle_by_pins(**pins)).bundle_raw == data[
        "bundle_raw"
    ]


async def test_direct_table_read_mutation_and_schema_changes_denied(
    runtime_custody_database,
):
    repository, engine, admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    consume(await repository.seal_bundle(**data), data)
    for statement in (
        "SELECT * FROM public.public_runtime_raw_bundles_v1",
        "UPDATE public.public_runtime_raw_bundles_v1 SET contract_version=1",
        "DELETE FROM public.public_runtime_raw_bundles_v1",
        "TRUNCATE public.public_runtime_raw_bundles_v1",
        "ALTER TABLE public.public_runtime_raw_bundles_v1 DISABLE TRIGGER ALL",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as connection:
                await connection.execute(text(statement))
    # The database owner also encounters append-only guards on ordinary DML.
    # A hostile owner can remove them; this test does not claim otherwise.
    for statement in (
        "UPDATE public.public_runtime_raw_bundles_v1 SET contract_version=1",
        "DELETE FROM public.public_runtime_raw_bundles_v1",
        "TRUNCATE public.public_runtime_raw_bundles_v1",
    ):
        with pytest.raises(Exception, match="runtime_bundle_immutable"):
            async with admin.begin() as connection:
                await connection.execute(text(statement))


async def test_native_receipt_cannot_cross_task_or_be_reclaimed(
    runtime_custody_database,
):
    repository, _engine, _admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    receipt = await repository.seal_bundle(**data)

    async def other_task():
        with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
            consume(receipt, data)

    await asyncio.create_task(other_task())
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(receipt, data)


async def test_receipt_proof_digest_mismatch_consumes_without_authority(
    runtime_custody_database,
):
    repository, _engine, _admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    receipt = await repository.seal_bundle(**data)
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(receipt, {**data, "proof_envelope_sha256": "a" * 64})
    with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
        consume(receipt, data)


async def test_empty_0046_ddl_downgrade_reupgrade_and_restricted_role(
    runtime_custody_database,
):
    repository, engine, admin = runtime_custody_database
    await repository.verify_deployment()
    async with admin.begin() as connection:
        await connection.run_sync(_apply_migration, "downgrade")
        assert (
            await connection.execute(
                text("SELECT to_regclass('public.public_runtime_raw_bundles_v1')")
            )
        ).scalar_one() is None
        await connection.run_sync(_apply_migration, "upgrade")
        # DROP/CREATE deliberately removes role grants; no silent re-grant.
    with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
        await repository.verify_deployment()
    async with admin.begin() as connection:
        role = engine.url.username
        # The fixture login is generated locally and contains only [a-z0-9_].
        assert role.startswith("ctcc_rt_") and role.replace("_", "").isalnum()
        for signature in ("append(text,text,text,bytea)", "read(text)"):
            await connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION public.public_runtime_bundle_v1_"
                    f"{signature} TO {role}"
                )
            )
    await repository.verify_deployment()
    data = bundle()
    assert (
        consume(await repository.seal_bundle(**data), data).bundle_raw
        == data["bundle_raw"]
    )


async def test_nonempty_migration_downgrade_refuses_without_removing_evidence(
    runtime_custody_database,
):
    repository, _engine, admin = runtime_custody_database
    await repository.verify_deployment()
    data = bundle()
    observed = consume(await repository.seal_bundle(**data), data)
    with pytest.raises(Exception, match="runtime_bundle_downgrade_requires_empty"):
        async with admin.begin() as connection:
            await connection.run_sync(_apply_migration, "downgrade")
    retained = await repository.read_retained_bundle(
        bundle_id=observed.bundle_id,
        expected_sha256=data["expected_sha256"],
        proof_envelope_sha256=data["proof_envelope_sha256"],
    )
    assert retained.bundle_raw == data["bundle_raw"]
