"""UNRUN R10 sealed-default PG17 mechanisms with synthetic stored B1/B3.

Real catalog metadata must satisfy the reviewed source and frame baselines with
require_native_baseline=True by default. These schema/read tests do not grant
account, source-ownership, portfolio or execution authority.
"""

import asyncio
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
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database.repositories import account_history_original_frame_v1 as decoder
from app.domain import account_history_original_frame_v1 as policy
from app.domain import account_history_original_source_catalog_v1 as source_guard
from app.domain import account_history_partition_catalog_v1 as parent
from scripts import current_contract_source_scopes_v1 as source_scopes
from tests.integration.test_account_history_partition_repository import _sources
from tests.original_frame_portable_pg_profile import (
    create_portable_reader_role,
    parse_portable_url,
    portable_profile,
    verify_portable_server,
    verify_portable_url,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]


def _now():
    return datetime.now(UTC)


def _migrate(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    source_scope = source_scopes.historical0052_source_scope(ROOT)
    assert script.get_current_head() == source_scope.actual_source_head
    context = MigrationContext.configure(
        connection, opts={"fn": lambda heads, _: script._upgrade_revs("0052", heads)}
    )
    with Operations.context(context), context.begin_transaction():
        context.run_migrations()


@pytest.fixture
async def original_frame_database():
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        pytest.skip(
            "explicit isolated PostgreSQL DATABASE_URL required; skip is not acceptance"
        )
    profile = portable_profile(ROOT)
    if profile is None:
        # Preserve the original explicit Docker fixture contract unchanged.
        url = make_url(raw)
        assert url.drivername == "postgresql+asyncpg"
        assert re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "")
        assert url.username == "ctcc" and url.password is None
        assert url.port == 5432 and url.database == "ctcc" and not url.query
    else:
        url = parse_portable_url(raw)
        verify_portable_url(url, profile)
    primary = create_async_engine(url, poolclass=NullPool)
    child_name = "ctcc_original_frame_" + uuid4().hex
    child = None
    created = False
    try:
        async with primary.connect() as connection:
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert (
                170000
                <= await connection.scalar(
                    text("SELECT current_setting('server_version_num')::int")
                )
                < 180000
            )
        async with primary.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            if profile is not None:
                await verify_portable_server(connection, url, profile)
            assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", child_name)
            await connection.execute(text(f'CREATE DATABASE "{child_name}"'))
            created = True
        if profile is None:
            child = create_async_engine(
                url.set(database=child_name), poolclass=NullPool
            )
        else:
            # Fresh fixture-owned engine never crosses this test's event loop.
            # Separate committed-session readback remains unchanged in the repository.
            child = create_async_engine(
                url.set(database=child_name),
                pool_size=2,
                max_overflow=0,
                pool_pre_ping=True,
                pool_recycle=1800,
            )
        async with child.begin() as connection:
            await connection.run_sync(_migrate)
            await connection.run_sync(
                lambda actual: source_scopes.verify_database_scope(
                    actual, ROOT, source_scopes.historical0052_source_scope(ROOT)
                )
            )
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0052"
            )
        yield child
    finally:
        if child is not None:
            await child.dispose()
        if created:
            assert re.fullmatch(r"ctcc_original_frame_[a-f0-9]{32}", child_name)
            async with primary.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                if profile is not None:
                    await verify_portable_server(connection, url, profile)
                assert (
                    await connection.scalar(text("SELECT current_database()")) == "ctcc"
                )
                await connection.execute(
                    text(f'DROP DATABASE "{child_name}" WITH (FORCE)')
                )
        await primary.dispose()


