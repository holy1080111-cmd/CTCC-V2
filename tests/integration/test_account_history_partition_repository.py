"""Actual isolated PG17 mechanisms with explicitly synthetic source inputs.

B1/B3 are originally acquired through the synthetic transport and committed by
their real repositories. Linkage locators below are NEW declared synthetic
records, never original native companions or opaque owners. Direct SQL tests
do not bypass a formal repository gate: no baseline is injected, and an unsealed
repository must deny. Only the primary retention case leaves committed rows.
"""

import asyncio
import hashlib
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import account_history_partition as repository
from app.database.repositories.account_observation_index import (
    AccountObservationIndexRepository,
)
from app.domain import account_history_partition_catalog_v1 as policy
from app.domain import account_history_partition_v1 as records
from app.trade_qualification.reservations import LedgerScope
from tests.controlled_writer_coverage_fixtures import seed_controlled_writer_control
from tests.integration.test_account_observation_index_repository import (
    additional_capture,
    stored,
)
from tests.unit.test_account_history_partition_v1 import original_linkage

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_ROOT = Path(__file__).resolve().parents[2]


def _now():
    return datetime.now(UTC)


def _migrate(connection, target, direction):
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0051"
    resolver = (
        script._upgrade_revs if direction == "upgrade" else script._downgrade_revs
    )
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: resolver(target, heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@pytest.fixture
async def partition_primary():
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip("explicit isolated migrated DATABASE_URL required")
    url = make_url(raw)
    assert url.drivername == "postgresql+asyncpg"
    assert re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
    assert url.username == "ctcc" and url.password is None
    assert url.port == 5432 and url.database == "ctcc" and not url.query
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            assert (
                170000
                <= await connection.scalar(
                    text("SELECT current_setting('server_version_num')::int")
                )
                < 180000
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0051"
            )
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _restricted_role(admin):
    role = "ctcc_hp50_" + uuid4().hex
    assert re.fullmatch(r"ctcc_hp50_[a-f0-9]{32}", role)
    created = False
    engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
            created = True
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            for name in (
                "account_history_partition_v1_append",
                "account_history_partition_v1_read",
            ):
                await connection.execute(
                    text(
                        f'GRANT EXECUTE ON FUNCTION public.{name}({policy.FUNCTION_SIGNATURES[name]}) TO "{role}"'
                    )
                )
        engine = create_async_engine(admin.url.set(username=role), poolclass=NullPool)
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                for name in (
                    "account_history_partition_v1_append",
                    "account_history_partition_v1_read",
                ):
                    # An empty downgrade may have removed these exact objects.
                    signature = f"public.{name}({policy.FUNCTION_SIGNATURES[name]})"
                    if await connection.scalar(
                        text("SELECT to_regprocedure(:signature) IS NOT NULL"),
                        {"signature": signature},
                    ):
                        await connection.execute(
                            text(f'REVOKE ALL ON FUNCTION {signature} FROM "{role}"')
                        )
                await connection.execute(
                    text(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
                )
                await connection.execute(text(f'DROP ROLE "{role}"'))


@asynccontextmanager
async def _child(parent):
    name = "ctcc_hp50_" + uuid4().hex
    assert re.fullmatch(r"ctcc_hp50_[a-f0-9]{32}", name)
    created = False
    engine = None
    try:
        async with parent.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            assert await connection.scalar(text("SELECT current_database()")) == "ctcc"
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        engine = create_async_engine(parent.url.set(database=name), poolclass=NullPool)
        async with engine.begin() as connection:
            await connection.run_sync(_migrate, "0050", "upgrade")
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with parent.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                assert (
                    await connection.scalar(text("SELECT current_database()")) == "ctcc"
                )
                assert parent.url.database != name
                await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def partition_child(partition_primary):
    async with _child(partition_primary) as child:
        yield child


async def _sources(monkeypatch, admin, *, count=1):
    factory = async_sessionmaker(admin, expire_on_commit=False, autoflush=False)
    index, ledger_scope, _, _, first = await stored(monkeypatch, (admin, factory))
    documents = [first.document_json]
    for sequence in range(1, count):
        reference, window = await additional_capture(
            monkeypatch, index, ledger_scope, []
        )
        current = await index.append(
            ledger_scope, reference, window, expected_revision=sequence
        )
        documents.append(current.document_json)
    scope = records.HistoryPartitionScopeV1("demo", ledger_scope.account_id, "USDT")
    async with admin.begin() as connection:
        control, _ = await seed_controlled_writer_control(
            connection, uid=scope.account_id
        )
        state = (
            await connection.execute(
                text(
                    "SELECT account_revision,ledger_revision FROM public.qualification_account_scopes WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT'"
                ),
                {"uid": scope.account_id},
            )
        ).one()
    return (
        scope,
        tuple(documents),
        (control.revision, state.account_revision, state.ledger_revision),
        control.lease_until,
    )


def _member(scope, document):
    # Construct a new labelled synthetic linkage against the ORIGINAL B3 bytes;
    # neither a previous packet nor native issuer provenance is rewritten.
    linkage = original_linkage(document)
    linkage["scope"] = records._scope(scope)
    raw_linkage = records.canonical(linkage)
    member = records.derive_native_member_registration_v1(
        scope=scope,
        b3_document_raw=document,
        native_linkage_raw=raw_linkage,
        expected_b3_policy_sha256=json.loads(document)["policy_sha256"],
        expected_b3_event_sha256=records.digest(document),
    )
    return member.record_json, raw_linkage


async def _settings(connection):
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await connection.execute(
        text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
    )
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await connection.execute(text("SET LOCAL statement_timeout='10000ms'"))


async def _append(
    connection,
    scope,
    document,
    member,
    revisions,
    lease,
    *,
    checkpoint=None,
    expires=None,
):
    return await connection.scalar(
        text(repository._APPEND_SQL),
        {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "currency": scope.settlement_currency,
            "head": records.digest(document),
            "previous": None,
            "registration": member.decode("ascii"),
            "checkpoint": None if checkpoint is None else checkpoint.decode("ascii"),
            "control_revision": revisions[0],
            "account_revision": revisions[1],
            "ledger_revision": revisions[2],
            "expires": min(lease, _now() + timedelta(seconds=20))
            if expires is None
            else expires,
        },
    )


async def _read(connection, scope):
    return await connection.scalar(
        text(repository._READ_SQL),
        {
            "environment": scope.environment,
            "account_id": scope.account_id,
            "currency": scope.settlement_currency,
        },
    )


async def _snapshot(admin):
    async with admin.connect() as connection, connection.begin():
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        await connection.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        catalog = await repository.read_account_history_partition_catalog(connection)
        rows = await repository.read_account_history_partition_retention(connection)
    return head, catalog, rows


def _receipt_side(snapshot):
    head, catalog, retained = snapshot
    return {
        "head": head,
        "rows_sha256": retained["rows_sha256"],
        "counts": retained["counts"],
        "catalog_sha256": records.digest(records.canonical(catalog)),
    }


async def test_partition_restricted_role_deployment_pending_and_actual_commit_readback(
    monkeypatch, partition_child
):
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_child)
    member, linkage = _member(scope, documents[-1])
    async with _restricted_role(partition_child) as restricted:
        factory = async_sessionmaker(
            restricted, expire_on_commit=False, autoflush=False
        )
        repo = repository.AccountHistoryPartitionRepository(factory, clock=_now)
        async with restricted.begin() as connection:
            await _settings(connection)
            catalog = await repository.read_account_history_partition_catalog(
                connection
            )
            policy.verify_account_history_partition_catalog(
                catalog, require_native_baseline=False
            )
            policy.verify_runtime_role(catalog)
        if policy.PG17_DEPARSED_BASELINE is None:
            with pytest.raises(
                policy.AccountHistoryPartitionError,
                match="^account_history_partition_native_baseline_pending$",
            ):
                await repo.verify_deployment()
            # Direct restricted-role SQL proves storage mechanics only. It
            # does not convert a pending formal repository into an admitted one.
            async with restricted.begin() as connection:
                await _settings(connection)
                transaction = await connection.scalar(
                    text("SELECT pg_current_xact_id()::text")
                )
                assert await _append(
                    connection, scope, documents[-1], member, revisions, lease
                ) == records.digest(member)
        else:
            await repo.verify_deployment()
            after = await repo.append_member_and_checkpoint(
                scope,
                b3_document_raw=documents[-1],
                native_linkage_raw=linkage,
                expected_b3_policy_sha256=json.loads(documents[-1])["policy_sha256"],
                expected_b3_event_sha256=records.digest(documents[-1]),
                expected_previous_checkpoint_sha256=None,
                expected_control_revision=revisions[0],
                expected_account_revision=revisions[1],
                expected_ledger_revision=revisions[2],
                expires_at=min(lease, _now() + timedelta(seconds=20)),
            )
            transaction = after.transaction_id
            assert after.members[0].record_json == member
            assert not after.account_complete and not after.execution_authority
        async with restricted.begin() as connection:
            await _settings(connection)
            raw = await _read(connection, scope)
            assert raw["transaction_id"] != transaction
            observed = repository._readback(raw, scope, _now())
            assert observed.members[0].record_json == member
            assert observed.head_sha256 == records.digest(documents[-1])
            assert not observed.account_complete and not observed.execution_authority
        for statement in (
            f"SELECT * FROM public.{policy.TABLES[0]}",
            f"UPDATE public.{policy.TABLES[0]} SET b3_sequence=b3_sequence",
            f"TRUNCATE public.{policy.TABLES[1]}",
            "SELECT public.account_history_partition_v1_json('{}')",
        ):
            with pytest.raises(DBAPIError):
                async with restricted.begin() as connection:
                    await connection.execute(text(statement))


async def test_partition_current_nonempty_downgrade_retains_exact_catalog_and_rows(
    monkeypatch, partition_primary
):
    # Leave this real row on the primary0050 database for producer before/after
    # downgrade stages. Retention is deliberate, not fixture cleanup leakage.
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_primary)
    member, _ = _member(scope, documents[-1])
    async with _restricted_role(partition_primary) as restricted:
        async with restricted.begin() as connection:
            await _settings(connection)
            await _append(connection, scope, documents[-1], member, revisions, lease)
        before = await _snapshot(partition_primary)
        assert before[0] == "0051" and sum(before[2]["counts"].values()) > 0
        with pytest.raises(
            DBAPIError, match="account_history_partition_downgrade_requires_empty"
        ):
            async with partition_primary.begin() as connection:
                await connection.run_sync(_migrate, "0049", "downgrade")
        after = await _snapshot(partition_primary)
        assert before == after
        directory = os.environ.get("CTCC_COVERAGE_RECEIPT_DIR")
        if directory is not None:
            output = Path(directory) / "account-history-partition-migration.json"
            result = {
                "schema_version": "ctcc.account_history_partition_migration_acceptance.v2",
                "manifest_sha256": hashlib.sha256(
                    (_ROOT / "MANIFEST.sha256").read_bytes()
                ).hexdigest(),
                "repository_sha256": hashlib.sha256(
                    (
                        _ROOT / "app/database/repositories/account_history_partition.py"
                    ).read_bytes()
                ).hexdigest(),
                "migration_sha256": hashlib.sha256(
                    (
                        _ROOT / "migrations/versions/0050_account_history_partition.py"
                    ).read_bytes()
                ).hexdigest(),
                "server_major": 17,
                "before": _receipt_side(before),
                "after": _receipt_side(after),
                "refusal_code": "account_history_partition_downgrade_requires_empty",
                "downgrade_target": "0049",
                "native_baseline_admission": False,
                "execution_authority": False,
            }
            raw = records.canonical(result)
            with output.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            assert output.read_bytes() == raw


