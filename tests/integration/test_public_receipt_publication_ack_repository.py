"""Disposable PostgreSQL 0033 restricted-observer transaction acceptance.

Only 0024 and 0033 DDL are needed for this narrow test. Full-chain migration,
deployment OS isolation, trusted clocks, and Gate 3 remain separate checks.
"""

from __future__ import annotations

import json
import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_publication_ack import (
    PublicReceiptPublicationAckError,
    PublicReceiptPublicationAckRepository,
)
from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
)
from app.public_market_source.public_receipt_storage import PublicJournalCheckpointV1
from tests.durable_migration_fixtures import load_migration

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
    database = "ctcc_public_ack_" + uuid4().hex
    witness_role = "ctcc_witness_" + uuid4().hex[:16]
    ack_role = "ctcc_observer_" + uuid4().hex[:16]
    assert re.fullmatch(r"ctcc_public_ack_[a-f0-9]{32}", database)
    assert re.fullmatch(r"ctcc_(witness|observer)_[a-f0-9]{16}", witness_role)
    assert re.fullmatch(r"ctcc_(witness|observer)_[a-f0-9]{16}", ack_role)
    admin = create_async_engine(raw_url, poolclass=NullPool)
    db_engine = witness_engine = ack_engine = None
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
            for revision in ("0024", "0033"):
                await connection.run_sync(migrate, revision, "upgrade")
        for role in (witness_role, ack_role):
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            created_roles.append(role)
        async with db_engine.begin() as connection:
            for role in (witness_role, ack_role):
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
                "public_receipt_publication_ack_append(text,bigint,text,text)",
                "public_receipt_publication_ack_read(text,bigint)",
            ):
                await connection.execute(
                    text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {ack_role}")
                )
        witness_engine = create_async_engine(
            db_url.set(username=witness_role, password=None), poolclass=NullPool
        )
        ack_engine = create_async_engine(
            db_url.set(username=ack_role, password=None), poolclass=NullPool
        )
        witness_repo = PublicReceiptWitnessRepository(
            async_sessionmaker(witness_engine, expire_on_commit=False)
        )
        ack_repo = PublicReceiptPublicationAckRepository(
            async_sessionmaker(ack_engine, expire_on_commit=False), witness_repo
        )
        await witness_repo.verify_role()
        await ack_repo.verify_role()
        yield witness_repo, ack_repo, db_engine, witness_engine, ack_engine, ack_role
    finally:
        if ack_engine is not None:
            await ack_engine.dispose()
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


def initial_checkpoint() -> PublicJournalCheckpointV1:
    genesis = uuid4().hex + uuid4().hex
    return PublicJournalCheckpointV1(
        genesis_sha256=genesis,
        root_device=1,
        root_inode=2,
        sequence=0,
        head_sha256=genesis,
        attempt_sequence=0,
        attempt_head_sha256=genesis,
    )


async def committed_attempt(repository):
    initial = initial_checkpoint()
    journal_id, operation = uuid4().hex, uuid4().hex
    plan_sha = uuid4().hex + uuid4().hex
    genesis = await repository.append(
        journal_id=journal_id,
        checkpoint=initial,
        transition="initialize",
        expected=None,
    )
    opened = await repository.append(
        journal_id=journal_id,
        checkpoint=initial,
        transition="open_attempt",
        expected=genesis,
        operation_id=operation,
        plan_sha256=plan_sha,
    )
    attempted_checkpoint = initial.model_copy(
        update={"attempt_sequence": 1, "attempt_head_sha256": "e" * 64}
    )
    attempted = await repository.append(
        journal_id=journal_id,
        checkpoint=attempted_checkpoint,
        transition="append_attempt",
        expected=opened,
        operation_id=operation,
        plan_sha256=plan_sha,
        attempt_outcome="completed_collection",
    )
    capture_checkpoint = attempted_checkpoint.model_copy(
        update={"sequence": 1, "head_sha256": "f" * 64}
    )
    return attempted, capture_checkpoint


def capture_record(attempt, checkpoint):
    return {
        "journal_key": checkpoint.genesis_sha256,
        "revision": attempt.revision + 1,
        "journal_id": attempt.journal_id,
        "root_device": checkpoint.root_device,
        "root_inode": checkpoint.root_inode,
        "transition": "append_capture",
        "state": "idle",
        "operation_id": attempt.operation_id,
        "plan_sha256": attempt.plan_sha256,
        "attempt_outcome": "completed_collection",
        "previous_record_sha256": attempt.record_sha256,
        "checkpoint_json": checkpoint.canonical_bytes().decode("utf-8"),
        "checkpoint_sha256": checkpoint.canonical_sha256(),
    }


async def raw_ack(connection, key, revision, record_sha, checkpoint_sha):
    await connection.execute(
        text("""
          SELECT public.public_receipt_publication_ack_append(
            :key,:revision,:record_sha,:checkpoint_sha)
        """),
        {
            "key": key,
            "revision": revision,
            "record_sha": record_sha,
            "checkpoint_sha": checkpoint_sha,
        },
    )


