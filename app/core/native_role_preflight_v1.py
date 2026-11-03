"""Default-off N95 draft: actual PG17 restricted-role proof before Demo recovery.

No owner connection, provider call, order path, or database write is made here.
The frame role's transaction is READ WRITE because the existing sealed
current0055 pair verifier requires that observed setting; this code issues
only SET and SELECT and returns metadata, never a frame or account row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.database.repositories import (
    account_history_original_native_read_current0053_v1 as original,
)
from app.database.repositories import (
    collector_current_0055_multirole_catalog_v1 as catalog,
)
from app.database.repositories import public_runtime_bundle_custody as custody
from app.demo_automation import native_server_selectors_v2 as selectors
from app.domain.source_primitives import decode
from scripts import current0055_native_account_source_acl_v1 as account_acl
from scripts import current0055_native_lifecycle_acl_v1 as lifecycle_acl
from scripts import current0055_runtime_grants as core_acl


class NativeRolePreflightError(RuntimeError):
    """Fixed non-sensitive denial code."""


_IDENTITY_SQL = text("""
SELECT current_user::text, session_user::text,
       current_setting('role')::text,
       (SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=current_user),
       (SELECT oid::bigint FROM pg_catalog.pg_database
         WHERE datname=pg_catalog.current_database()),
       pg_catalog.pg_postmaster_start_time(),
       current_setting('server_version_num')::int,
       pg_catalog.inet_server_addr()::text,
       pg_catalog.inet_server_port()
