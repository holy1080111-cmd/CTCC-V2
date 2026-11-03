"""Actual current0054 owner capture/readback and explicit historical52 scope.

No current0052/0053 API is weakened. New54 admission requires the independent
new54 native profile. Historical observations retain actual source54/DB52.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from sqlalchemy import text

from app.domain import collector_current_0054_catalog_v1 as policy
from app.domain.source_primitives import canonical
from scripts import collector_current_0053_toolchain as old53
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import verify_final_hermetic as legacy
from scripts.verify_migration_identity import SOURCE_ROOT

CAPTURE_SCHEMA = "ctcc.collector_current0054.native_owner_observation.v1"
CAPTURE_PATHS = (
    *old53.CAPTURE_PATHS,
    "migrations/versions/0054_collector_issued_account_revisions.py",
    "app/domain/account_issued_revision_schema_v1.py",
    "app/domain/collector_current_0054_catalog_v1.py",
    "app/database/repositories/collector_current_0054_catalog.py",
    "app/database/repositories/collector_current_0054_multirole_catalog_v1.py",
    "scripts/current_contract_source_scopes_v1.py",
    "scripts/historical0053_on_current0054_toolchain.py",
    "scripts/historical0052_on_current0054_toolchain.py",
    "scripts/collector_current_0054_toolchain.py",
    "scripts/current0054_partition_retention.py",
    "scripts/current0054_hermetic_transitions.py",
    "scripts/current0054_hermetic_contracts.py",
    "scripts/current_hermetic_contract_dispatch_v1.py",
    "scripts/verify_current0054_hermetic.py",
    "scripts/verify_current0054_linux_union.py",
    "scripts/current0054_union_archive_v1.py",
)
MAX_BYTES = 16 * 1024 * 1024


class Current0054CaptureError(ValueError):
    pass


def _deny():
    raise Current0054CaptureError(
        "current0054_owner_capture_or_readback_unavailable"
    ) from None


def _pins(root):
    result = old53._pins(root)
    if len(set(CAPTURE_PATHS)) != len(CAPTURE_PATHS):
        _deny()
    for name in CAPTURE_PATHS:
        path = root / name
        if path.is_symlink() or not path.is_file():
            _deny()
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


async def _prepare(connection):
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL search_path TO pg_catalog,public,pg_temp",
    ):
        await connection.execute(text(statement))


def _owner_declaration():
    return {
        "schema": "ctcc.collector_current0054.owner_installation.v1",
        "database_head": "0054",
        "issuer_ddl_sha256": policy.issued.DDL_SHA256,
        "lineage_ddl_sha256": policy.lineage.DDL_SHA256,
        "native_definitions_sha256": policy._required_baseline(),
        "native_baseline_admission": True,
        "account_complete": False,
        "execution_authority": False,
    }


def expected_current0054_migration_readback():
    return {
        **legacy.expected_migration_revision_readback("0052"),
        "schema": "ctcc.migration_revision_readback.v11",
        "expected": "0054",
        "observed": "0054",
        "collector_issued_revision_owner_installation": _owner_declaration(),
    }


def _parent_readback(state):
    return legacy._validated_0051_parent_fields(
        state["witness_present"],
        state["guard_present"],
        state["trigger_catalog"],
        state["hwm_present"],
        state["hwm_guard_present"],
        state["hwm_trigger_catalog"],
        state["runtime_catalog"],
        state["native_identity_catalog"],
        coverage_catalog=state["coverage_catalog"],
        partition_catalog=state["partition_catalog"],
        cutover_catalog=state["cutover_catalog"],
    )


def current0054_migration_readback(state):
    if (
        type(state) is not dict
        or state.get("observed_head") != "0054"
        or "current0054_owner_catalog" not in state
    ):
        _deny()
    historical52 = old53.frame.verify_owner_installation_catalog(
        state["original_frame_catalog"], state["partition_catalog"]
    )
    installed54 = policy.verify_actual_owner_catalog(state["current0054_owner_catalog"])
    if historical52["revision"] != "0052":
        _deny()
    # The actual volatile role OID is retained in the raw capture. A deterministic
    # schema declaration excludes it without substituting another observed role.
    declaration = {
        name: value for name, value in installed54.items() if name != "role_oid"
    }
    result = {
        **_parent_readback(state),
        "schema": "ctcc.migration_revision_readback.v11",
        "expected": "0054",
        "observed": state["observed_head"],
        "original_history_frame_owner_installation": historical52,
        "collector_issued_revision_owner_installation": declaration,
    }
    if canonical(result) != canonical(expected_current0054_migration_readback()):
        _deny()
    return result


async def _actual54(connection):
    state = await old53._actual_current0053_state(connection)
    if state["observed_head"] != "0054":
        _deny()
    state["current0054_owner_catalog"] = await connection.scalar(
        text(policy.OWNER_CATALOG_SQL)
    )
    return state


async def capture_current0054_owner(connection, root=SOURCE_ROOT):
    before, pins = scopes.current0054_source_scope(root), _pins(root)
    await _prepare(connection)
    scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    first, second = await _actual54(connection), await _actual54(connection)
    observed = policy.observe_actual_owner_catalog(first["current0054_owner_catalog"])
    if (
        canonical(first) != canonical(second)
        or before != scopes.current0054_source_scope(root)
        or pins != _pins(root)
        or scope
        != await connection.run_sync(
            lambda actual: scopes.verify_database_scope(actual, root, before)
        )
    ):
        _deny()
    document = {
        "schema": CAPTURE_SCHEMA,
        "source_head": "0054",
        "database_head": "0054",
        "source_scope": scope,
        "source_files": pins,
        "actual_catalogs": first,
        "current0054_observation": observed,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    raw = canonical(document) + b"\n"
    if not 0 < len(raw) <= MAX_BYTES:
        _deny()
    return document, raw


def verify_current0054_owner_capture(document, root=SOURCE_ROOT):
    scope, pins = scopes.current0054_source_scope(root), _pins(root)
    if (
        type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "database_head",
            "source_scope",
            "source_files",
            "actual_catalogs",
            "current0054_observation",
            "native_baseline_admission",
            "runtime_admission",
            "account_complete",
            "execution_authority",
        }
        or document["schema"] != CAPTURE_SCHEMA
        or document["source_head"] != "0054"
        or document["database_head"] != "0054"
        or document["source_files"] != pins
        or any(
            document[key] is not False
            for key in (
                "native_baseline_admission",
                "runtime_admission",
                "account_complete",
                "execution_authority",
            )
        )
    ):
        _deny()
    expected_scope = {
        "schema": "ctcc.actual_source_historical_database_scope.v1",
        "source_head": "0054",
        "database_head": "0054",
        "migration_sha256": scope.actual_migration_sha256,
        "historical_prefix_sha256": scope.historical_prefix_sha256,
        "manifest_sha256": scope.manifest_sha256,
        "account_complete": False,
        "execution_authority": False,
    }
    if document["source_scope"] != expected_scope:
        _deny()
    measured = policy.observe_actual_owner_catalog(
        document["actual_catalogs"]["current0054_owner_catalog"]
    )
    if measured != document["current0054_observation"]:
        _deny()
    result = current0054_migration_readback(document["actual_catalogs"])
    if scope != scopes.current0054_source_scope(root) or pins != _pins(root):
        _deny()
    return result


async def read_current0054_migration(connection, root=SOURCE_ROOT):
    before, pins = scopes.current0054_source_scope(root), _pins(root)
    await _prepare(connection)
    scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    first = await _actual54(connection)
    result = current0054_migration_readback(first)
    final = await _actual54(connection)
    current0054_migration_readback(final)
    if (
        canonical(first) != canonical(final)
        or before != scopes.current0054_source_scope(root)
        or pins != _pins(root)
        or scope
        != await connection.run_sync(
            lambda actual: scopes.verify_database_scope(actual, root, before)
        )
    ):
        _deny()
    return result


async def read_current0054_partition_retention(connection, root=SOURCE_ROOT):
    from app.database.repositories.account_history_partition import (
        read_account_history_partition_retention,
    )
    from scripts.current0054_partition_retention import (
        history_partition_nonempty_snapshot_current54,
    )

    before, pins = scopes.current0054_source_scope(root), _pins(root)
    await _prepare(connection)
    actual_scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    first = await _actual54(connection)
    revision = current0054_migration_readback(first)
    retained = await read_account_history_partition_retention(connection)
    result = history_partition_nonempty_snapshot_current54(revision, retained)
    final = await _actual54(connection)
    current0054_migration_readback(final)
    if (
        canonical(first) != canonical(final)
        or before != scopes.current0054_source_scope(root)
        or pins != _pins(root)
        or actual_scope
        != await connection.run_sync(
            lambda actual: scopes.verify_database_scope(actual, root, before)
        )
    ):
        _deny()
    return result


def publish_capture(path, raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        _deny()
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        _deny()
    with path.open("xb") as stream:
        if stream.write(raw) != len(raw):
            _deny()
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        _deny()
    return hashlib.sha256(raw).hexdigest()


async def _run(mode, output):
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    if (
        os.environ.get("ENVIRONMENT") != "test"
        or os.environ.get("TRADING_MODE") != "analysis_only"
    ):
        _deny()
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        _deny()
    url = make_url(raw)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.username != "ctcc"
        or url.password is not None
        or re.fullmatch(r"ctcc-final-[a-f0-9]{12}-postgres", url.host or "") is None
        or url.port != 5432
        or url.database != "ctcc"
        or url.query
    ):
        _deny()
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            if mode == "capture-owner0054":
                if output is None:
                    _deny()
                document, raw = await capture_current0054_owner(connection)
                pin = publish_capture(output, raw)
                return {
                    "artifact_sha256": pin,
                    "schema": document["schema"],
                    "source_head": "0054",
                    "database_head": "0054",
                    "native_baseline_admission": False,
                    "account_complete": False,
                    "execution_authority": False,
                }
            if mode == "readback0054" and output is None:
                return await read_current0054_migration(connection)
            if mode == "retention0054" and output is None:
                return await read_current0054_partition_retention(connection)
            if mode == "historical52-readback" and output is None:
                from scripts.historical0052_on_current0054_toolchain import (
                    read_historical0052_migration_on_current0054,
                )

                return await read_historical0052_migration_on_current0054(connection)
            if mode == "historical53-readback" and output is None:
                from scripts.historical0053_on_current0054_toolchain import (
                    read_historical0053_migration_on_current0054,
                )

                return await read_historical0053_migration_on_current0054(connection)
            _deny()
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture-owner0054", action="store_true")
    mode.add_argument("--readback0054", action="store_true")
    mode.add_argument("--retention0054", action="store_true")
    mode.add_argument("--historical52-readback", action="store_true")
    mode.add_argument("--historical53-readback", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        selected = (
            "capture-owner0054"
            if args.capture_owner0054
            else "readback0054"
            if args.readback0054
            else "retention0054"
            if args.retention0054
            else "historical52-readback"
            if args.historical52_readback
            else "historical53-readback"
        )
        result = asyncio.run(_run(selected, args.output))
    except BaseException:  # noqa: BLE001 -- fail closed with sanitized CLI output
        result = {
            "schema": CAPTURE_SCHEMA,
            "status": "FAIL",
            "code": "current0054_owner_capture_or_readback_unavailable",
            "account_complete": False,
            "execution_authority": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