async def test_partition_empty_downgrade_reupgrade_preserves_old49(partition_child):
    async with partition_child.begin() as connection:
        for table in policy.TABLES:
            assert not await connection.scalar(
                text(f"SELECT EXISTS(SELECT 1 FROM public.{table})")
            )
        await connection.run_sync(_migrate, "0049", "downgrade")
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0049"
        )
        assert await connection.scalar(
            text(
                "SELECT to_regprocedure('public.controlled_writer_coverage_v1_read(text,text,bigint,text,text)') IS NOT NULL"
            )
        )
        await connection.run_sync(_migrate, "0050", "upgrade")
        catalog = await repository.read_account_history_partition_catalog(connection)
        policy.verify_account_history_partition_catalog(
            catalog, require_native_baseline=False
        )
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0050"
        )


async def test_partition_parent_writer_nowait_refusal_retains_head_and_rows(
    monkeypatch, partition_child
):
    await _sources(monkeypatch, partition_child)
    before = await _snapshot(partition_child)
    async with partition_child.connect() as writer, writer.begin():
        # Simulate the native writer relation lock without an invalid no-op
        # UPDATE: DB0017 requires every scope UPDATE to advance ledger_revision.
        await writer.execute(
            text(
                "LOCK TABLE public.qualification_account_scopes IN ROW EXCLUSIVE MODE NOWAIT"
            )
        )
        writer_pid, granted = (
            await writer.execute(
                text("""
                  SELECT pg_catalog.pg_backend_pid(), EXISTS(
                    SELECT 1 FROM pg_catalog.pg_locks
                    WHERE pid=pg_catalog.pg_backend_pid()
                      AND locktype='relation'
                      AND relation='public.qualification_account_scopes'::pg_catalog.regclass
                      AND mode='RowExclusiveLock' AND granted)
                """)
            )
        ).one()
        assert type(writer_pid) is int and writer_pid > 0 and granted is True
        with pytest.raises(DBAPIError) as error:
            async with partition_child.begin() as migration:
                assert (
                    await migration.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
                    != writer_pid
                )
                await migration.run_sync(_migrate, "0049", "downgrade")
        assert error.value.orig.sqlstate == "55P03"
    assert await _snapshot(partition_child) == before