""")
_OWNER_OID_SQL = text("""
SELECT c.relowner::bigint FROM pg_catalog.pg_class c
WHERE c.oid=pg_catalog.to_regclass('public.demo_automation_state')
""")
_SETTINGS = (
    "SET LOCAL search_path TO pg_catalog, public, pg_temp",
    "SET LOCAL TIME ZONE 'UTC'",
    "SET LOCAL lock_timeout='1000ms'",
    "SET LOCAL statement_timeout='10000ms'",
)


def _need(value: bool) -> None:
    if value is not True:
        raise NativeRolePreflightError("native_role_preflight_denied")


async def _identity(session, expected_name: str):
    row = tuple((await session.execute(_IDENTITY_SQL)).one())
    _need(
        len(row) == 9
        and row[:3] == (expected_name, expected_name, "none")
        and type(row[3]) is int
        and row[3] > 0
        and type(row[4]) is int
        and row[4] > 0
        and type(row[5]) is datetime
        and row[5].tzinfo is not None
        and type(row[6]) is int
        and 170000 <= row[6] < 180000
        and type(row[7]) is str
        and bool(row[7])
        and type(row[8]) is int
        and 1 <= row[8] <= 65535
    )
    return row


async def _session_settings(session, *, frame: bool) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
    await session.execute(
        text("SET TRANSACTION READ WRITE" if frame else "SET TRANSACTION READ ONLY")
    )
    for statement in _SETTINGS:
        await session.execute(text(statement))


def _same_server(*rows) -> None:
    _need(len(rows) == 4)
    _need(len({row[3] for row in rows[:3]}) == 3)
    # Database OID, postmaster start, major version, address and port must
    # agree across roles and a final fresh writer read. This prevents a DNS or
    # backend switch during the proof from silently mixing databases.
    _need(len({row[4:] for row in rows}) == 1)
    _need(rows[0] == rows[3])


async def _exact_existing_acl(connection, names, writer_oid: int) -> None:
    """Observe the full 28+11+3 effective ACL; issue no GRANT or REVOKE."""
    owner = await connection.scalar(_OWNER_OID_SQL)
    _need(type(owner) is int and owner > 0 and owner != writer_oid)
    roles = await core_acl._roles(connection, names, owner)
    _need(roles[names[0]] == writer_oid)
    _need(
        tuple(
            (
                await connection.execute(core_acl._OWNER_POLICY_SQL, {"owner": owner})
            ).one()
        )
        == (True, True)
    )
    core, account = account_acl._plans(*names)
    previous, lifecycle = lifecycle_acl._plans(*names)
    _need(
        len(core) == 28
        and len(account) == 11
        and len(lifecycle) == 3
        and previous == core + account
    )
    combined = previous + lifecycle
    expected = core_acl._expected_acl(combined, roles, owner)
    targets = await core_acl._target_oids(connection, expected, owner, granted=True)
    await core_acl._access(connection, combined, roles, granted=True, targets=targets)
    account_expected = core_acl._expected_acl(account, roles, owner)
    _need(
        await account_acl._delta_relations(connection, account_expected, owner)
        == {key: targets[key] for key in account_expected}
    )
    lifecycle_tables = {key: targets[key] for key in lifecycle_acl._TABLES}
    _need(
        await account_acl._delta_relations(connection, lifecycle_tables, owner)
        == lifecycle_tables
    )
    await lifecycle_acl._guard(connection, lifecycle_tables, owner)


async def verify_selected_native_roles_v1(selected) -> dict:
    """Verify actual writer seal, custody deployment, and raw/writer pair."""
    _need(type(selected) is selectors.ConfiguredNativeDemoFactoriesV2)
    writer = selected.writer_factory
    custody_factory = selected.custody_factory
    frame_factory = selected.frame_reader_factory
    names = (
        writer.kw["bind"].url.username,
        custody_factory.kw["bind"].url.username,
        frame_factory.kw["bind"].url.username,
    )
    _need(
        len(set(names)) == 3
        and selectors._database_route(writer.kw["bind"].url)
        == selectors._database_route(custody_factory.kw["bind"].url)
        == selectors._database_route(frame_factory.kw["bind"].url)
    )

    async with writer() as session, session.begin():
        await _session_settings(session, frame=False)
        writer_seal = await catalog.verify_actual_control_catalog_v2(session)
        writer_identity = await _identity(session, names[0])
        await _exact_existing_acl(await session.connection(), names, writer_identity[3])
    _need(
        writer_seal["database_head"] == "0055"
        and writer_seal["role_oid"] == writer_identity[3]
        and writer_seal["native_baseline_admission"] is True
        and writer_seal["account_complete"] is False
        and writer_seal["execution_authority"] is False
    )

    # The repository checks the real restricted custody role and sealed
    # native function/table catalog in its own independent READ ONLY session.
    await custody.RuntimeBundleCustodyRepository(custody_factory).verify_deployment()
    async with custody_factory() as session, session.begin():
        await _session_settings(session, frame=False)
        custody_identity = await _identity(session, names[1])

    async with frame_factory() as session, session.begin():
        await _session_settings(session, frame=True)
        frame_identity = await _identity(session, names[2])
        fence = original._Fence(datetime.now(UTC) + timedelta(seconds=30))
        wire = await catalog.verify_actual_raw_history_catalog_v1(
            session, fence, frame_identity[3], writer, frame_factory
        )
        pair = decode(wire, maximum=4 * 1024 * 1024)["current0055"]
    profile = pair["profile"]
    _need(
        profile["database_head"] == "0055"
        and profile["actual_separate_roles_verified"] is True
        and profile["native_definitions_sha256"]
        == writer_seal["native_definitions_sha256"]
        and profile["account_complete"] is False
        and profile["execution_authority"] is False
        and pair["raw"]["role"]["oid"] == frame_identity[3]
        and pair["writer"]["role"]["oid"] == writer_identity[3]
    )

    async with writer() as session, session.begin():
        await _session_settings(session, frame=False)
        writer_after = await _identity(session, names[0])
    _same_server(writer_identity, custody_identity, frame_identity, writer_after)
    return {
        "schema": "ctcc.native_only.restricted_role_preflight.v1",
        "database_head": "0055",
        "writer_seal_verified": True,
        "custody_deployment_verified": True,
        "frame_pair_verified": True,
        "server_identity_stable": True,
        "account_complete": False,
        "execution_authority": False,
    }


async def preflight_configured_native_roles_v1(expected_writer_factory) -> dict:
    """Select server-private DSNs and always dispose the two extra engines."""
    selected = None
    result = None
    failure: BaseException | None = None
    try:
        selected = selectors.select_configured_native_demo_factories_v2()
        _need(selected.writer_factory is expected_writer_factory)
        result = await verify_selected_native_roles_v1(selected)
    except asyncio.CancelledError as error:
        failure = error
    except NativeRolePreflightError as error:
        failure = error
    except Exception:  # noqa: BLE001 - never expose DSN, SQL or catalog text
        failure = NativeRolePreflightError("native_role_preflight_unavailable")
    finally:
        if selected is not None:
            for engine in (selected.custody_engine, selected.frame_reader_engine):
                try:
                    await engine.dispose()
                except BaseException as error:  # noqa: BLE001 - preserve first failure
                    if failure is None:
                        failure = (
                            error
                            if isinstance(error, asyncio.CancelledError)
                            else (
                                NativeRolePreflightError(
                                    "native_role_preflight_disposal_failed"
                                )
                            )
                        )
    if failure is not None:
        raise failure
    _need(result is not None)
    return result
