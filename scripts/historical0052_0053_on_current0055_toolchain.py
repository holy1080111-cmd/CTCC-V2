"""Actual source0055 readback of historical DB0052, DB0053 and current DB0055."""

from sqlalchemy import text

from app.domain import collector_current_0055_catalog_v1 as current55
from app.domain.source_primitives import canonical
from scripts import collector_current_0053_toolchain as legacy53
from scripts import collector_current_0054_toolchain as legacy54
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import historical0052_on_current0054_toolchain as historical52
from scripts.verify_migration_identity import SOURCE_ROOT

_LATER_OBJECTS_ABSENT = text("""
SELECT
  pg_catalog.to_regclass('public.demo_collector_issued_account_revisions') IS NULL
  AND pg_catalog.to_regclass('public.demo_control_clear_acknowledgements_v2') IS NULL
  AND pg_catalog.to_regclass('public.demo_policy_counter_initializations_v1') IS NULL
  AND pg_catalog.to_regclass('public.qualification_leverage_configuration_intents_v1') IS NULL
  AND pg_catalog.to_regclass('public.qualification_leverage_configuration_attempts_v1') IS NULL
  AND pg_catalog.to_regclass('public.qualification_leverage_configuration_results_v1') IS NULL
  AND pg_catalog.to_regclass('public.qualification_native_close_intent_once_v55') IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN (
      'collector_issued_account_revision_guard_v1',
      'collector_issued_account_revision_immutable_v1',
      'account_history_partition_current55_v2_lock',
      'account_history_partition_current55_v2_append',
      'account_history_original_frame_current55_v2_read',
      'qualification_native_filled_flat_data_v55',
      'qualification_native_report_eligible_v55'))
""")


class Current0055ReadbackError(ValueError):
    """Fixed source or database mismatch at this read-only boundary."""


def _deny():
    raise Current0055ReadbackError("source0055_actual_database_scope_changed")


def _source(root, target):
    get_scope = {
        "0052": scopes.historical0052_source_scope,
        "0053": scopes.historical0053_source_scope,
        "0055": scopes.actual_current_source_scope,
    }[target]
    scope = get_scope(root)
    if scope.actual_source_head != "0055" or scope.database_target != target:
        _deny()
    return scope


async def _database_scope(connection, root, scope):
    return await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, scope)
    )


async def _begin_readback(connection, root, target):
    scope, pins = _source(root, target), legacy53._pins(root)
    await historical52._prepare(connection)
    observed = await _database_scope(connection, root, scope)
    await _check_later_objects(connection, target)
    return scope, pins, observed


async def _check_later_objects(connection, target):
    if target == "0052":
        await historical52._require_later_schema_absent(connection)
    if target in {"0052", "0053"} and (
        await connection.scalar(_LATER_OBJECTS_ABSENT) is not True
    ):
        _deny()


async def _finish_readback(connection, root, start, first, final):
    scope, pins, observed = start
    await _check_later_objects(connection, scope.database_target)
    if (
        canonical(first) != canonical(final)
        or scope != _source(root, scope.database_target)
        or pins != legacy53._pins(root)
        or observed != await _database_scope(connection, root, scope)
    ):
        _deny()


def _historical_envelope(scope, schema):
    return {
        "schema": schema,
        "source_head": scope["source_head"],
        "database_head": scope["database_head"],
        "source_scope": scope,
        "account_complete": False,
        "execution_authority": False,
    }


async def read_historical0052_migration_on_current0055(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0052")
    first = await historical52._actual_historical0052_state(connection)
    readback = historical52._readback(first)
    final = await historical52._actual_historical0052_state(connection)
    historical52._readback(final)
    await _finish_readback(connection, root, start, first, final)
    return {
        **_historical_envelope(
            start[2], "ctcc.source0055_historical0052.migration_readback.v1"
        ),
        "historical0052_readback": readback,
    }


async def capture_historical0053_owner_on_current0055(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0053")
    first = await legacy53._catalogs(connection)
    final = await legacy53._catalogs(connection)
    await _finish_readback(connection, root, start, first, final)
    document = {
        **_historical_envelope(
            start[2], "ctcc.source0055_historical0053.owner_observation.v1"
        ),
        "source_files": start[1],
        **first,
        "native_baseline_admission": False,
        "runtime_admission": False,
    }
    raw = canonical(document) + b"\n"
    if not 0 < len(raw) <= legacy53.MAX_CAPTURE_BYTES:
        _deny()
    return document, raw


async def read_historical0053_migration_on_current0055(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0053")
    first = await legacy53._actual_current0053_state(connection)
    readback = legacy53.current0053_migration_readback(**first)
    final = await legacy53._actual_current0053_state(connection)
    legacy53.current0053_migration_readback(**final)
    await _finish_readback(connection, root, start, first, final)
    return {
        **_historical_envelope(
            start[2], "ctcc.source0055_historical0053.migration_readback.v1"
        ),
        "historical0053_readback": readback,
    }


async def _actual0055(connection):
    state = await historical52._actual_historical0052_state(connection)
    if state["observed_head"] != "0055":
        _deny()
    state["current0055_owner_catalog"] = await connection.scalar(
        text(current55.OWNER_CATALOG_SQL)
    )
    return state


def _current0055_readback(state):
    parent = legacy54._parent_readback(state)
    historical = legacy53.frame.verify_owner_installation_catalog(
        state["original_frame_catalog"], state["partition_catalog"]
    )
    owner = current55.verify_actual_owner_catalog(state["current0055_owner_catalog"])
    if (
        historical["revision"] != "0052"
        or owner["database_head"] != "0055"
        or state["observed_head"] != "0055"
    ):
        _deny()
    return {
        **parent,
        "schema": "ctcc.source0055_current0055.parent_migration_readback.v1",
        "expected": "0055",
        "observed": state["observed_head"],
        "original_history_frame_owner_installation": historical,
        "current0055_owner_installation": owner,
        "account_complete": False,
        "execution_authority": False,
    }


async def read_current0055_parent_migration(connection, root=SOURCE_ROOT):
    """Verify preserved B4/B5 catalogs and the actual current55 owner catalog."""
    start = await _begin_readback(connection, root, "0055")
    first = await _actual0055(connection)
    result = _current0055_readback(first)
    final = await _actual0055(connection)
    _current0055_readback(final)
    await _finish_readback(connection, root, start, first, final)
    return {**result, "source_scope": start[2]}
