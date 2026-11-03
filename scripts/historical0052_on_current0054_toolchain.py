"""Actual-source54 / historical-DB52 readback; strict old52 API is unchanged."""

from sqlalchemy import text

from app.domain.source_primitives import canonical
from scripts import collector_current_0053_toolchain as old53
from scripts import current_contract_source_scopes_v1 as scopes
from scripts import verify_final_hermetic as legacy
from scripts.verify_migration_identity import SOURCE_ROOT, database_head

frame, owner, partition = old53.frame, old53.owner, old53.partition


class Historical0052ScopeError(ValueError):
    pass


def _deny():
    raise Historical0052ScopeError(
        "historical0052_on_current0054_scope_changed"
    ) from None


def _source(root):
    scope = scopes.historical0052_source_scope(root)
    if scope.actual_source_head != "0054" or scope.database_target != "0052":
        _deny()
    return scope


async def _prepare(connection):
    for statement in (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
        "SET TRANSACTION READ ONLY",
        "SET LOCAL TIME ZONE 'UTC'",
        "SET LOCAL search_path TO pg_catalog,public,pg_temp",
    ):
        await connection.execute(text(statement))


async def _require_later_schema_absent(connection):
    value = await connection.scalar(
        text("""
      SELECT to_regclass('public.demo_collector_current_revision_lineages') IS NULL
        AND to_regclass('public.demo_collector_issued_account_revisions') IS NULL
        AND NOT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='public' AND p.proname IN(
            'collector_current_revision_lineage_guard_v1','collector_current_revision_lineage_immutable_v1',
            'collector_issued_account_revision_guard_v1','collector_issued_account_revision_immutable_v1'))
    """)
    )
    if value is not True:
        _deny()


async def _actual_historical0052_state(connection):
    from app.domain import prospective_event_cutover_catalog_v2 as cutover
    from scripts import verify_final_hermetic as legacy
    from scripts.verify_controlled_writer_coverage_schema_v1 import read_catalog

    observed = await connection.run_sync(database_head)
    witness = await connection.scalar(
        text(
            "SELECT to_regclass('public.demo_account_revision_source_witnesses') IS NOT NULL"
        )
    )
    guard = await connection.scalar(
        text(
            "SELECT to_regprocedure('public.account_revision_source_witness_guard()') IS NOT NULL"
        )
    )
    triggers = (
        await connection.execute(
            text(legacy.ACCOUNT_REVISION_WITNESS_TRIGGER_CATALOG_SQL)
        )
    ).all()
    hwm = await connection.scalar(
        text("SELECT to_regclass('public.demo_account_measured_hwm_v2') IS NOT NULL")
    )
    hwm_guard = await connection.scalar(
        text(
            "SELECT to_regprocedure('public.account_measured_hwm_v2_guard()') IS NOT NULL"
        )
    )
    hwm_triggers = (
        await connection.execute(
            text(legacy.ACCOUNT_MEASURED_HWM_V2_TRIGGER_CATALOG_SQL)
        )
    ).all()
    return {
        "observed_head": observed,
        "witness_present": witness,
        "guard_present": guard,
        "trigger_catalog": [tuple(row) for row in triggers],
        "hwm_present": hwm,
        "hwm_guard_present": hwm_guard,
        "hwm_trigger_catalog": [tuple(row) for row in hwm_triggers],
        "runtime_catalog": await connection.scalar(
            text(legacy.PUBLIC_RUNTIME_BUNDLE_SCHEMA_SQL)
        ),
        "native_identity_catalog": await connection.scalar(
            text(legacy.NATIVE_WITNESS_IDENTITY_SCHEMA_SQL)
        ),
        "coverage_catalog": await read_catalog(connection),
        "partition_catalog": await connection.scalar(text(partition.CATALOG_SQL)),
        "cutover_catalog": await connection.scalar(text(cutover.CATALOG_SQL)),
        "original_frame_catalog": await connection.scalar(text(frame.CATALOG_SQL)),
    }


def _readback(actual):
    if actual["observed_head"] != "0052":
        _deny()
    return legacy.migration_revision_readback(
        "0052",
        actual["observed_head"],
        actual["witness_present"],
        actual["guard_present"],
        actual["trigger_catalog"],
        actual["hwm_present"],
        actual["hwm_guard_present"],
        actual["hwm_trigger_catalog"],
        actual["runtime_catalog"],
        actual["native_identity_catalog"],
        coverage_catalog=actual["coverage_catalog"],
        partition_catalog=actual["partition_catalog"],
        cutover_catalog=actual["cutover_catalog"],
        original_frame_catalog=actual["original_frame_catalog"],
    )


async def read_historical0052_migration_on_current0054(connection, root=SOURCE_ROOT):
    scope, pins = _source(root), old53._pins(root)
    await _prepare(connection)
    actual_scope = await connection.run_sync(
        lambda actual: scopes.verify_database_scope(actual, root, scope)
    )
    await _require_later_schema_absent(connection)
    first = await _actual_historical0052_state(connection)
    result = _readback(first)
    final = await _actual_historical0052_state(connection)
    _readback(final)
    await _require_later_schema_absent(connection)
    if (
        canonical(first) != canonical(final)
        or scope != _source(root)
        or pins != old53._pins(root)
        or actual_scope
        != await connection.run_sync(
            lambda actual: scopes.verify_database_scope(actual, root, scope)
        )
    ):
        _deny()
    return {
        "schema": "ctcc.source0054_historical0052.migration_readback.v1",
        "source_head": "0054",
        "database_head": "0052",
        "source_scope": actual_scope,
        "historical0052_readback": result,
        "account_complete": False,
        "execution_authority": False,
    }
