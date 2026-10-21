"""Real PostgreSQL 0024 witness/role acceptance on an isolated migrated DB.

No public network, account credentials, exchange writes or local Docker setup.
The hermetic harness supplies the disposable DATABASE_URL and destroys it.
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
from sqlalchemy.exc import DBAPIError
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
    observed = await repository.observe_committed_revision(
        initial.genesis_sha256, final.revision
    )
    assert observed.revision == final
    assert observed.observed_at.tzinfo is not None
    assert observed.observed_at.utcoffset().total_seconds() == 0
    assert observed.trusted_clock_verified is False
    assert observed.predictive_oos_eligible is False
    assert observed.execution_authority is False
    with pytest.raises(PublicWitnessError, match="witness_revision_missing"):
        await repository.observe_committed_revision(
            initial.genesis_sha256, final.revision + 1
        )
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


@pytest.mark.parametrize(
    ("device", "inode"),
    [
        pytest.param(2**63 + 1, 2**64 + 3, id="unsigned-windows-identity-width"),
        pytest.param(2**64 - 1, 2**128 - 1, id="full-native-identity-width"),
        pytest.param(0, 2**64, id="zero-device-and-wide-file-id"),
    ],
)
async def test_native_identity_round_trips_without_signed_conversion(
    witness_database, device, inode
):
    repository, _, admin_engine = witness_database
    initial = checkpoint().model_copy(
        update={"root_device": device, "root_inode": inode}
    )
    original = initial.canonical_bytes()
    recorded = await repository.append(
        journal_id=uuid4().hex,
        checkpoint=initial,
        transition="initialize",
        expected=None,
    )
    assert type(recorded.root_device) is int and recorded.root_device == device
    assert type(recorded.root_inode) is int and recorded.root_inode == inode
    assert recorded.checkpoint.canonical_bytes() == original
    assert await repository.read_latest(initial.genesis_sha256) == recorded
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT root_device,root_inode,checkpoint_json,checkpoint_sha256 "
                    "FROM public.public_receipt_witness_revisions "
                    "WHERE journal_key=:key AND revision=0"
                ),
                {"key": initial.genesis_sha256},
            )
        ).one()
    assert row.root_device == device and row.root_inode == inode
    assert row.checkpoint_json.encode("utf-8") == original
    assert row.checkpoint_sha256 == hashlib.sha256(original).hexdigest()
    assert recorded.execution_authority is False
    catalog_sql = text("""
      SELECT c.oid::bigint AS table_oid,
        (SELECT jsonb_agg(jsonb_build_array(a.attname,a.atttypid::bigint,
                   a.atttypmod,a.attnotnull) ORDER BY a.attname)
         FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid
           AND a.attname IN ('root_device','root_inode')) AS columns,
        (SELECT jsonb_agg(jsonb_build_array(p.oid::bigint,p.prosrc,
                   p.proacl::text,p.prosecdef,p.proconfig) ORDER BY p.oid)
         FROM pg_catalog.pg_proc p WHERE p.oid IN (
           'public.public_receipt_witness_append(jsonb)'::regprocedure,
           'public.public_receipt_witness_insert_guard()'::regprocedure,
           'public.public_receipt_witness_read(text)'::regprocedure)) AS functions,
        (SELECT jsonb_agg(jsonb_build_array(k.oid::bigint,k.conname,
                   pg_catalog.pg_get_constraintdef(k.oid)) ORDER BY k.conname)
         FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid) AS constraints
      FROM pg_catalog.pg_class c
      WHERE c.oid='public.public_receipt_witness_revisions'::regclass
    """)
    async with admin_engine.connect() as connection:
        before = (await connection.execute(catalog_sql)).one()
    with pytest.raises(
        DBAPIError, match="public_witness_wide_identity_downgrade_requires_empty"
    ):
        async with admin_engine.begin() as connection:
            await connection.run_sync(_downgrade_native_identity)
    async with admin_engine.connect() as connection:
        assert (await connection.execute(catalog_sql)).one() == before
    assert await repository.read_latest(initial.genesis_sha256) == recorded


def _downgrade_native_identity(connection):
    path = (
        Path(__file__).parents[2]
        / "migrations/versions/0047_public_witness_native_identity.py"
    )
    spec = importlib.util.spec_from_file_location("native_identity_0047_test", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()


@pytest.mark.parametrize(
    ("field", "token", "checkpoint_text"),
    [
        pytest.param("root_device", "1.2", False, id="fraction-must-not-round"),
        pytest.param("root_device", "1.0", False, id="decimal-must-not-coerce"),
        pytest.param("root_device", "true", False, id="boolean-must-not-coerce"),
        pytest.param("root_device", '"1"', False, id="string-must-not-coerce"),
        pytest.param("root_device", str(2**64), False, id="volume-overflow"),
        pytest.param("root_inode", "2.1", False, id="file-id-fraction-must-not-round"),
        pytest.param("root_inode", "0", False, id="file-id-zero"),
        pytest.param("root_inode", str(2**128), False, id="file-id-overflow"),
        pytest.param("root_device", "1e0", True, id="checkpoint-exponent-lexeme"),
        pytest.param("root_device", "1.0", True, id="checkpoint-decimal-lexeme"),
        pytest.param("root_inode", '"2"', True, id="checkpoint-string-identity"),
    ],
)
async def test_restricted_function_rejects_identity_token_coercion(
    witness_database, field, token, checkpoint_text
):
    repository, restricted_engine, _ = witness_database
    initial = checkpoint()
    raw = initial.canonical_bytes()
    record = {
        "journal_key": initial.genesis_sha256,
        "revision": 0,
        "journal_id": uuid4().hex,
        "root_device": initial.root_device,
        "root_inode": initial.root_inode,
        "transition": "initialize",
        "state": "idle",
        "operation_id": None,
        "plan_sha256": None,
        "attempt_outcome": None,
        "previous_record_sha256": None,
        "checkpoint_json": raw.decode("utf-8"),
        "checkpoint_sha256": hashlib.sha256(raw).hexdigest(),
    }
    needle = f'"{field}":{record[field]}'
    if checkpoint_text:
        # JSONB discards some original number spellings. The immutable TEXT
        # checkpoint retains them, so test that source instead of inventing
        # evidence about an original wire lexeme that is no longer available.
        assert record["checkpoint_json"].count(needle) == 1
        record["checkpoint_json"] = record["checkpoint_json"].replace(
            needle, f'"{field}":{token}', 1
        )
        record["checkpoint_sha256"] = hashlib.sha256(
            record["checkpoint_json"].encode("utf-8")
        ).hexdigest()
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    else:
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        assert payload.count(needle) == 1
        payload = payload.replace(needle, f'"{field}":{token}', 1)
    with pytest.raises(DBAPIError):
        async with restricted_engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT public.public_receipt_witness_append(CAST(:record AS jsonb))"
                ),
                {"record": payload},
            )
    assert await repository.read_latest(initial.genesis_sha256) is None