@asynccontextmanager
async def collector(admin, *, grant=False):
    role = "ctcc_raw_frame_" + uuid4().hex
    assert re.fullmatch(r"ctcc_raw_frame_[a-f0-9]{32}", role)
    engine = None
    created = False
    profile = portable_profile(ROOT)
    reader_password = None
    if profile is not None:
        verify_portable_url(admin.url, profile, database=admin.url.database)
    try:
        async with admin.begin() as connection:
            if profile is None:
                await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
            else:
                reader_password = await create_portable_reader_role(connection, role)
            created = True
            await connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            oid = await connection.scalar(
                text("SELECT oid::bigint FROM pg_roles WHERE rolname=:name"),
                {"name": role},
            )
            if grant:
                # Explicit NEW raw-read permission for this declared test role.
                # It is not inferred from an old metadata-reader grant.
                await connection.execute(
                    text(
                        f'GRANT EXECUTE ON FUNCTION public.{policy.FUNCTION}({policy.SIGNATURE}) TO "{role}"'
                    )
                )
        engine = create_async_engine(
            admin.url.set(username=role, password=reader_password), poolclass=NullPool
        )
        yield engine, oid
    finally:
        if engine is not None:
            await engine.dispose()
        if created:
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        f'REVOKE ALL ON FUNCTION public.{policy.FUNCTION}({policy.SIGNATURE}) FROM "{role}"'
                    )
                )
                await connection.execute(
                    text(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
                )
                await connection.execute(text(f'DROP ROLE "{role}"'))


async def settings(connection):
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await connection.execute(
        text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
    )
    await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await connection.execute(text("SET LOCAL lock_timeout='1000ms'"))
    await connection.execute(text("SET LOCAL statement_timeout='10000ms'"))


def parameters(scope, documents, revisions, lease, *, after=0, expires=None):
    return {
        "environment": scope.environment,
        "account_id": scope.account_id,
        "currency": scope.settlement_currency,
        "through": len(documents),
        "head": parent.digest(documents[-1]),
        "after": after,
        "previous": None if after == 0 else parent.digest(documents[after - 1]),
        "control": revisions[0],
        "account": revisions[1],
        "ledger": revisions[2],
        "expires": min(lease, _now() + timedelta(seconds=20))
        if expires is None
        else expires,
    }


async def test_new_raw_read_is_not_covered_by_old_metadata_permission(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    async with collector(admin) as (reader, _):
        async with admin.begin() as connection:
            name = reader.url.username
            await connection.execute(
                text(
                    f'GRANT EXECUTE ON FUNCTION public.account_history_partition_v1_read({parent.FUNCTION_SIGNATURES["account_history_partition_v1_read"]}) TO "{name}"'
                )
            )
        try:
            async with reader.begin() as connection:
                await settings(connection)
                assert (
                    await connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user,'public.demo_account_capture_events','SELECT')"
                        )
                    )
                    is False
                )
                with pytest.raises(DBAPIError):
                    await connection.scalar(
                        text(decoder.SQL),
                        parameters(scope, documents, revisions, lease),
                    )
        finally:
            async with admin.begin() as connection:
                await connection.execute(
                    text(
                        f'REVOKE ALL ON FUNCTION public.account_history_partition_v1_read({parent.FUNCTION_SIGNATURES["account_history_partition_v1_read"]}) FROM "{reader.url.username}"'
                    )
                )


