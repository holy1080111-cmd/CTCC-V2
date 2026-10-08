"""Disposable PostgreSQL 0035 durable post-read observation acceptance."""

from __future__ import annotations

import os
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_post_read_observation import (
    PublicReceiptPostReadError,
    PublicReceiptPostReadRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from tests.durable_migration_fixtures import load_migration
from tests.integration.test_public_receipt_publication_ack_repository import (
    committed_attempt,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def migrate(connection, revision: str, direction: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = load_migration(revision)
    module.op = Operations(MigrationContext.configure(connection))
    getattr(module, direction)()


@pytest.fixture
async def isolated_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    database = "ctcc_post_read_" + uuid4().hex
    witness_role = "ctcc_witness_" + uuid4().hex[:16]
    observer_role = "ctcc_observer_" + uuid4().hex[:16]
    assert re.fullmatch(r"ctcc_post_read_[a-f0-9]{32}", database)
    assert re.fullmatch(r"ctcc_(witness|observer)_[a-f0-9]{16}", witness_role)
    assert re.fullmatch(r"ctcc_(witness|observer)_[a-f0-9]{16}", observer_role)
    admin = create_async_engine(raw_url, poolclass=NullPool)
    db_engine = witness_engine = observer_engine = None
    created_db = False
    created_roles: list[str] = []
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"CREATE DATABASE {database}"))
        created_db = True
        db_url = make_url(raw_url).set(database=database)
        db_engine = create_async_engine(db_url, poolclass=NullPool)
        async with db_engine.begin() as connection:
            version_num = int(await connection.scalar(text("SHOW server_version_num")))
            assert version_num >= 170000, "DB0035 requires PostgreSQL 17 or newer"
            for revision in ("0024", "0035"):
                await connection.run_sync(migrate, revision, "upgrade")
        for role in (witness_role, observer_role):
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created_roles.append(role)
        async with db_engine.begin() as connection:
            for role in (witness_role, observer_role):
                await connection.execute(
                    text(f"GRANT USAGE ON SCHEMA public TO {role}")
                )
            for function in (
                "public_receipt_witness_append(jsonb)",
                "public_receipt_witness_read(text)",
            ):
                await connection.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION public.{function} TO {witness_role}"
                    )
                )
            for function in (
                "public_receipt_post_read_append(text,bigint,text,text,text,text,text,text,text)",
                "public_receipt_post_read_read(text,bigint)",
            ):
                await connection.execute(
                    text(
                        f"GRANT EXECUTE ON FUNCTION public.{function} TO {observer_role}"
                    )
                )
        witness_engine = create_async_engine(
            db_url.set(username=witness_role, password=None), poolclass=NullPool
        )
        observer_engine = create_async_engine(
            db_url.set(username=observer_role, password=None), poolclass=NullPool
        )
        witness_repo = PublicReceiptWitnessRepository(
            async_sessionmaker(witness_engine, expire_on_commit=False)
        )
        observation_repo = PublicReceiptPostReadRepository(
            async_sessionmaker(observer_engine, expire_on_commit=False), witness_repo
        )
        await witness_repo.verify_role()
        try:
            await observation_repo.verify_role()
        except PublicReceiptPostReadError as exc:
            if str(exc) != "post_read_role_unavailable":
                raise
            # CI has an isolated ephemeral PostgreSQL cluster. Keep the
            # production redaction unchanged while reporting only the driver
            # class and SQLSTATE needed to diagnose this fixture failure.
            try:
                async with async_sessionmaker(
                    observer_engine, expire_on_commit=False
                )() as diagnostic_session:
                    await PublicReceiptPostReadRepository._role_guard(
                        diagnostic_session
                    )
            except Exception as diagnostic:  # noqa: BLE001 - safe CI diagnostic only
                underlying = getattr(diagnostic, "orig", diagnostic)
                sqlstate = getattr(underlying, "sqlstate", None) or getattr(
                    underlying, "pgcode", None
                )
                safe_sqlstate = (
                    sqlstate
                    if isinstance(sqlstate, str)
                    and re.fullmatch(r"[0-9A-Z]{5}", sqlstate)
                    else "unavailable"
                )
                raise AssertionError(
                    "post_read_role_diagnostic:"
                    f"{type(diagnostic).__name__}:"
                    f"{type(underlying).__name__}:"
                    f"{safe_sqlstate}"
                ) from None
            raise
        yield (
            witness_repo,
            observation_repo,
            db_engine,
            witness_engine,
            observer_engine,
            observer_role,
        )
    finally:
        if observer_engine is not None:
            await observer_engine.dispose()
        if witness_engine is not None:
            await witness_engine.dispose()
        if db_engine is not None:
            await db_engine.dispose()
        if created_db:
            async with admin.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(f"DROP DATABASE {database}"))
        for role in reversed(created_roles):
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP ROLE {role}"))
        await admin.dispose()


def pins(witness, checkpoint, not_before):
    return {
        "journal_key": witness.journal_key,
        "witness_revision": witness.revision,
        "witness_record_sha256": witness.record_sha256,
        "checkpoint_sha256": witness.checkpoint_sha256,
        "capture_id": uuid4().hex,
        "plan_sha256": witness.plan_sha256,
        "receipt_sha256": uuid4().hex + uuid4().hex,
        "source_rows_sha256": uuid4().hex + uuid4().hex,
        "v2_capture_sha256": uuid4().hex + uuid4().hex,
        "not_before": not_before,
    }