async def test_partition_checkpoint32_keeps_missing_registration_and_rejects_backfill(
    monkeypatch, partition_child
):
    scope, documents, revisions, lease = await _sources(
        monkeypatch, partition_child, count=32
    )
    member, _ = _member(scope, documents[-1])
    checkpoint = records.build_partition_checkpoint_v1(
        scope=scope,
        b3_documents=documents,
        native_registrations=(None,) * 31 + (member,),
        previous_checkpoints=(),
        expected_b3_policy_sha256=json.loads(documents[-1])["policy_sha256"],
        expected_b3_head_sha256=records.digest(documents[-1]),
    ).record_json
    async with _restricted_role(partition_child) as restricted:
        async with restricted.begin() as connection:
            await _settings(connection)
            await _append(
                connection,
                scope,
                documents[-1],
                member,
                revisions,
                lease,
                checkpoint=checkpoint,
            )
        before = await _snapshot(partition_child)
        async with restricted.begin() as connection:
            await _settings(connection)
            raw = await _read(connection, scope)
            observed = repository._readback(raw, scope, _now())
            assert observed.checkpoints[0].record_json == checkpoint
            state = json.loads(checkpoint)
            assert state["missing_native_registration_sequences"] == list(range(1, 32))
            assert state["missing_native_registrations_through_sequence"] == 31
            assert (
                not state["source_authenticity_verified"]
                and not observed.execution_authority
            )
        older, _ = _member(scope, documents[-2])
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await _settings(connection)
                await _append(connection, scope, documents[-2], older, revisions, lease)
        assert await _snapshot(partition_child) == before
        async with partition_child.begin() as connection:
            for table in policy.TABLES:
                with pytest.raises(
                    DBAPIError, match="account_history_partition_immutable"
                ):
                    async with connection.begin_nested():
                        await connection.execute(text(f"DELETE FROM public.{table}"))