async def test_explicit_execute_only_raw_read_keeps_original32_and33_and_sealed_default(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin, count=33)
    fixed_expiry = min(lease, _now() + timedelta(seconds=20))
    async with collector(admin, grant=True) as (reader, oid):
        for after in range(33):
            async with reader.begin() as connection:
                await settings(connection)
                assert (
                    await connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user,'public.demo_account_capture_events','SELECT')"
                        )
                    )
                    is False
                )
                original_catalog = await connection.scalar(text(parent.CATALOG_SQL))
                raw_catalog = await connection.scalar(text(policy.CATALOG_SQL))
                assert (
                    source_guard.verify_source_catalog(
                        raw_catalog["source_catalog"], original_catalog
                    )
                    == source_guard.PG17_NATIVE_BASELINE_SHA256
                )
                assert (
                    policy.verify_catalog(
                        raw_catalog, original_catalog, approved_collector_role_oid=oid
                    )
                    == policy.DDL_SHA256
                )
                value = await connection.scalar(
                    text(decoder.SQL),
                    parameters(
                        scope,
                        documents,
                        revisions,
                        lease,
                        after=after,
                        expires=fixed_expiry,
                    ),
                )
                assert value["next_after"] == after + 1
                assert bytes.fromhex(value["document_hex"]) == documents[after]
                assert (
                    value["account_complete"] is value["execution_authority"] is False
                )
                assert value["admission"] == "DENY"

            # Independently compare original bytes using the admin test reader;
            # that privilege is never granted to the collector role.
            async with admin.connect() as connection:
                for chain in value["chains"]:
                    rows = (
                        await connection.execute(
                            text(
                                "SELECT event_json,raw_body,packet_payload,db_recorded_at FROM public.demo_account_capture_events WHERE capture_id=:id ORDER BY sequence"
                            ),
                            {"id": chain["reference"]["capture_id"]},
                        )
                    ).all()
                    assert len(rows) == len(chain["rows"])
                    for original, exported in zip(rows, chain["rows"], strict=True):
                        assert bytes.fromhex(
                            exported["event_hex"]
                        ) == original.event_json.encode("utf-8")
                        assert (
                            None
                            if exported["raw_hex"] is None
                            else bytes.fromhex(exported["raw_hex"])
                        ) == original.raw_body
                        assert (
                            None
                            if exported["packet_hex"] is None
                            else bytes.fromhex(exported["packet_hex"])
                        ) == original.packet_payload
                        assert (
                            datetime.fromisoformat(exported["db_recorded_at"])
                            == original.db_recorded_at
                        )
        # The source transport fixture is synthetic. This loop is not native
        # account completeness, custody, checkpoint or economic acceptance.


@pytest.mark.parametrize("change", ["cursor", "head", "revision", "expired"])
async def test_wrong_cursor_head_revision_and_original_expiry_deny(
    monkeypatch, original_frame_database, change
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin, count=2)
    values = parameters(scope, documents, revisions, lease, after=1)
    if change == "cursor":
        values["previous"] = "f" * 64
    elif change == "head":
        values["head"] = "f" * 64
    elif change == "revision":
        values["control"] += 1
    else:
        values["expires"] = _now() - timedelta(seconds=1)
    async with (
        collector(admin, grant=True) as (reader, _),
        reader.begin() as connection,
    ):
        await settings(connection)
        with pytest.raises(DBAPIError):
            await connection.scalar(text(decoder.SQL), values)


async def test_real_uid_lock_wait_can_be_cancelled_without_partial_raw_result(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    uid_key_sql = parent.UID_SQL.replace("p_environment", "'demo'").replace(
        "p_account_id", ":uid"
    )
    async with collector(admin, grant=True) as (reader, _):
        async with admin.connect() as holder, holder.begin():
            await holder.execute(
                text("SELECT pg_advisory_xact_lock(" + uid_key_sql + ")"),
                {"uid": scope.account_id},
            )
            started = asyncio.Event()

            async def read():
                async with reader.begin() as connection:
                    await settings(connection)
                    started.set()
                    return await connection.scalar(
                        text(decoder.SQL),
                        parameters(scope, documents, revisions, lease),
                    )

            task = asyncio.create_task(read())
            try:
                await started.wait()
                # Confirm an actual lock waiter, rather than cancelling a task
                # that has not begun its database request.
                found = False
                for _ in range(50):
                    async with admin.connect() as observer:
                        found = bool(
                            await observer.scalar(
                                text(
                                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename=:name AND wait_event_type='Lock')"
                                ),
                                {"name": reader.url.username},
                            )
                        )
                    if found:
                        break
                    await asyncio.sleep(0.01)
                assert found
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with reader.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1