async def test_ack_binds_only_committed_capture_and_survives_new_session(
    isolated_database,
):
    witness_repo, ack_repo, db_engine, witness_engine, ack_engine, _ = isolated_database
    attempt, checkpoint = await committed_attempt(witness_repo)
    key = checkpoint.genesis_sha256
    revision = attempt.revision + 1
    with pytest.raises(
        PublicReceiptPublicationAckError, match="publication_ack_witness_replay_failed"
    ):
        await ack_repo.acknowledge(journal_key=key, witness_revision=revision)
    record = capture_record(attempt, checkpoint)
    async with witness_engine.connect() as writer:
        transaction = await writer.begin()
        try:
            await writer.execute(
                text(
                    "SELECT public.public_receipt_witness_append(CAST(:record AS jsonb))"
                ),
                {"record": json.dumps(record, sort_keys=True)},
            )
            uncommitted = (
                (
                    await writer.execute(
                        text("""
                      SELECT record_sha256 FROM public.public_receipt_witness_read(:key)
                      WHERE revision=:revision
                    """),
                        {"key": key, "revision": revision},
                    )
                )
                .one()
                .record_sha256
            )
            with pytest.raises(DBAPIError, match="public_receipt_ack_busy"):
                async with ack_engine.begin() as observer:
                    await raw_ack(
                        observer,
                        key,
                        revision,
                        uncommitted,
                        checkpoint.canonical_sha256(),
                    )
            await transaction.commit()
        finally:
            if transaction.is_active:
                await transaction.rollback()
    anchored = await witness_repo.read_revision(key, revision)
    assert anchored.record_sha256 == uncommitted
    observed = await ack_repo.acknowledge(journal_key=key, witness_revision=revision)
    assert observed.capture_sequence == 1
    assert observed.capture_head_sha256 == checkpoint.head_sha256
    assert observed.witness_record_sha256 == anchored.record_sha256
    assert observed.witness_recorded_at <= observed.acknowledged_at
    assert observed.host_ack_clock_bracket_verified
    assert observed.host_readback_clock_bracket_verified
    assert not observed.historical_decision_availability_verified
    assert not observed.trusted_clock_verified
    assert not observed.predictive_oos_eligible
    assert not observed.execution_authority
    replayed = await ack_repo.read(journal_key=key, witness_revision=revision)
    assert replayed.acknowledged_at == observed.acknowledged_at
    assert not replayed.host_ack_clock_bracket_verified
    assert not replayed.historical_decision_availability_verified
    assert (
        await ack_repo.acknowledge(journal_key=key, witness_revision=revision)
    ).acknowledged_at == observed.acknowledged_at
    with pytest.raises(DBAPIError, match="public_receipt_ack_immutable"):
        async with db_engine.begin() as connection:
            await connection.execute(
                text("""
                  UPDATE public.public_receipt_publication_acks
                  SET capture_sequence=2 WHERE journal_key=:key
                """),
                {"key": key},
            )
    with pytest.raises(DBAPIError, match="public_receipt_ack_downgrade_requires_empty"):
        async with db_engine.begin() as connection:
            await connection.run_sync(migrate, "0033", "downgrade")


async def test_observer_cannot_ack_own_uncommitted_witness_insert(
    isolated_database,
):
    witness_repo, ack_repo, db_engine, _, ack_engine, ack_role = isolated_database
    attempt, checkpoint = await committed_attempt(witness_repo)
    record = capture_record(attempt, checkpoint)
    key, revision = record["journal_key"], record["revision"]
    async with db_engine.begin() as connection:
        for function in (
            "public_receipt_witness_append(jsonb)",
            "public_receipt_witness_read(text)",
        ):
            await connection.execute(
                text(f"GRANT EXECUTE ON FUNCTION public.{function} TO {ack_role}")
            )
    try:
        with pytest.raises(
            PublicReceiptPublicationAckError,
            match="restricted_publication_ack_role_required",
        ):
            await ack_repo.verify_role()
        with pytest.raises(DBAPIError, match="public_receipt_ack_role_denied"):
            async with ack_engine.begin() as connection:
                await connection.execute(
                    text(
                        "SELECT public.public_receipt_witness_append("
                        "CAST(:record AS jsonb))"
                    ),
                    {"record": json.dumps(record, sort_keys=True)},
                )
                uncommitted = (
                    (
                        await connection.execute(
                            text("""
                          SELECT record_sha256
                          FROM public.public_receipt_witness_read(:key)
                          WHERE journal_key=:key AND revision=:revision
                        """),
                            {"key": key, "revision": revision},
                        )
                    )
                    .one()
                    .record_sha256
                )
                await raw_ack(
                    connection,
                    key,
                    revision,
                    uncommitted,
                    checkpoint.canonical_sha256(),
                )
    finally:
        async with db_engine.begin() as connection:
            for function in (
                "public_receipt_witness_append(jsonb)",
                "public_receipt_witness_read(text)",
            ):
                await connection.execute(
                    text(
                        f"REVOKE EXECUTE ON FUNCTION public.{function} FROM {ack_role}"
                    )
                )
    with pytest.raises(
        PublicReceiptPublicationAckError, match="publication_ack_witness_replay_failed"
    ):
        await ack_repo.read(journal_key=key, witness_revision=revision)


async def test_empty_downgrade_and_reupgrade(isolated_database):
    _, _, db_engine, _, _, _ = isolated_database
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0033", "downgrade")
    async with db_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT to_regclass('public.public_receipt_publication_acks')")
            )
            is None
        )
    async with db_engine.begin() as connection:
        await connection.run_sync(migrate, "0033", "upgrade")
