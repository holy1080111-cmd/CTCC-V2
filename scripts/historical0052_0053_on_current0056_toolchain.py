"""Actual source0056 readback of separate historical DB0052 and DB0053.

This is a source/database migration observation only. It never substitutes a
historical database for a current native account or execution authority.
"""

from sqlalchemy import text

from app.domain.source_primitives import canonical
from scripts import collector_current_0053_toolchain as legacy53
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import historical0052_0053_on_current0055_toolchain as source55
from scripts import historical0052_on_current0054_toolchain as historical52
from scripts.verify_migration_identity import SOURCE_ROOT

_ADDED_0056_OBJECTS_ABSENT = text("""
SELECT
  pg_catalog.to_regclass('public.gate3_fitted_candidate_bindings') IS NULL
  AND pg_catalog.to_regclass('public.gate3_fitted_candidate_acks') IS NULL
  AND pg_catalog.to_regclass('public.gate3_fitted_candidate_evaluations') IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public'
      AND pg_catalog.left(p.proname,13)='gate3_fitted_'
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_trigger t
    JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public'
      AND pg_catalog.left(t.tgname,13)='gate3_fitted_'
  )
""")


class Current0056HistoricalReadbackError(ValueError):
    """Fixed source or historical database mismatch."""


def _deny():
    raise Current0056HistoricalReadbackError(
        "source0056_historical_database_scope_changed"
    ) from None


def _source(root, target):
    getter = {
        "0052": scopes.historical0052_source_scope,
        "0053": scopes.historical0053_source_scope,
    }.get(target)
    if getter is None:
        _deny()
    scope = getter(root)
    if scope.actual_source_head != "0056" or scope.database_target != target:
        _deny()
    return scope


async def _database_scope(connection, root, scope):
    return await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, scope)
    )


async def _check_later_objects(connection, target):
    if target not in {"0052", "0053"}:
        _deny()
    try:
        if target == "0052":
            await historical52._require_later_schema_absent(connection)
        if (
            await connection.scalar(source55._LATER_OBJECTS_ABSENT) is not True
            or await connection.scalar(_ADDED_0056_OBJECTS_ABSENT) is not True
        ):
            _deny()
    except Current0056HistoricalReadbackError:
        raise
    except Exception:  # noqa: BLE001 - SQL and private connection detail stay local.
        _deny()


async def _begin_readback(connection, root, target):
    scope, pins = _source(root, target), legacy53._pins(root)
    await historical52._prepare(connection)
    observed = await _database_scope(connection, root, scope)
    await _check_later_objects(connection, target)
    return scope, pins, observed


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


async def read_historical0052_migration_on_current0056(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0052")
    first = await historical52._actual_historical0052_state(connection)
    readback = historical52._readback(first)
    final = await historical52._actual_historical0052_state(connection)
    historical52._readback(final)
    await _finish_readback(connection, root, start, first, final)
    return {
        **_historical_envelope(
            start[2], "ctcc.source0056_historical0052.migration_readback.v1"
        ),
        "historical0052_readback": readback,
    }


async def capture_historical0053_owner_on_current0056(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0053")
    first = await legacy53._catalogs(connection)
    final = await legacy53._catalogs(connection)
    await _finish_readback(connection, root, start, first, final)
    document = {
        **_historical_envelope(
            start[2], "ctcc.source0056_historical0053.owner_observation.v1"
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


async def read_historical0053_migration_on_current0056(connection, root=SOURCE_ROOT):
    start = await _begin_readback(connection, root, "0053")
    first = await legacy53._actual_current0053_state(connection)
    readback = legacy53.current0053_migration_readback(**first)
    final = await legacy53._actual_current0053_state(connection)
    legacy53.current0053_migration_readback(**final)
    await _finish_readback(connection, root, start, first, final)
    return {
        **_historical_envelope(
            start[2], "ctcc.source0056_historical0053.migration_readback.v1"
        ),
        "historical0053_readback": readback,
    }