async def _stored_shape_corruption(admin, scope, documents, *, target, change):
    """Declared isolated synthetic corruption, never an original-source repair.

    Restore all actual guards before calling the reader. A poisoned B1 terminal
    is an independent trap: reaching phase1 would raise a DIFFERENT fixed code.
    Exact descriptor refusal therefore cannot be explained by decoder rejection
    or the final wire size check after hex/raw array allocation.
    """
    material = [json.loads(document) for document in documents]
    index = len(material) - 1 if target == "current" else 0
    selected = material[index]
    capture_id = selected["reference"]["capture_id"]
    if change == "extra_reference":
        selected["reference"]["unbounded_extra"] = "x" * 16384
    elif change == "long_reference":
        selected["reference"]["plan_sha256"] = "a" * 16384
    elif change == "numeric_reference":
        selected["reference"]["head_sha256"] = 1
    elif change == "object_membership":
        selected["source_membership_sha256"] = {"unbounded": "x" * 16384}
    else:
        selected["coverage_membership_sha256"] = "b" * 16384
    rebuilt = []
    async with admin.begin() as connection:
        # Deliberately use only the isolated fixture owner, then restore the
        # original enabled trigger states before the restricted RPC starts.
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_capture_events DISABLE TRIGGER account_capture_event_immutable"
            )
        )
        await connection.execute(
            text(
                "UPDATE public.demo_account_capture_events SET event_json='{}' WHERE capture_id=:capture AND sequence=(SELECT max(sequence) FROM public.demo_account_capture_events WHERE capture_id=:capture)"
            ),
            {"capture": capture_id},
        )
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_capture_events ENABLE TRIGGER account_capture_event_immutable"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_observation_batches DISABLE TRIGGER account_observation_batch_immutable"
            )
        )
        for number, document in enumerate(material, 1):
            row = (
                await connection.execute(
                    text(
                        "SELECT receipt_json FROM public.demo_account_observation_batches WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT' AND sequence=:number"
                    ),
                    {"uid": scope.account_id, "number": number},
                )
            ).one()
            receipt = json.loads(row.receipt_json)
            anchor = json.loads(document["anchor_json"])
            for witness in anchor["verified_witnesses"]:
                original = material[witness["sequence"] - 1]
                witness["event_sha256"] = parent.digest(
                    rebuilt[witness["sequence"] - 1]
                )
                witness["source_membership_sha256"] = original[
                    "source_membership_sha256"
                ]
                witness["coverage_membership_sha256"] = original[
                    "coverage_membership_sha256"
                ]
            receipt["continuation_anchor"] = anchor
            document["anchor_json"] = parent.canonical(anchor).decode("ascii")
            document["anchor_sha256"] = parent.digest(parent.canonical(anchor))
            document["receipt_sha256"] = parent.digest(parent.canonical(receipt))
            document["previous_sha256"] = (
                None if number == 1 else parent.digest(rebuilt[-1])
            )
            raw = parent.canonical(document)
            rebuilt.append(raw)
            await connection.execute(
                text(
                    "UPDATE public.demo_account_observation_batches SET document_json=:document,receipt_json=:receipt,event_sha256=:digest,previous_sha256=:previous WHERE environment='demo' AND account_id=:uid AND settlement_currency='USDT' AND sequence=:number"
                ),
                {
                    "document": raw.decode("ascii"),
                    "receipt": parent.canonical(receipt).decode("ascii"),
                    "digest": parent.digest(raw),
                    "previous": document["previous_sha256"],
                    "uid": scope.account_id,
                    "number": number,
                },
            )
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_observation_batches ENABLE TRIGGER account_observation_batch_immutable"
            )
        )
    return tuple(rebuilt)


@pytest.mark.parametrize("target", ["current", "witness"])
@pytest.mark.parametrize(
    "change",
    [
        "extra_reference",
        "long_reference",
        "numeric_reference",
        "object_membership",
        "long_membership",
    ],
)
async def test_stored_copied_metadata_refuses_before_any_source_export(
    monkeypatch, original_frame_database, target, change
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin, count=2)
    if target == "witness":
        assert any(
            witness["sequence"] == 1
            for witness in json.loads(json.loads(documents[-1])["anchor_json"])[
                "verified_witnesses"
            ]
        )
    changed = await _stored_shape_corruption(
        admin, scope, documents, target=target, change=change
    )
    expected = (
        "original_frame_source_reference"
        if "reference" in change
        else "original_frame_witness_shape"
        if target == "witness"
        else "original_frame_source_membership"
    )
    async with (
        collector(admin, grant=True) as (reader, _),
        reader.begin() as connection,
    ):
        await settings(connection)
        with pytest.raises(DBAPIError, match=expected):
            await connection.scalar(
                text(decoder.SQL),
                parameters(scope, changed, revisions, lease, after=1),
            )


