"""Actual source54 raw-prefix catalog, with a separate captured writer role.

This never substitutes an actor or source head. Original strict partition and
frame admission remains enabled. The versioned54 graph requires its own real
native seal and returns an inert read digest, never an account or trade owner.
"""

import asyncio
from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories import public_runtime_bundle_custody as custody
from app.domain import account_history_original_frame_v1 as frame_policy
from app.domain import account_history_partition_catalog_v1 as parent_policy
from app.domain import collector_current_0054_catalog_v1 as policy
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_history_source_v1 as history

FAILED_CLOSE_SECONDS = 5.0


class Current0054MultiRoleError(ValueError):
    pass


def _deny():
    raise Current0054MultiRoleError(
        "current0054_multirole_catalog_unavailable"
    ) from None


def _data_pin(value, depth=0):
    # Same bounded plain-data/native identity semantics as the captured V4
    # factory route check. Values and credentials never enter a receipt.
    if depth > 24:
        _deny()
    kind = type(value)
    if value is None or kind in (str, bytes, int, bool, float):
        return kind, value
    if kind in (tuple, list):
        return kind, tuple(_data_pin(part, depth + 1) for part in value)
    if kind in (dict, MappingProxyType):
        return kind, tuple(
            (_data_pin(key, depth + 1), _data_pin(part, depth + 1))
            for key, part in value.items()
        )
    if kind in (set, frozenset):
        return kind, frozenset(_data_pin(part, depth + 1) for part in value)
    return "exact_identity", id(value)


def _factory_route(factory):
    pin = history._factory_pin(factory)
    parameters = custody._closure(pin[1].sync_engine.pool._creator).get("cparams")
    allowed = {
        "host",
        "port",
        "database",
        "user",
        "password",
        "ssl",
        "timeout",
        "command_timeout",
        "server_settings",
        "statement_cache_size",
        "prepared_statement_cache_size",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
    }
    if (
        type(parameters) not in (dict, custody.immutabledict)
        or not set(parameters) <= allowed
        or not {"host", "port", "database", "user"} <= set(parameters)
        or type(parameters["host"]) is not str
        or not 1 <= len(parameters["host"]) <= 253
        or any(
            c
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for c in parameters["host"]
        )
        or type(parameters["port"]) is not int
        or not 1 <= parameters["port"] <= 65535
        or type(parameters["database"]) is not str
        or not 1 <= len(parameters["database"]) <= 63
        or any(
            ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in parameters["database"]
        )
        or type(parameters["user"]) is not str
        or not parameters["user"]
    ):
        _deny()
    route = {
        name: parameters[name]
        for name in sorted(parameters)
        if name not in {"user", "password"}
    }
    return (
        pin,
        (type(parameters), _data_pin(dict(parameters))),
        _data_pin(route),
        parameters["user"],
    )


def _role(row, expected_name, *, expected_oid=None):
    if type(row) is not dict or set(row) != {
        "oid",
        "name",
        "current_user",
        "session_user",
        "setting",
        "login",
        "superuser",
        "createdb",
        "createrole",
        "replication",
        "bypassrls",
        "membership",
        "create",
    }:
        _deny()
    if (
        type(row["oid"]) is not int
        or not 0 < row["oid"] < 2**32
        or (expected_oid is not None and row["oid"] != expected_oid)
        or row["name"] != expected_name
        or row["current_user"] != expected_name
        or row["session_user"] != expected_name
        or row["setting"] != "none"
        or row["login"] is not True
        or any(
            row[key] is not False
            for key in (
                "superuser",
                "createdb",
                "createrole",
                "replication",
                "bypassrls",
                "membership",
                "create",
            )
        )
    ):
        _deny()


async def _close_owned(session):
    # Reviewed cap16 cancellation ownership pattern, retained with a fixed
    # five-second close cap. No close retry or background task escapes.
    async def close_once():
        async with asyncio.timeout(FAILED_CLOSE_SECONDS):
            await session.close()

    task = asyncio.create_task(close_once())
    cancelled = bool(asyncio.current_task().cancelling())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 -- redacted fixed error below.
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError() from None
    try:
        task.result()
    except asyncio.CancelledError:
        raise asyncio.CancelledError() from None
    except Exception:  # noqa: BLE001 -- no SQL/credential details.
        _deny()


