"""Real PostgreSQL 0024 witness/role acceptance on an isolated migrated DB.

No public network, account credentials, exchange writes or local Docker setup.
The hermetic harness supplies the disposable DATABASE_URL and destroys it.
"""

from __future__ import annotations

import asyncio
import os
import re
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories.public_receipt_witness import (
    PublicReceiptWitnessRepository,
    PublicWitnessError,
    WitnessRevision,
)
from app.public_market_source.public_receipt_storage import PublicJournalCheckpointV1

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def witness_database():
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        pytest.skip("explicit isolated DATABASE_URL required")
    role = f"ctcc_wit_{uuid4().hex[:18]}"
    admin_engine = create_async_engine(raw_url, poolclass=NullPool)
    restricted_engine = None
    created = False
    try:
        async with admin_engine.begin() as connection:
            # The disposable hermetic PostgreSQL fixture uses trust auth. No
            # password is created or logged; production must use independent
            # authentication and a separately controlled restricted role.
            await connection.execute(text(f"CREATE ROLE {role} LOGIN"))
            await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            await connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION public.public_receipt_witness_append(jsonb) "
                    f"TO {role}"
                )
            )
            await connection.execute(
                text(
                    "GRANT EXECUTE ON FUNCTION public.public_receipt_witness_read(text) "
                    f"TO {role}"
                )
            )
        created = True
        restricted_url = make_url(raw_url).set(username=role, password=None)
        restricted_engine = create_async_engine(restricted_url, poolclass=NullPool)
        repository = PublicReceiptWitnessRepository(
            async_sessionmaker(restricted_engine, expire_on_commit=False)
        )
        await repository.verify_role()
        yield repository, restricted_engine, admin_engine
    finally:
        if restricted_engine is not None:
            await restricted_engine.dispose()
        try:
            if created:
                async with admin_engine.begin() as connection:
                    await connection.execute(
                        text(
                            "REVOKE EXECUTE ON FUNCTION "
                            "public.public_receipt_witness_append(jsonb) "
                            f"FROM {role}"
                        )
                    )
                    await connection.execute(
                        text(
                            "REVOKE EXECUTE ON FUNCTION "
                            "public.public_receipt_witness_read(text) "
                            f"FROM {role}"
                        )
                    )
                    await connection.execute(
                        text(f"REVOKE USAGE ON SCHEMA public FROM {role}")
                    )
                    await connection.execute(text(f"DROP ROLE {role}"))
        finally:
            await admin_engine.dispose()


def checkpoint():
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


async def test_restricted_function_only_role_and_monotonic_cas(witness_database):
    repository, restricted_engine, admin_engine = witness_database
    initial = checkpoint()
    journal_id = uuid4().hex
    recorded = await repository.append(
        journal_id=journal_id,
        checkpoint=initial,
        transition="initialize",
        expected=None,
    )
    assert recorded.revision == 0 and recorded.state == "idle"
    assert await repository.read_latest(initial.genesis_sha256) == recorded
    async with restricted_engine.connect() as connection:
        facts = (
            await connection.execute(
                text("""
                  SELECT current_user=session_user AS direct_login,
                    has_table_privilege(current_user,
                      'public.public_receipt_witness_revisions','SELECT')
                      AS table_select,
                    has_table_privilege(current_user,
                      'public.public_receipt_witness_revisions','INSERT')
                      AS table_insert
                """)
            )
        ).one()
    assert facts.direct_login and not facts.table_select and not facts.table_insert
    operation = uuid4().hex
    plan = uuid4().hex + uuid4().hex

    async def open_once():
        return await repository.append(
            journal_id=journal_id,
            checkpoint=initial,
            transition="open_attempt",
            expected=recorded,
            operation_id=operation,
            plan_sha256=plan,
        )

    attempts = await asyncio.gather(open_once(), open_once(), return_exceptions=True)
    assert sum(type(item) is WitnessRevision for item in attempts) == 1
    assert sum(type(item) is PublicWitnessError for item in attempts) == 1
    opened = await repository.read_latest(initial.genesis_sha256)
    assert opened.revision == 1 and opened.state == "open"
    anchored = initial.model_copy(
        update={"attempt_sequence": 1, "attempt_head_sha256": "e" * 64}
    )
    attempt = await repository.append(
        journal_id=journal_id,
        checkpoint=anchored,
        transition="append_attempt",
        expected=opened,
        operation_id=operation,
        plan_sha256=plan,
        attempt_outcome="completed_collection",
    )
    captured = anchored.model_copy(update={"sequence": 1, "head_sha256": "f" * 64})
    final = await repository.append(
        journal_id=journal_id,
        checkpoint=captured,
        transition="append_capture",
        expected=attempt,
        operation_id=operation,
        plan_sha256=plan,
        attempt_outcome="completed_collection",
    )
    assert final.revision == 3 and final.state == "idle"
    assert await repository.read_latest(initial.genesis_sha256) == final
    with pytest.raises(PublicWitnessError):
        await repository.append(
            journal_id=journal_id,
            checkpoint=initial,
            transition="open_attempt",
            expected=recorded,
            operation_id=uuid4().hex,
            plan_sha256=plan,
        )
    # The ordinary CTCC DB login is the migration/table owner in this
    # isolated fixture. It must not be accepted as a capture witness role.
    admin_repository = PublicReceiptWitnessRepository(
        async_sessionmaker(admin_engine, expire_on_commit=False)
    )
    with pytest.raises(PublicWitnessError, match="restricted_witness_role_required"):
        await admin_repository.verify_role()


async def test_restricted_role_rejects_direct_column_and_table_grants(
    witness_database,
):
    repository, restricted_engine, admin_engine = witness_database
    role = restricted_engine.url.username
    assert re.fullmatch(r"ctcc_wit_[a-f0-9]{18}", role)
    table = "public.public_receipt_witness_revisions"
    grants = (
        "SELECT (checkpoint_json)",
        "INSERT (journal_key)",
        "UPDATE (checkpoint_json)",
        "REFERENCES (journal_key)",
        "REFERENCES",
        "MAINTAIN",
    )
    for privilege in grants:
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f"GRANT {privilege} ON TABLE {table} TO {role}")
            )
        try:
            with pytest.raises(
                PublicWitnessError, match="restricted_witness_role_required"
            ):
                await repository.verify_role()
        finally:
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(f"REVOKE {privilege} ON TABLE {table} FROM {role}")
                )
        await repository.verify_role()


async def test_restricted_role_rejects_other_role_membership(witness_database):
    repository, restricted_engine, admin_engine = witness_database
    role = restricted_engine.url.username
    assert re.fullmatch(r"ctcc_wit_[a-f0-9]{18}", role)
    other = f"ctcc_wit_group_{uuid4().hex[:16]}"
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"CREATE ROLE {other} NOLOGIN"))
        await connection.execute(text(f"GRANT {other} TO {role}"))
    try:
        with pytest.raises(
            PublicWitnessError, match="restricted_witness_role_required"
        ):
            await repository.verify_role()
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"REVOKE {other} FROM {role}"))
            await connection.execute(text(f"DROP ROLE {other}"))
    await repository.verify_role()
