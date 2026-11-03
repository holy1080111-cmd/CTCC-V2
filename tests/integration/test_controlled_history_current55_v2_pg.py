"""Actual55 SQL/ACL/migration mechanics on a fresh owned PG17 database.

Control/scope parents below are DECLARED SYNTHETIC. They are not native
credentials, a source owner, a complete account or a write permit. Valid Armed
fences must reach the original missing-head guard; no empty frame is accepted.
Only Root's later genuine full capture can supply native admission/seals.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.repositories.qualification_ledger import QualificationLedgerRepository
from app.domain import collector_current_0055_catalog_v1 as profile
from app.domain import controlled_history_current55_schema_v2 as schema
from app.domain import native_submission_reporting_schema_v55 as submission
from app.trade_qualification.demo_control import (
    ControlPins,
    ControlScope,
    acquire,
    advance,
)
from app.trade_qualification.reservations import LedgerScope
from tests.controlled_current55_fixtures import (
    controlled_current55_database as controlled_current55_database,  # noqa: PLC0414 -- pytest registers this exact fixture reexport.
)
from tests.controlled_current55_fixtures import migrate_current55 as _migrate
from tests.controlled_writer_coverage_fixtures import _store_control_fixture
from tests.integration.test_account_history_original_frame_repository_v1 import (
    collector,
    settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _metadata(engine):
    async with engine.connect() as connection:
        return await connection.scalar(text(schema.CATALOG_SQL))


async def _armed_parent(engine):
    uid = "5500" + str(uuid4().int % 10**18)
    scope = LedgerScope(environment="demo", account_id=uid, settlement_currency="USDT")
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    checkpoint = await QualificationLedgerRepository(
        sessions, clock=lambda: datetime.now(UTC)
    ).initialize_capture_scope(scope)
    assert checkpoint.state.account_revision == checkpoint.state.ledger_revision == 0
    async with engine.begin() as connection:
        now = datetime.now(UTC)
        owner = "a" * 64
        state = acquire(
            None,
            scope=ControlScope("demo", uid),
            owner_sha256=owner,
            pins=ControlPins("1" * 64, "2" * 64, "3" * 64),
            now=now,
        )
        prior = await _store_control_fixture(
            connection,
            state,
            command_id=uuid4().hex * 2,
            action="acquire",
            previous_sha256=None,
        )
        arm_command = uuid4().hex * 2
        state = advance(
            state,
            owner_sha256=owner,
            owner_epoch=1,
            expected_revision=1,
            action="arm_requested",
            command_id=arm_command,
            now=now + timedelta(microseconds=1),
            arm_expires_at=now + timedelta(seconds=25),
        )
        await _store_control_fixture(
            connection,
            state,
            command_id=arm_command,
            action="arm_requested",
            previous_sha256=prior,
        )
    return scope, state


def _append_parameters(scope, control):
    return {
        "environment": "demo",
        "account_id": scope.account_id,
        "currency": "USDT",
        "head": "b" * 64,
        "previous": None,
        "registration": "{}",
        "checkpoint": None,
        "control_revision": control.revision,
        "account_revision": 0,
        "ledger_revision": 0,
        "expires": min(
            control.arm_expires_at, datetime.now(UTC) + timedelta(seconds=15)
        ),
    }


async def _grant(engine, role, name, *, revoke=False):
    # All names come from this test's genuine collector and fixed RPC tuple.
    assert name in schema.FUNCTIONS
    signature = schema.SIGNATURES[name]
    action = "REVOKE ALL" if revoke else "GRANT EXECUTE"
    direction = "FROM" if revoke else "TO"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f'{action} ON FUNCTION public.{name}({signature}) {direction} "{role}"'
            )
        )


async def test_fresh55_full_unsealed_catalog_and_no_grants(
    controlled_current55_database,
):
    engine = controlled_current55_database
    metadata = await _metadata(engine)
    owner = metadata["actor"]
    observed = schema.observe_catalog(
        metadata, owner=owner, actor=owner, owner_mode=True
    )
    assert observed["raw_role_oid"] is None and observed["writer_role_oid"] is None
    assert observed["account_complete"] is observed["execution_authority"] is False
    async with engine.connect() as connection, connection.begin():
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(
            text("SET LOCAL search_path TO pg_catalog, public, pg_temp")
        )
        await connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        actual = await connection.scalar(text(profile.OWNER_CATALOG_SQL))
        result = profile.observe_actual_owner_catalog(actual)
        assert (
            result["database_head"] == "0055"
            and result["native_baseline_admission"] is False
        )
        assert result["native_submission_body_sha256"] == submission.CURRENT_BODY_SHA256


async def test_0055_down_reup_preserves_original_functions(
    controlled_current55_database,
):
    engine = controlled_current55_database
    before = await _metadata(engine)
    original = {
        row["name"]: row
        for row in before["functions"]
        if row["name"] in schema.ORIGINALS
    }
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0054"
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='public' AND p.proname IN(:one,:two,:three)"
                ),
                dict(zip(("one", "two", "three"), schema.FUNCTIONS, strict=True)),
            )
            == 0
        )
        assert (
            await connection.scalar(
                text(
                    "SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc "
                    "WHERE oid='public.qualification_submission_binding()'::regprocedure"
                )
            )
            == submission.ORIGINAL_BODY_SHA256
        )
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0055"))
    after = await _metadata(engine)
    assert {
        row["name"]: row
        for row in after["functions"]
        if row["name"] in schema.ORIGINALS
    } == original
    for name in schema.FUNCTIONS:
        assert next(row for row in after["functions"] if row["name"] == name)[
            "source"
        ] == next(body for candidate, _, _, body in schema.SPECS if candidate == name)


async def test_new_rpc_grants_required_and_old_stopped_append_stays_denied(
    controlled_current55_database,
):
    engine = controlled_current55_database
    scope, control = await _armed_parent(engine)
    async with collector(engine) as (reader, _):
        params = _append_parameters(scope, control)
        with pytest.raises(DBAPIError, match="permission denied"):
            async with reader.begin() as connection:
                await settings(connection)
                await connection.scalar(text(schema.APPEND_SQL), params)
        await _grant(engine, reader.url.username, schema.APPEND)
        try:
            # Passing the actual new fence reaches the retained head check.
            with pytest.raises(
                DBAPIError, match="account_history_partition_head_changed"
            ):
                async with reader.begin() as connection:
                    await settings(connection)
                    await connection.scalar(text(schema.APPEND_SQL), params)
            old_sql = schema.APPEND_SQL.replace(
                "public." + schema.APPEND + "(",
                "public." + schema.ORIGINALS[1] + "(",
                1,
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f'GRANT EXECUTE ON FUNCTION public.{schema.ORIGINALS[1]}({schema.ORIGINAL_SIGNATURES[schema.ORIGINALS[1]]}) TO "{reader.url.username}"'
                    )
                )
            try:
                with pytest.raises(
                    DBAPIError, match="account_history_partition_fence_changed"
                ):
                    async with reader.begin() as connection:
                        await settings(connection)
                        await connection.scalar(text(old_sql), params)
            finally:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            f'REVOKE ALL ON FUNCTION public.{schema.ORIGINALS[1]}({schema.ORIGINAL_SIGNATURES[schema.ORIGINALS[1]]}) FROM "{reader.url.username}"'
                        )
                    )
        finally:
            await _grant(engine, reader.url.username, schema.APPEND, revoke=True)


async def test_new_frame_reaches_retained_head_guard_while_old_frame_stays_stopped(
    controlled_current55_database,
):
    engine = controlled_current55_database
    scope, control = await _armed_parent(engine)
    params = {
        "environment": "demo",
        "account_id": scope.account_id,
        "currency": "USDT",
        "through": 1,
        "head": "b" * 64,
        "after": 0,
        "previous": None,
        "control": control.revision,
        "account": 0,
        "ledger": 0,
        "expires": datetime.now(UTC) + timedelta(seconds=10),
    }
    async with collector(engine, grant=True) as (reader, _):
        await _grant(engine, reader.url.username, schema.FRAME)
        try:
            with pytest.raises(DBAPIError, match="original_frame_head_changed"):
                async with reader.begin() as connection:
                    await settings(connection)
                    await connection.scalar(text(schema.FRAME_SQL), params)
            old_sql = schema.FRAME_SQL.replace(
                "public." + schema.FRAME + "(", "public." + schema.ORIGINALS[2] + "(", 1
            )
            with pytest.raises(
                DBAPIError, match="account_history_partition_fence_changed"
            ):
                async with reader.begin() as connection:
                    await settings(connection)
                    await connection.scalar(text(old_sql), params)
        finally:
            await _grant(engine, reader.url.username, schema.FRAME, revoke=True)


async def test_upgrade_denies_changed_original_history_body_before_install(
    controlled_current55_database,
):
    engine = controlled_current55_database
    old_name = schema.ORIGINALS[0]
    original = schema.partition.FUNCTION_SQL[old_name].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    changed = original.replace(
        schema.OLD_LOCK_BODY, schema.OLD_LOCK_BODY + "\n-- DECLARED COMPONENT DRIFT", 1
    )
    assert changed != original
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: _migrate(sync, "0054", down=True))
    try:
        async with engine.begin() as connection:
            await connection.execute(text(changed))
        with pytest.raises(RuntimeError, match="history_function_drift_or_grants"):
            async with engine.begin() as connection:
                await connection.run_sync(lambda sync: _migrate(sync, "0055"))
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0054"
            )
            assert (
                await connection.scalar(
                    text("SELECT to_regprocedure(:signature) IS NULL"),
                    {
                        "signature": "public."
                        + schema.APPEND
                        + "("
                        + schema.SIGNATURES[schema.APPEND]
                        + ")"
                    },
                )
                is True
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(original))
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: _migrate(sync, "0055"))


@pytest.mark.parametrize(
    "fault",
    (
        "null_control",
        "control",
        "account",
        "ledger",
        "expired",
        "beyond_arm",
        "infinite",
        "stopped",
    ),
)
async def test_new_append_rejects_original_fence_faults(
    controlled_current55_database, fault
):
    engine = controlled_current55_database
    scope, control = await _armed_parent(engine)
    params = _append_parameters(scope, control)
    if fault == "null_control":
        params["control_revision"] = None
    elif fault == "control":
        params["control_revision"] += 1
    elif fault == "account":
        params["account_revision"] += 1
    elif fault == "ledger":
        params["ledger_revision"] += 1
    elif fault == "expired":
        params["expires"] = datetime.now(UTC) - timedelta(seconds=1)
    elif fault == "beyond_arm":
        params["expires"] = control.arm_expires_at + timedelta(seconds=1)
    elif fault == "infinite":
        params["expires"] = "infinity"
    elif fault == "stopped":
        async with engine.begin() as connection:
            stopped = advance(
                control,
                owner_sha256=control.owner_sha256,
                owner_epoch=control.owner_epoch,
                expected_revision=control.revision,
                action="estop",
                command_id=uuid4().hex * 2,
                now=datetime.now(UTC),
            )
            previous = await connection.scalar(
                text(
                    "SELECT event_sha256 FROM demo_control_journal WHERE environment='demo' AND account_id=:uid AND control_revision=:revision"
                ),
                {"uid": scope.account_id, "revision": control.revision},
            )
            await _store_control_fixture(
                connection,
                stopped,
                command_id=uuid4().hex * 2,
                action="estop",
                previous_sha256=previous,
            )
            params["control_revision"] = stopped.revision
    async with collector(engine) as (reader, _):
        await _grant(engine, reader.url.username, schema.APPEND)
        try:
            with pytest.raises(
                DBAPIError, match="current55_controlled_history_armed_fence_changed"
            ):
                async with reader.begin() as connection:
                    await settings(connection)
                    statement = schema.APPEND_SQL
                    values = dict(params)
                    if fault == "infinite":
                        statement = statement.replace(
                            ":expires", "'infinity'::timestamptz"
                        )
                        values.pop("expires")
                    await connection.scalar(text(statement), values)
        finally:
            await _grant(engine, reader.url.username, schema.APPEND, revoke=True)


async def test_downgrade_denies_unrevoked_new_rpc_grant(controlled_current55_database):
    engine = controlled_current55_database
    async with collector(engine) as (reader, _):
        await _grant(engine, reader.url.username, schema.APPEND)
        try:
            with pytest.raises(RuntimeError, match="history_function_drift_or_grants"):
                async with engine.begin() as connection:
                    await connection.run_sync(
                        lambda sync: _migrate(sync, "0054", down=True)
                    )
            assert (await _metadata(engine))["head"] == "0055"
        finally:
            await _grant(engine, reader.url.username, schema.APPEND, revoke=True)