def _verify_pair(
    raw, writer, *, raw_name, writer_name, approved_raw_role_oid, parent_owner_oid
):
    for value in (raw, writer):
        if (
            type(value) is not dict
            or set(value)
            != {
                "schema",
                "issued",
                "lineage",
                "lineage_base",
                "role",
                "table_guards",
                "defaults",
                "acl",
                "custody_internal",
                "scope_parent",
            }
            or value["schema"] != policy.SCHEMA
        ):
            _deny()
    _role(raw["role"], raw_name, expected_oid=approved_raw_role_oid)
    _role(writer["role"], writer_name)
    if (
        raw_name == writer_name
        or raw["role"]["oid"] == writer["role"]["oid"]
        or type(parent_owner_oid) is not int
        or parent_owner_oid in (raw["role"]["oid"], writer["role"]["oid"])
    ):
        _deny()
    qualified_writer = policy.verify_actual_readonly_writer_catalog(writer)
    for component, parents, schema_name in (
        (
            "issued",
            policy.PARENTS,
            "ctcc.collector_current0054.actual_issued_catalog.v1",
        ),
        (
            "lineage",
            policy.old53.PARENTS,
            "ctcc.collector_current0053.actual_owner_catalog.v1",
        ),
    ):
        left, right = raw[component], writer[component]
        if (
            type(left) is not dict
            or set(left) != policy.BASE_KEYS | policy.old53.EXTRA_KEYS
            or left["owner"] != parent_owner_oid
            or left["actor"] != raw["role"]["oid"]
            or right["owner"] != parent_owner_oid
            or right["actor"] != writer["role"]["oid"]
            or left["table_ok"] is not True
            or left["bad_table_acl"] is not True
            or right["bad_table_acl"] is not False
        ):
            _deny()
        for key in policy.BASE_KEYS - {"actor", "bad_table_acl"}:
            if left[key] != right[key]:
                _deny()
        policy._context(
            left,
            role=raw["role"],
            read_only="off",
            parents=parents,
            schema_name=schema_name,
        )
        for key in (
            "table_oid",
            "parent_facts",
            "native_definitions",
            "ri_triggers",
            "custody_incoming",
        ):
            if left[key] != right[key]:
                _deny()
        if left["server"]["database"] != right["server"]["database"]:
            _deny()
    if (
        type(raw["lineage_base"]) is not dict
        or set(raw["lineage_base"]) != policy.BASE_KEYS
        or raw["lineage_base"]["actor"] != raw["role"]["oid"]
        or raw["lineage_base"]["bad_table_acl"] is not True
    ):
        _deny()
    for key in policy.BASE_KEYS - {"actor", "bad_table_acl"}:
        if raw["lineage_base"][key] != writer["lineage_base"][key]:
            _deny()
    for key in ("table_guards", "defaults", "acl", "custody_internal", "scope_parent"):
        if raw[key] != writer[key]:
            _deny()
    return {
        "schema": "ctcc.collector_current0054.actual_multirole_catalog.v1",
        "database_head": "0054",
        "actual_separate_roles_verified": True,
        "native_definitions_sha256": qualified_writer["native_definitions_sha256"],
        "account_complete": False,
        "execution_authority": False,
    }


async def _current54_pair(
    raw_session,
    fence,
    *,
    approved_raw_role_oid,
    lineage_session_factory,
    raw_session_factory,
    parent_owner_oid,
):
    initial_writer = _factory_route(lineage_session_factory)
    initial_raw = _factory_route(raw_session_factory)
    if (
        initial_writer[2] != initial_raw[2]
        or initial_writer[3] == initial_raw[3]
        or raw_session.get_bind() is not initial_raw[0][1].sync_engine
    ):
        _deny()

    def check():
        fence.check()
        for factory, expected in (
            (lineage_session_factory, initial_writer),
            (raw_session_factory, initial_raw),
        ):
            if _factory_route(factory) != expected:
                _deny()
        if raw_session.get_bind() is not initial_raw[0][1].sync_engine:
            _deny()

    writer = None
    try:
        check()
        writer = lineage_session_factory()
        if (
            type(writer) is not AsyncSession
            or writer.get_bind() is not initial_writer[0][1].sync_engine
        ):
            _deny()
        await writer.begin()
        check()
        for statement in (
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET TRANSACTION READ ONLY",
            "SET LOCAL search_path TO pg_catalog, public, pg_temp",
            "SET LOCAL TIME ZONE 'UTC'",
            "SET LOCAL lock_timeout='1000ms'",
            "SET LOCAL statement_timeout='10000ms'",
        ):
            await writer.execute(text(statement))
            check()
        writer_value = (
            await writer.execute(text(policy.ACTOR_CATALOG_SQL))
        ).scalar_one()
        check()
        raw_value = (
            await raw_session.execute(text(policy.ACTOR_CATALOG_SQL))
        ).scalar_one()
        check()
        observed = _verify_pair(
            raw_value,
            writer_value,
            raw_name=initial_raw[3],
            writer_name=initial_writer[3],
            approved_raw_role_oid=approved_raw_role_oid,
            parent_owner_oid=parent_owner_oid,
        )
        check()
        await writer.commit()
        check()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- never expose credentials or private SQL.
        _deny()
    finally:
        if writer is not None:
            await _close_owned(writer)
    check()
    return {"profile": observed, "raw": raw_value, "writer": writer_value}


async def _one(session, fence, statement):
    fence.check()
    value = (await session.execute(text(statement))).scalar_one()
    fence.check()
    return value


async def verify_actual_raw_history_catalog_v1(
    session, fence, approved_raw_role_oid, lineage_session_factory, raw_session_factory
):
    try:
        if (
            type(session) is not AsyncSession
            or session.get_bind().dialect.name != "postgresql"
        ):
            _deny()
        previous = await _one(session, fence, parent_policy.CATALOG_SQL)
        parent_policy.verify_account_history_partition_catalog(previous)
        original = await _one(session, fence, frame_policy.CATALOG_SQL)
        frame_policy.verify_catalog(
            original, previous, approved_collector_role_oid=approved_raw_role_oid
        )
        actual = await _current54_pair(
            session,
            fence,
            approved_raw_role_oid=approved_raw_role_oid,
            lineage_session_factory=lineage_session_factory,
            raw_session_factory=raw_session_factory,
            parent_owner_oid=previous["tables"][0]["owner_oid"],
        )
        fence.check()
        return journal.canonical(
            {"partition": previous, "frame": original, "current0054": actual}
        )
    except asyncio.CancelledError:
        raise
    except Current0054MultiRoleError:
        raise
    except Exception:  # noqa: BLE001 -- fixed failure, no source/head/role substitution.
        _deny()