@pytest.mark.parametrize(
    "change",
    [
        "insert_disabled",
        "immutable_dropped",
        "truncate_disabled",
        "function_body",
        "constraint_dropped",
        "ri_disabled",
    ],
)
async def test_actual_original_source_guard_drift_refuses_raw_rpc(
    monkeypatch, original_frame_database, change
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    async with admin.begin() as connection:
        if change == "insert_disabled":
            statement = "ALTER TABLE public.demo_account_capture_events DISABLE TRIGGER account_capture_event_guard"
        elif change == "immutable_dropped":
            statement = "DROP TRIGGER account_observation_batch_immutable ON public.demo_account_observation_batches"
        elif change == "truncate_disabled":
            statement = "ALTER TABLE public.demo_account_capture_events DISABLE TRIGGER account_capture_event_no_truncate"
        elif change == "function_body":
            statement = "CREATE OR REPLACE FUNCTION public.account_capture_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"
        elif change == "constraint_dropped":
            statement = "ALTER TABLE public.demo_account_capture_events DROP CONSTRAINT ck_demo_account_capture_events_packet_bound"
        else:
            trigger_name = await connection.scalar(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_constraint c ON c.oid=t.tgconstraint WHERE c.conname='fk_account_observation_source' AND t.tgrelid='public.demo_account_observation_batches'::regclass ORDER BY t.tgname LIMIT 1"
                )
            )
            assert re.fullmatch(r"RI_ConstraintTrigger_[ac]_[0-9]+", trigger_name)
            statement = (
                'ALTER TABLE public.demo_account_observation_batches DISABLE TRIGGER "'
                + trigger_name
                + '"'
            )
        await connection.execute(text(statement))
    async with (
        collector(admin, grant=True) as (reader, _),
        reader.begin() as connection,
    ):
        await settings(connection)
        with pytest.raises(DBAPIError, match="original_frame_source_schema_drift"):
            await connection.scalar(
                text(decoder.SQL), parameters(scope, documents, revisions, lease)
            )


async def test_actual_check_expression_change_is_rejected_by_sealed_default(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    await _sources(monkeypatch, admin)
    async with admin.begin() as connection:
        previous = await connection.scalar(text(parent.CATALOG_SQL))
        before = await connection.scalar(text(source_guard.CATALOG_SQL))
        observed_before = source_guard.verify_source_catalog(before, previous)
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_capture_events DROP CONSTRAINT ck_demo_account_capture_events_packet_bound"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE public.demo_account_capture_events ADD CONSTRAINT ck_demo_account_capture_events_packet_bound CHECK (true)"
            )
        )
        changed = await connection.scalar(text(source_guard.CATALOG_SQL))
        observed_after = parent.digest(parent.canonical(changed["definitions"]))
        assert observed_before == source_guard.PG17_NATIVE_BASELINE_SHA256
        assert observed_after != observed_before
        with pytest.raises(
            parent.AccountHistoryPartitionError,
            match="^original_frame_source_deparser_drift$",
        ):
            source_guard.verify_source_catalog(changed, previous)


async def test_other_uid_source_writer_relation_lock_is_rejected_without_waiting(
    monkeypatch, original_frame_database
):
    admin = original_frame_database
    scope, documents, revisions, lease = await _sources(monkeypatch, admin)
    async with (
        collector(admin, grant=True) as (reader, _),
        admin.connect() as holder,
        holder.begin(),
    ):
        # No UID lock: models another UID's existing B1 append transaction.
        await holder.execute(
            text("LOCK TABLE public.demo_account_capture_events IN ROW EXCLUSIVE MODE")
        )
        async with reader.begin() as connection:
            await settings(connection)
            with pytest.raises(DBAPIError):
                await connection.scalar(
                    text(decoder.SQL),
                    parameters(scope, documents, revisions, lease),
                )