async def test_partition_same_uid_concurrent_append_commits_once(
    monkeypatch, partition_child
):
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_child)
    member, _ = _member(scope, documents[-1])
    async with _restricted_role(partition_child) as restricted:

        async def worker():
            try:
                async with restricted.begin() as connection:
                    await _settings(connection)
                    return await _append(
                        connection, scope, documents[-1], member, revisions, lease
                    )
            except DBAPIError:
                return "denied"

        results = await asyncio.gather(worker(), worker())
        assert sorted(results) == sorted([records.digest(member), "denied"])
        retained = (await _snapshot(partition_child))[2]
        assert retained["counts"] == {policy.TABLES[0]: 1, policy.TABLES[1]: 0}


@pytest.mark.parametrize(
    "fence",
    [
        "control",
        "account",
        "ledger",
        "expired",
        "nested_duplicate",
        "noncanonical",
        "boolean_sequence",
    ],
)
async def test_partition_invalid_fences_and_raw_bytes_roll_back(
    monkeypatch, partition_child, fence
):
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_child)
    member, _ = _member(scope, documents[-1])
    changed = list(revisions)
    expires = None
    if fence in {"control", "account", "ledger"}:
        changed[{"control": 0, "account": 1, "ledger": 2}[fence]] += 1
    elif fence == "expired":
        expires = _now() - timedelta(seconds=1)
    elif fence == "nested_duplicate":
        value = json.loads(member)
        original = value["native_linkage_json"]
        value["native_linkage_json"] = original.replace(
            '"raw_root":{', '"raw_root":{"device":7,', 1
        )
        assert value["native_linkage_json"] != original
        member = records.canonical(value)
    elif fence == "noncanonical":
        member = b" " + member
    else:
        value = json.loads(member)
        value["b3_sequence"] = True
        member = records.canonical(value)
    async with _restricted_role(partition_child) as restricted:
        before = await _snapshot(partition_child)
        with pytest.raises(DBAPIError):
            async with restricted.begin() as connection:
                await _settings(connection)
                await _append(
                    connection,
                    scope,
                    documents[-1],
                    member,
                    tuple(changed),
                    lease,
                    expires=expires,
                )
        assert await _snapshot(partition_child) == before