async def committed_capture(witness_repo):
    attempt, checkpoint = await committed_attempt(witness_repo)
    witness = await witness_repo.append(
        journal_id=attempt.journal_id,
        checkpoint=checkpoint,
        transition="append_capture",
        expected=attempt,
        operation_id=attempt.operation_id,
        plan_sha256=attempt.plan_sha256,
        attempt_outcome="completed_collection",
    )
    return witness, checkpoint


async def test_post_read_sample_is_immutable_and_replayable(isolated_database):
    witness_repo, repo, db_engine, _, observer_engine, _ = isolated_database
    witness, checkpoint = await committed_capture(witness_repo)
    sampled = await witness_repo.observe_committed_revision(
        witness.journal_key, witness.revision
    )
    expected = pins(witness, checkpoint, sampled.observed_at)
    result = await repo.append_new(**expected)
    assert result.witness_record_sha256 == witness.record_sha256
    assert result.capture_sequence == checkpoint.sequence
    assert result.capture_head_sha256 == checkpoint.head_sha256
    assert result.observed_at >= sampled.observed_at
    assert result.database_readback_at >= result.observed_at
    assert result.persisted_observation_replayable
    assert not result.trusted_clock_verified
    assert not result.independently_protected
    assert not result.predictive_oos_eligible
    assert not result.execution_authority
    replayed = await repo.read(**expected)
    assert replayed.observed_at == result.observed_at
    with pytest.raises(PublicReceiptPostReadError, match="post_read_commit_uncertain"):
        await repo.append_new(**expected)
    with pytest.raises(PublicReceiptPostReadError, match="post_read_readback_mismatch"):
        await repo.read(**{**expected, "receipt_sha256": "f" * 64})
    with pytest.raises(DBAPIError, match="public_receipt_post_read_immutable"):
        async with db_engine.begin() as connection:
            await connection.execute(
                text("""
                  UPDATE public.public_receipt_post_read_observations
                  SET capture_sequence=2 WHERE journal_key=:key
                """),
                {"key": witness.journal_key},
            )
    with pytest.raises(DBAPIError, match="public_receipt_post_read_immutable"):
        async with db_engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE public.public_receipt_post_read_observations")
            )
    with pytest.raises(DBAPIError, match="post_read_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0035", "downgrade")
    async with observer_engine.begin() as connection:
        assert not await connection.scalar(
            text("""
              SELECT pg_catalog.has_table_privilege(
                current_user,'public.public_receipt_post_read_observations','SELECT')
            """)
        )


async def test_bad_identity_and_clock_are_denied(isolated_database):
    witness_repo, repo, _, _, _, _ = isolated_database
    witness, checkpoint = await committed_capture(witness_repo)
    sampled = await witness_repo.observe_committed_revision(
        witness.journal_key, witness.revision
    )
    expected = pins(witness, checkpoint, sampled.observed_at)
    with pytest.raises(
        PublicReceiptPostReadError, match="post_read_witness_pin_mismatch"
    ):
        await repo.append_new(**{**expected, "witness_record_sha256": "f" * 64})
    with pytest.raises(
        PublicReceiptPostReadError, match="post_read_time_input_invalid"
    ):
        await repo.append_new(
            **{**expected, "not_before": sampled.observed_at.replace(tzinfo=None)}
        )
    with pytest.raises(PublicReceiptPostReadError, match="post_read_readback_mismatch"):
        await repo.append_new(
            **{**expected, "not_before": sampled.observed_at + timedelta(days=1)}
        )


async def test_observer_with_witness_permission_is_rejected(isolated_database):
    witness_repo, repo, db_engine, _, observer_engine, observer_role = isolated_database
    witness, checkpoint = await committed_capture(witness_repo)
    sampled = await witness_repo.observe_committed_revision(
        witness.journal_key, witness.revision
    )
    expected = pins(witness, checkpoint, sampled.observed_at)
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION public.public_receipt_witness_append(jsonb) "
                f"TO {observer_role}"
            )
        )
    try:
        with pytest.raises(
            PublicReceiptPostReadError,
            match="restricted_post_read_role_required",
        ):
            await repo.verify_role()
        with pytest.raises(DBAPIError, match="public_receipt_post_read_role_denied"):
            async with observer_engine.begin() as connection:
                await connection.execute(
                    text("""
                      SELECT public.public_receipt_post_read_append(
                        :journal_key,:witness_revision,:witness_record_sha256,
                        :checkpoint_sha256,:capture_id,:plan_sha256,
                        :receipt_sha256,:source_rows_sha256,:v2_capture_sha256)
                    """),
                    {
                        key: value
                        for key, value in expected.items()
                        if key != "not_before"
                    },
                )
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                text(
                    "REVOKE EXECUTE ON FUNCTION public.public_receipt_witness_append(jsonb) "
                    f"FROM {observer_role}"
                )
            )


async def test_empty_downgrade_and_reupgrade(isolated_database):
    _, _, db_engine, _, _, _ = isolated_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0035", "downgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT to_regclass('public.public_receipt_post_read_observations')"
                )
            )
            is None
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0035", "upgrade")