async def test_sealed_default_catalog_accepts_only_explicit_frame_role_without_direct_read(
    original_frame_database,
):
    """Native catalogs, fresh PG and real restricted login; no account fixtures."""
    admin = original_frame_database
    async with collector(admin, grant=True) as (reader, oid):
        async with reader.begin() as connection:
            await settings(connection)
            previous = await connection.scalar(text(parent.CATALOG_SQL))
            actual = await connection.scalar(text(policy.CATALOG_SQL))
            assert (
                source_guard.verify_source_catalog(actual["source_catalog"], previous)
                == source_guard.PG17_NATIVE_BASELINE_SHA256
            )
            assert (
                policy.verify_catalog(actual, previous, approved_collector_role_oid=oid)
                == policy.DDL_SHA256
            )
            assert actual["source_table_access"] is False
            assert actual["schema_usage"] is True
            assert actual["functions"][0]["can_execute"] is True
            for table in (
                *source_guard.TABLES,
                *parent.TABLES,
                "qualification_account_scopes",
                "demo_account_controls",
            ):
                assert (
                    await connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user,:relation,"
                            "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')"
                            " OR has_any_column_privilege(current_user,:relation,"
                            "'SELECT,INSERT,UPDATE,REFERENCES')"
                        ),
                        {"relation": "public." + table},
                    )
                    is False
                )
            signature = parent.FUNCTION_SIGNATURES["account_history_partition_v1_read"]
            assert (
                await connection.scalar(
                    text(
                        "SELECT has_function_privilege(current_user,:function,'EXECUTE')"
                    ),
                    {
                        "function": "public.account_history_partition_v1_read("
                        + signature
                        + ")"
                    },
                )
                is False
            )
        # Permission is tested against the actual connection, not just its DTO.
        async with reader.begin() as connection:
            await settings(connection)
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text(
                        "SELECT event_json FROM public.demo_account_capture_events LIMIT 1"
                    )
                )


@pytest.mark.parametrize("target", ["source", "frame"])
async def test_actual_native_definition_change_denies_sealed_default(
    original_frame_database, target
):
    """COST changes native deparse without changing fixed prosrc or structure."""
    admin = original_frame_database
    async with collector(admin, grant=True) as (reader, oid):
        async with reader.begin() as connection:
            await settings(connection)
            previous = await connection.scalar(text(parent.CATALOG_SQL))
            before = await connection.scalar(text(policy.CATALOG_SQL))
            assert (
                source_guard.verify_source_catalog(before["source_catalog"], previous)
                == source_guard.PG17_NATIVE_BASELINE_SHA256
            )
            assert (
                policy.verify_catalog(before, previous, approved_collector_role_oid=oid)
                == policy.DDL_SHA256
            )
        async with admin.begin() as connection:
            if target == "source":
                statement = (
                    "ALTER FUNCTION public.account_capture_event_guard() COST 101"
                )
            else:
                statement = f"ALTER FUNCTION public.{policy.FUNCTION}({policy.SIGNATURE}) COST 101"
            await connection.execute(text(statement))
        async with reader.begin() as connection:
            await settings(connection)
            previous = await connection.scalar(text(parent.CATALOG_SQL))
            changed = await connection.scalar(text(policy.CATALOG_SQL))
            if target == "source":
                assert (
                    changed["source_catalog"]["structure"]
                    == (before["source_catalog"]["structure"])
                )
                assert (
                    changed["source_catalog"]["definitions"]
                    != (before["source_catalog"]["definitions"])
                )
                with pytest.raises(
                    parent.AccountHistoryPartitionError,
                    match="^original_frame_source_deparser_drift$",
                ):
                    source_guard.verify_source_catalog(
                        changed["source_catalog"], previous
                    )
                expected = "^original_frame_source_deparser_drift$"
            else:
                assert changed["functions"][0]["source"] == policy.BODY
                assert (
                    changed["functions"][0]["definition"]
                    != (before["functions"][0]["definition"])
                )
                assert (
                    source_guard.verify_source_catalog(
                        changed["source_catalog"], previous
                    )
                    == source_guard.PG17_NATIVE_BASELINE_SHA256
                )
                expected = "^original_frame_native_baseline_pending$"
            with pytest.raises(parent.AccountHistoryPartitionError, match=expected):
                policy.verify_catalog(
                    changed, previous, approved_collector_role_oid=oid
                )