async def test_partition_expire_during_write_preserves_previous_committed_record(
    monkeypatch, partition_child
):
    scope, documents, revisions, lease = await _sources(monkeypatch, partition_child)
    member, _ = _member(scope, documents[-1])
    async with _restricted_role(partition_child) as restricted:
        async with restricted.begin() as connection:
            await _settings(connection)
            await _append(connection, scope, documents[-1], member, revisions, lease)
        # Obtain a genuinely NEW B1/B3 original; never change the accepted bytes.
        factory = async_sessionmaker(
            partition_child, expire_on_commit=False, autoflush=False
        )
        index = AccountObservationIndexRepository(factory, clock=_now)
        ledger_scope = LedgerScope(
            account_id=scope.account_id, settlement_currency="USDT"
        )
        reference, window = await additional_capture(
            monkeypatch, index, ledger_scope, []
        )
        second = await index.append(
            ledger_scope, reference, window, expected_revision=1
        )
        next_member, _ = _member(scope, second.document_json)
        async with partition_child.begin() as connection:
            await connection.execute(
                text(
                    "CREATE FUNCTION public.hp50_test_delay() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.25); RETURN NEW; END $$"
                )
            )
            await connection.execute(
                text(
                    f"CREATE TRIGGER hp50_test_delay AFTER INSERT ON public.{policy.TABLES[0]} FOR EACH ROW EXECUTE FUNCTION public.hp50_test_delay()"
                )
            )
        before = await _snapshot(partition_child)
        with pytest.raises(
            DBAPIError, match="account_history_partition_expired_before_commit"
        ):
            async with restricted.begin() as connection:
                await _settings(connection)
                await _append(
                    connection,
                    scope,
                    second.document_json,
                    next_member,
                    revisions,
                    lease,
                    expires=_now() + timedelta(milliseconds=150),
                )
        assert await _snapshot(partition_child) == before
        assert before[2]["counts"] == {policy.TABLES[0]: 1, policy.TABLES[1]: 0}


async def test_partition_raw_catalog_pending_and_other_schema_trigger_identity_denial(
    partition_child,
):
    async with _restricted_role(partition_child) as restricted:
        async with restricted.begin() as connection:
            await _settings(connection)
            observed = await repository.read_account_history_partition_catalog(
                connection
            )
            original = records.canonical(observed)
            policy.verify_account_history_partition_catalog(
                observed, require_native_baseline=False
            )
            policy.verify_runtime_role(observed)
            for row in observed["parents"] + observed["tables"] + observed["functions"]:
                for key, value in row.items():
                    if key == "oid" or key.endswith("_oid"):
                        assert type(value) is int and 0 < value < 2**32
            for row in observed["constraints"]:
                assert type(row["table_oid"]) is int and 0 < row["table_oid"] < 2**32
                assert (
                    row["target_oid"] is None
                    or type(row["target_oid"]) is int
                    and 0 < row["target_oid"] < 2**32
                )
            for row in observed["triggers"]:
                assert (
                    type(row["function_oid"]) is int and 0 < row["function_oid"] < 2**32
                )
                assert (
                    type(row["function_namespace_oid"]) is int
                    and 0 < row["function_namespace_oid"] < 2**32
                )
            if policy.PG17_DEPARSED_BASELINE is None:
                with pytest.raises(
                    policy.AccountHistoryPartitionError,
                    match="^account_history_partition_native_baseline_pending$",
                ):
                    policy.verify_account_history_partition_catalog(observed)
            else:
                policy.verify_account_history_partition_catalog(observed)
            assert records.canonical(observed) == original
        async with partition_child.begin() as connection:
            await connection.execute(text("CREATE SCHEMA hp50_other"))
            await connection.execute(
                text(
                    "CREATE FUNCTION hp50_other.account_history_partition_v1_member_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
                )
            )
            await connection.execute(
                text(f"DROP TRIGGER history_member_insert ON public.{policy.TABLES[0]}")
            )
            await connection.execute(
                text(
                    f"CREATE TRIGGER history_member_insert BEFORE INSERT ON public.{policy.TABLES[0]} FOR EACH ROW EXECUTE FUNCTION hp50_other.account_history_partition_v1_member_guard()"
                )
            )
        async with restricted.begin() as connection:
            await _settings(connection)
            changed = await repository.read_account_history_partition_catalog(
                connection
            )
            with pytest.raises(
                policy.AccountHistoryPartitionError,
                match="^account_history_partition_trigger_identity_drift$",
            ):
                policy.verify_account_history_partition_catalog(
                    changed, require_native_baseline=False
                )
