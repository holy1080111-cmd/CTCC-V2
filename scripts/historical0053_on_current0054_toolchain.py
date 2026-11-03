"""Explicit actual-source0054 / historical-database0053 owner observations.

Strict current0053 APIs remain unchanged and refuse source0054. The historical
wrapper records both different real heads, checks the exact immutable53 prefix,
and invokes the actual53 owner contracts without relabelling a receipt.
"""

from sqlalchemy import text

from app.domain import collector_current_0053_owner_installation_v1 as owner
from app.domain.source_primitives import canonical
from scripts import collector_current_0053_toolchain as old53
from scripts import current_contract_source_scopes_v1 as scopes
from scripts.verify_migration_identity import SOURCE_ROOT, database_head

CAPTURE_SCHEMA = "ctcc.source0054_historical0053.owner_observation.v1"


class Historical0053ScopeError(ValueError):
    pass


def _deny():
    raise Historical0053ScopeError(
        "historical0053_on_current0054_scope_changed"
    ) from None


def _source(root):
    value = scopes.historical0053_source_scope(root)
    if value.actual_source_head != "0054" or value.database_target != "0053":
        _deny()
    return value


async def _prepare(connection):
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL search_path TO pg_catalog,public,pg_temp",
    ):
        await connection.execute(text(statement))


async def capture_historical0053_owner_on_current0054(connection, root=SOURCE_ROOT):
    before, pins = _source(root), old53._pins(root)
    await _prepare(connection)
    actual_scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    observed = await old53._catalogs(connection)
    final = await old53._catalogs(connection)
    after_scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    if (
        canonical(observed) != canonical(final)
        or actual_scope != after_scope
        or before != _source(root)
        or pins != old53._pins(root)
    ):
        _deny()
    document = {
        "schema": CAPTURE_SCHEMA,
        "source_head": "0054",
        "database_head": "0053",
        "source_scope": actual_scope,
        "source_files": pins,
        **observed,
        "native_baseline_admission": False,
        "runtime_admission": False,
        "account_complete": False,
        "execution_authority": False,
    }
    raw = canonical(document) + b"\n"
    if not 0 < len(raw) <= old53.MAX_CAPTURE_BYTES:
        _deny()
    return document, raw


def verify_historical0053_owner_on_current0054_capture(document, root=SOURCE_ROOT):
    scope, pins = _source(root), old53._pins(root)
    if (
        type(document) is not dict
        or set(document)
        != {
            "schema",
            "source_head",
            "database_head",
            "source_scope",
            "source_files",
            "partition_catalog",
            "historical_frame_catalog",
            "current0053_owner_catalog",
            "historical0052_installation",
            "current0053_observation",
            "native_baseline_admission",
            "runtime_admission",
            "account_complete",
            "execution_authority",
        }
        or document["schema"] != CAPTURE_SCHEMA
        or document["source_head"] != "0054"
        or document["database_head"] != "0053"
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
        "source_head": scope.actual_source_head,
        "database_head": scope.database_target,
        "migration_sha256": scope.actual_migration_sha256,
        "historical_prefix_sha256": scope.historical_prefix_sha256,
        "manifest_sha256": scope.manifest_sha256,
        "account_complete": False,
        "execution_authority": False,
    }
    historical52 = old53.frame.verify_owner_installation_catalog(
        document["historical_frame_catalog"], document["partition_catalog"]
    )
    observed53 = owner.observe_owner_installation_catalog(
        document["current0053_owner_catalog"]
    )
    if (
        document["source_scope"] != expected_scope
        or document["historical0052_installation"] != historical52
        or historical52["revision"] != "0052"
        or document["current0053_observation"] != observed53
    ):
        _deny()
    installed53 = owner.verify_owner_installation_catalog(
        document["current0053_owner_catalog"]
    )
    if scope != _source(root) or pins != old53._pins(root):
        _deny()
    return {
        "schema": "ctcc.source0054_historical0053.owner_capture_verified.v1",
        "source_head": "0054",
        "database_head": "0053",
        "historical0052_installation": historical52,
        "historical0053_installation": installed53,
        "account_complete": False,
        "execution_authority": False,
    }


async def read_historical0053_migration_on_current0054(connection, root=SOURCE_ROOT):
    before, pins = _source(root), old53._pins(root)
    await _prepare(connection)
    actual_scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, before)
    )
    first = await old53._actual_current0053_state(connection)
    result = old53.current0053_migration_readback(**first)
    second = await old53._actual_current0053_state(connection)
    old53.current0053_migration_readback(**second)
    if (
        first["observed_head"] != "0053"
        or await connection.run_sync(database_head) != "0053"
        or canonical(first) != canonical(second)
        or before != _source(root)
        or pins != old53._pins(root)
        or actual_scope
        != await connection.run_sync(
            lambda actual: scopes.verify_database_scope(actual, root, before)
        )
    ):
        _deny()
    return {
        "schema": "ctcc.source0054_historical0053.migration_readback.v1",
        "source_head": "0054",
        "database_head": "0053",
        "source_scope": actual_scope,
        "historical0053_readback": result,
        "account_complete": False,
        "execution_authority": False,
    }
