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
from contextlib import asynccontextmanager
from functools import wraps
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

from app.database.repositories import public_runtime_bundle_custody as custody
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


async def test_history_source_envelope_bytes_use_existing_custody_without_source_kind_gate(
    runtime_custody_database,
):
    """DB0046 byte retention only, not native historical-source acceptance."""
    from app.trade_qualification.account_history_source_bundle_v1 import (
        POLICY_SHA256,
        SCHEMA,
    )

    repository, _, _ = runtime_custody_database
    await repository.verify_deployment()
    # Deliberately not a valid source bundle: SQL custody is generic bytes and
    # must not be misreported as a semantic/native source authentication test.
    raw = json.dumps(
        {
            "schema_version": SCHEMA,
            "policy_sha256": POLICY_SHA256,
            "fixture_id": uuid4().hex,
            "fixture_scope": "opaque_byte_retention_only",
            "execution_authority": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    data = {
        "bundle_raw": raw,
        "expected_sha256": hashlib.sha256(raw).hexdigest(),
        "proof_envelope_sha256": hashlib.sha256(b"synthetic-history-proof").hexdigest(),
    }
    receipt = await repository.seal_bundle(**data)
    retained = consume(receipt, data)
    assert retained.bundle_raw == raw
    assert retained.append_transaction_id != retained.readback_transaction_id
    assert retained.recorded_at <= retained.database_readback_at
    replay = await repository.read_retained_bundle(
        bundle_id=retained.bundle_id,
        expected_sha256=data["expected_sha256"],
        proof_envelope_sha256=data["proof_envelope_sha256"],
    )
    assert replay.bundle_raw == raw
    assert replay.execution_authority is False
    with pytest.raises(RuntimeBundleCustodyError):
        consume(receipt, data)


def consume(receipt, data):
    return _consume_runtime_bundle_custody_receipt(
        receipt,
        expected_bundle_sha256=data["expected_sha256"],
        expected_proof_envelope_sha256=data["proof_envelope_sha256"],
    )


def _safe_custody_diagnostic(case):
    """Preserve a fixed category in tb=no JUnit without native error payloads."""

    @wraps(case)
    async def run(*args, **kwargs):
        try:
            return await case(*args, **kwargs)
        except RuntimeBundleCustodyError as error:
            category = error.diagnostic_category
            conditions = error.diagnostic_conditions
        # Outside the handler, pytest's JUnit text cannot inherit the original
        # exception chain. Keep the exported category a single fixed line.
        checks = ";checks=" + ",".join(conditions) if conditions else ""
        pytest.fail(f"runtime_custody_diagnostic:{category}{checks}", pytrace=False)

    return run


async def _assert_native_catalog_text_projection(engine):
    """Compare real native internal-char decoding with the nine text projections.

    This runs only on the disposable fixture's actual restricted connection.
    Failures emit a fixed code, never an assertion representation of any row.
    """
    async with engine.connect() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        raw = (
            await connection.execute(
                text(
                    "SELECT c.relkind AS raw_relkind,c.relpersistence AS raw_relpersistence,"
                    "c.relkind::text AS text_relkind,c.relpersistence::text AS text_relpersistence "
                    "FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass("
                    "'public.public_runtime_raw_bundles_v1')"
                )
            )
        ).one()
        raw_types_verified = (
            type(raw.raw_relkind) is bytes
            and type(raw.raw_relpersistence) is bytes
            and type(raw.text_relkind) is str
            and type(raw.text_relpersistence) is str
            and raw.raw_relkind == raw.text_relkind.encode("utf-8")
            and raw.raw_relpersistence == raw.text_relpersistence.encode("utf-8")
        )
        if not raw_types_verified:
            pytest.fail("native_catalog_internal_char_type_contract", pytrace=False)
        for statement, fields, expected_rows in (
            (custody._ROLE_SQL, ("relkind", "relpersistence"), 1),
            (custody._FUNCTION_SQL, ("provolatile", "proparallel", "prokind"), 4),
            (custody._TRIGGER_SQL, ("tgenabled",), 3),
            (custody._COLUMN_SQL, ("attidentity", "attgenerated"), 6),
            (custody._CONSTRAINT_SQL, ("contype",), 4),
        ):
            rows = (await connection.execute(statement)).all()
            canonical_types_verified = len(rows) == expected_rows and all(
                type(getattr(row, field)) is str for row in rows for field in fields
            )
            if not canonical_types_verified:
                pytest.fail("native_catalog_text_projection_contract", pytrace=False)


@_safe_custody_diagnostic
async def test_migrated_restricted_role_commit_then_new_transaction_exact_readback(
    runtime_custody_database,
):
    repository, engine, _admin = runtime_custody_database
    await _assert_native_catalog_text_projection(engine)
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


@asynccontextmanager
async def _shadow_current_database_connection(engine, admin):
    """Owned hermetic database only; a real caller-path falsification fixture."""
    role, database = engine.url.username, engine.url.database
    assert re.fullmatch(r"ctcc_rt_[a-f0-9]{18}", role or "")
    assert re.fullmatch(r"ctcc_rt_[a-f0-9]{18}", database or "")
    schema = f"ctcc_shadow_{uuid4().hex[:18]}"
    async with admin.begin() as connection:
        assert not (
            await connection.execute(
                text(
                    "SELECT pg_catalog.has_database_privilege(:role,'template1','CREATE')"
                ),
                {"role": role},
            )
        ).scalar_one()
        await connection.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {role}"))
        await connection.execute(
            text(
                f"CREATE FUNCTION {schema}.current_database() RETURNS pg_catalog.name "
                "LANGUAGE sql STABLE AS $ctcc$ SELECT 'template1'::pg_catalog.name $ctcc$"
            )
        )
    unsafe_path = f"{schema}, pg_catalog, public"
    shadow_engine = create_async_engine(
        engine.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": unsafe_path}},
    )
    try:
        yield shadow_engine, unsafe_path, database
    finally:
        await shadow_engine.dispose()
    # The parent fixture drops the owned database, then the restricted role.
    # No shared schema, deployed role or production privileges are changed.


@_safe_custody_diagnostic
async def test_native_caller_path_is_local_and_safe_for_verify_seal_read_and_recovery(
    runtime_custody_database,
):
    _repository, engine, admin = runtime_custody_database
    async with _shadow_current_database_connection(engine, admin) as (
        shadow_engine,
        unsafe_path,
        database,
    ):
        factory = async_sessionmaker(shadow_engine, expire_on_commit=False)
        repository = RuntimeBundleCustodyRepository(factory)
        async with factory() as session, session.begin():
            await session.execute(text("SET LOCAL synchronous_commit = 'on'"))
            await session.execute(text("SET TRANSACTION READ ONLY"))
            before = (
                await session.execute(
                    text(
                        "SELECT pg_catalog.current_setting('search_path') AS path,"
                        "current_database()::pg_catalog.text AS shadow_database,"
                        "pg_catalog.current_database()::pg_catalog.text AS actual_database"
                    )
                )
            ).one()
            assert before.path == unsafe_path
            assert (
                before.shadow_database == "template1"
                and before.actual_database == database
            )
            assert len(await RuntimeBundleCustodyRepository._catalog(session)) == 64
            assert (
                await session.execute(
                    text("SELECT pg_catalog.current_setting('search_path')")
                )
            ).scalar_one() == "pg_catalog, public, pg_temp"
        # SET LOCAL ends with the controlled transaction. A new native session
        # still starts hostile, so every later transaction must earn its guard.
        async with factory() as session, session.begin():
            assert (
                await session.execute(
                    text("SELECT pg_catalog.current_setting('search_path')")
                )
            ).scalar_one() == unsafe_path
        await repository.verify_deployment()
        data = bundle()
        observed = consume(await repository.seal_bundle(**data), data)
        assert observed.bundle_raw == data["bundle_raw"]
        assert observed.append_transaction_id != observed.readback_transaction_id
        replay = await repository.read_retained_bundle(
            bundle_id=observed.bundle_id,
            expected_sha256=data["expected_sha256"],
            proof_envelope_sha256=data["proof_envelope_sha256"],
        )
        assert replay.bundle_raw == data["bundle_raw"]
        fresh = RuntimeBundleCustodyRepository(factory)
        await fresh.verify_deployment()
        recovered = await fresh.read_retained_bundle_by_pins(
            expected_sha256=data["expected_sha256"],
            proof_envelope_sha256=data["proof_envelope_sha256"],
        )
        assert recovered.bundle_raw == data["bundle_raw"]
        assert (
            not recovered.execution_authority
            and not recovered.historical_availability_verified
        )
        with pytest.raises(RuntimeBundleCustodyError, match="receipt_unavailable"):
            consume(recovered, data)
        async with admin.connect() as connection:
            settings = (
                (
                    await connection.execute(
                        text(
                            "SELECT p.proconfig FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                            "WHERE n.nspname='public' AND p.proname LIKE 'public_runtime_bundle_v1_%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(settings) == 4 and all(
            value == ["search_path=pg_catalog, public"] for value in settings
        )


@_safe_custody_diagnostic
async def test_shadow_current_database_cannot_hide_create_grant_or_privileged_login(
    runtime_custody_database,
):
    _repository, engine, admin = runtime_custody_database
    async with _shadow_current_database_connection(engine, admin) as (
        shadow_engine,
        unsafe_path,
        database,
    ):
        role = engine.url.username
        async with admin.begin() as connection:
            await connection.execute(
                text(f"GRANT CREATE ON DATABASE {database} TO {role}")
            )
        async with shadow_engine.connect() as connection:
            actual = (
                await connection.execute(
                    text(
                        "SELECT current_database()::pg_catalog.text AS shadow_database,"
                        "pg_catalog.current_database()::pg_catalog.text AS actual_database,"
                        "pg_catalog.has_database_privilege(current_user,current_database(),'CREATE') AS masked_create,"
                        "pg_catalog.has_database_privilege(current_user,pg_catalog.current_database(),'CREATE') AS actual_create"
                    )
                )
            ).one()
        assert (
            actual.shadow_database == "template1" and actual.actual_database == database
        )
        assert actual.actual_create and not actual.masked_create
        repository = RuntimeBundleCustodyRepository(
            async_sessionmaker(shadow_engine, expire_on_commit=False)
        )
        with pytest.raises(RuntimeBundleCustodyError) as refusal:
            await repository.verify_deployment()
        assert refusal.value.diagnostic_category == "role_scope"
        assert "no_create" in refusal.value.diagnostic_conditions
        assert repository._deployment is None
        with pytest.raises(RuntimeBundleCustodyError, match="custody_unavailable"):
            await repository.seal_bundle(**bundle())
        privileged_engine = create_async_engine(
            admin.url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": unsafe_path}},
        )
        try:
            privileged = RuntimeBundleCustodyRepository(
                async_sessionmaker(privileged_engine, expire_on_commit=False)
            )
            with pytest.raises(RuntimeBundleCustodyError) as refusal:
                await privileged.verify_deployment()
            assert "nonprivileged" in refusal.value.diagnostic_conditions
            assert privileged._deployment is None
        finally:
            await privileged_engine.dispose()
        async with admin.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM public.public_runtime_raw_bundles_v1")
                )
            ).scalar_one() == 0


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


@_safe_custody_diagnostic
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


@_safe_custody_diagnostic
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

    original_read = RuntimeBundleCustodyRepository._read

    async def unavailable(_self, **kwargs):
        if _self is repository:
            raise RuntimeError("private connection detail must not escape")
        return await original_read(_self, **kwargs)

    monkeypatch.setattr(RuntimeBundleCustodyRepository, "_read", unavailable)
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


@_safe_custody_diagnostic
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
