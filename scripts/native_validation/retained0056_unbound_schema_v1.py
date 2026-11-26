"""Offline catalog and preservation checks for the unbound 0055 to 0056 candidate.

This module contains no write entry or account initialization. Its observations
are usable only inside a separately reviewed native maintenance supervisor.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path

_QUERY = (
    Path(__file__).resolve().parent
    / "sql/retained0056_unbound_fitted_dependencies_v1.sql"
)
_QUERY_SHA256 = "c09a58aa2d0f2c346dcd9fa5babc55730eda1bdde5dd152f7af3aacb52c16206"
_OWNER = "ctcc"
_OWNER_OID = 10
_MAX_DEPENDENCIES = 100_000
_UNBOUND = """SELECT NOT EXISTS(SELECT 1 FROM public.qualification_account_scopes)
 AND NOT EXISTS(SELECT 1 FROM public.demo_account_controls)"""
_BOOTSTRAP_ROLE = """SELECT r.oid=10 AND r.rolsuper AND r.rolinherit AND r.rolcreatedb
 AND r.rolcreaterole AND r.rolcanlogin AND r.rolreplication AND r.rolbypassrls
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles x WHERE x.rolcanlogin AND x.oid<>10)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE 10 IN (m.member,m.roleid))
 FROM pg_catalog.pg_roles r WHERE r.rolname=CURRENT_USER AND r.rolname=SESSION_USER"""
_LOCAL_FENCE = """SELECT
 NOT EXISTS(SELECT 1 FROM public.qualification_reservations WHERE state IS DISTINCT FROM 'reconciled_flat')
 AND NOT EXISTS(SELECT 1 FROM public.qualification_leverage_configuration_intents_v1
   WHERE phase IS NULL OR phase NOT IN ('settled','no_change','not_sent'))
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_position_state)
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_order_state
   WHERE state IS NULL OR state NOT IN ('filled','canceled','mmp_canceled'))
 AND NOT EXISTS(SELECT 1 FROM public.okx_demo_algo_order_state
   WHERE state IS NULL OR state NOT IN ('canceled','order_failed','effective'))
 AND NOT EXISTS(SELECT 1 FROM public.positions WHERE status IS DISTINCT FROM 'closed' OR closed_at IS NULL)
 AND NOT EXISTS(SELECT 1 FROM public.orders
   WHERE status IS NULL OR status NOT IN ('filled','canceled','cancelled','rejected','expired','mmp_canceled'))
 AND NOT EXISTS(SELECT 1 FROM public.protective_orders
   WHERE status IS NULL OR status NOT IN ('filled','canceled','cancelled','rejected','expired','closed'))
 AND NOT EXISTS(SELECT 1 FROM public.trades WHERE status IS DISTINCT FROM 'closed' OR closed_at IS NULL)
 AND NOT EXISTS(SELECT 1 FROM public.demo_soak_sessions WHERE state IS NULL OR state='running')
 AND EXISTS(SELECT 1 FROM public.demo_automation_state
   WHERE id=1 AND active_trades::pg_catalog.jsonb='{}'::pg_catalog.jsonb AND last_error IS NULL
   AND active_instrument_id IS NULL AND active_client_order_id IS NULL
   AND active_start_equity IS NULL AND active_started_at IS NULL)"""
_DEPENDENCY_ROWS = """SELECT classid::pg_catalog.int8,objid::pg_catalog.int8,objsubid,
 refclassid::pg_catalog.int8,refobjid::pg_catalog.int8,refobjsubid,deptype::pg_catalog.text
 FROM pg_catalog.pg_depend LIMIT 100001"""
_SHARED_ROWS = """SELECT pg_catalog.to_jsonb(dependency)::pg_catalog.text
 FROM pg_catalog.pg_shdepend AS dependency LIMIT 100001"""
_INITIAL_ACL_ROWS = """SELECT pg_catalog.to_jsonb(privilege)::pg_catalog.text
 FROM pg_catalog.pg_init_privs AS privilege LIMIT 100001"""


class UnboundSchemaRefused(ValueError):
    """The known schema-only profile or preservation witness did not match."""


class _Phase(Enum):
    OBSERVE = ("repeatable read", "on", True)
    REHEARSE = ("read committed", "off", False)


@dataclass(frozen=True, slots=True)
class _Fitted:
    structural_sha256: str
    context_role_sha256: str
    dependency_sha256: str
    dependency_count: int
    physical: frozenset[tuple[int, int]]


@dataclass(frozen=True, slots=True, repr=False)
class _Inventory:
    projection: dict
    dependencies: tuple[Counter, Counter, Counter]
    fitted: _Fitted | None


def _need(value, reason):
    if value is not True:
        raise UnboundSchemaRefused(reason) from None


def _digest(raw):
    return sha256(raw).hexdigest()


def _dependency_query():
    self_path = Path(__file__).resolve(strict=True)
    _need(_QUERY.resolve(strict=True) == _QUERY, "unbound56_dependency_source_changed")
    _need(
        _QUERY.parent.parent == self_path.parent, "unbound56_dependency_source_changed"
    )
    with _QUERY.open("rb") as stream:
        raw = stream.read(6397)
    _need(
        len(raw) == 6396 and _digest(raw) == _QUERY_SHA256,
        "unbound56_dependency_source_changed",
    )
    return raw.decode("ascii")


async def _require_unbound(connection, pins):
    from sqlalchemy import text

    from scripts import retained0017_owner_upgrade_0055_v1 as retained

    route = pins.retained.route
    _need(
        (route.owner, route.owner_oid) == (_OWNER, _OWNER_OID),
        "unbound56_matching_bootstrap_owner_reference_required",
    )
    _need(
        await connection.scalar(text(_BOOTSTRAP_ROLE)) is True,
        "unbound56_matching_bootstrap_role_reference_required",
    )
    _need(
        await connection.scalar(text(_UNBOUND)) is True,
        "unbound56_account_scope_present",
    )
    _need(
        await connection.scalar(text(_LOCAL_FENCE)) is True,
        "unbound56_unresolved_local_state",
    )
    await retained.require_legacy_live_negative_fence_v1(connection)


def _project_fitted(document, access, phase, reference):
    """Compare the actual reader or writer context and the exact raw projection."""
    from scripts import current0056_fitted_catalog_pair_v1 as native
    from scripts import current0056_fitted_delta_catalog_v1 as fitted
    from scripts import retained0056_fitted_reference_pair_v1 as codec
    from scripts import retained0056_unscoped_fitted_observation_v1 as unscoped

    _need(type(phase) is _Phase, "unbound56_catalog_phase")
    read_only = phase is _Phase.OBSERVE
    context = {
        "schema": fitted.SCHEMA,
        "head": "0056",
        "server_version": "170011",
        "server_build": native.PG_BUILD,
        "actor": _OWNER,
        "login": _OWNER,
        "tls": True,
        "read_only": read_only,
        "repeatable" if read_only else "read_committed": True,
        "durable": True,
        "search_path": "pg_catalog, pg_temp",
    }
    fields = (
        *unscoped._STRUCTURAL_FIELDS,
        *unscoped._ROLE_FIELDS,
        "function_grants",
        "table_grants",
    )
    _need(
        type(document) is dict
        and set(document) == set(context) | set(fields)
        and all(
            type(document[key]) is type(value) and document[key] == value
            for key, value in context.items()
        )
        and all(type(document[key]) is list for key in fields)
        and len(codec.canonical(document)) <= 1_000_000,
        "unbound56_native_fitted_context_changed",
    )
    expected_access = {
        "owner_bound": True,
        "relations": len(fitted.TABLES),
        "routines": len(fitted.INTERNAL | fitted.APIS.keys()),
        "owned_heap_relations": True,
        "owned_functions": True,
        "no_extra_user_triggers": True,
        "native_fk_triggers": 12,
        "no_alternate_table_privilege": True,
        "no_alternate_execute": True,
    }
    _need(
        document["function_grants"] == document["table_grants"] == []
        and type(access) is dict
        and set(access) == set(expected_access)
        and all(
            type(access[key]) is type(value) and access[key] == value
            for key, value in expected_access.items()
        ),
        "unbound56_fitted_effective_acl_or_storage_changed",
    )
    structure = _digest(
        codec.canonical(
            {
                "schema": "ctcc.retained0056.unscoped_fitted_structure.v1",
                **{key: document[key] for key in unscoped._STRUCTURAL_FIELDS},
            }
        )
    )
    role = _digest(
        codec.canonical(
            {
                "schema": "ctcc.retained0056.unscoped_fitted_context_role.v1",
                **{key: document[key] for key in unscoped._ROLE_FIELDS},
            }
        )
    )
    _need(
        structure == reference.structural_sha256,
        "unbound56_matching_fitted_structure_reference_required",
    )
    _need(
        role == reference.fitted_context_role_sha256,
        "unbound56_matching_fitted_role_reference_required",
    )
    return structure, role


def _project_dependency_graph(bound, reference):
    from scripts import retained0056_fitted_reference_pair_v1 as codec

    _need(
        type(bound) is dict
        and set(bound)
        == {
            "layout_ok",
            "shared",
            "initial",
            "dependency_count",
            "edges",
            "objects",
            "physical",
        }
        and bound["layout_ok"] is True
        and type(bound["shared"]) is type(bound["initial"]) is int
        and bound["shared"] == bound["initial"] == 0,
        "unbound56_fitted_dependency_shape_or_shared_acl_changed",
    )
    for field in ("edges", "objects", "physical"):
        values = bound[field]
        _need(
            type(values) is list and 0 < len(values) <= 512,
            "unbound56_fitted_dependency_extent",
        )
        _need(
            len({codec.canonical(value) for value in values}) == len(values),
            "unbound56_fitted_dependency_duplicates",
        )
    _need(
        type(bound["dependency_count"]) is int
        and bound["dependency_count"]
        == len(bound["edges"])
        == reference.fitted_dependency_count
        and len(bound["physical"]) == len(bound["objects"])
        and all(
            type(row) is list
            and len(row) == 2
            and all(type(v) is int and 0 < v < 2**32 for v in row)
            for row in bound["physical"]
        ),
        "unbound56_fitted_dependency_identity_changed",
    )
    result = _digest(codec.canonical([bound["edges"], bound["objects"]]))
    _need(
        result == reference.logical_dependency_sha256,
        "unbound56_matching_fitted_dependency_reference_required",
    )
    return result, frozenset(tuple(row) for row in bound["physical"])


async def _observe_fitted(connection, phase):
    from sqlalchemy import text

    from scripts import current0056_fitted_delta_catalog_v1 as fitted
    from scripts import native_owner_upgrade_0056_v1 as native
    from scripts import retained0056_bootstrap_reference_pin_v2 as pin
    from scripts import retained0056_unscoped_fitted_observation_v1 as unscoped

    _need(type(phase) is _Phase, "unbound56_catalog_phase")
    isolation, readonly, is_read = phase.value
    outer = ("pg_catalog, public, pg_temp", isolation, readonly)
    _need(
        tuple((await connection.execute(native._CATALOG_CONTEXT)).one()) == outer,
        "unbound56_catalog_context",
    )
    reference = pin.read_reviewed_bootstrap_fitted_data_v2()
    fitted.pinned_migration(native._SOURCE)
    document = await native._read_fitted_owner_catalog_v2(connection, read_only=is_read)
    access = await connection.scalar(
        unscoped._EFFECTIVE_ACL, {"owner": _OWNER, "owner_oid": _OWNER_OID}
    )
    structure, context_role = _project_fitted(document, access, phase, reference)
    nested = await connection.begin_nested()
    try:
        await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog, pg_temp")
        _need(
            tuple((await connection.execute(native._CATALOG_CONTEXT)).one())
            == ("pg_catalog, pg_temp", isolation, readonly),
            "unbound56_dependency_context",
        )
        bound = await connection.scalar(
            text(_dependency_query()),
            {
                "tables": sorted(fitted.TABLES),
                "routines": sorted(
                    "public." + name for name in fitted.INTERNAL | fitted.APIS.keys()
                ),
            },
        )
        dependencies, physical = _project_dependency_graph(bound, reference)
    finally:
        await nested.rollback()
        _need(
            tuple((await connection.execute(native._CATALOG_CONTEXT)).one()) == outer,
            "unbound56_dependency_context_restore",
        )
    return _Fitted(
        structure, context_role, dependencies, bound["dependency_count"], physical
    )


async def _catalog_dependencies(connection):
    from sqlalchemy import text

    snapshots = []
    for query in (_DEPENDENCY_ROWS, _SHARED_ROWS, _INITIAL_ACL_ROWS):
        rows = tuple(
            tuple(row) for row in (await connection.execute(text(query))).all()
        )
        _need(len(rows) <= _MAX_DEPENDENCIES, "unbound56_dependency_inventory_limit")
        snapshots.append(Counter(rows))
    return tuple(snapshots)


def _require_preserved_dependencies(before, after, fitted):
    previous, shared, initial = before
    current, fresh_shared, fresh_initial = after
    _need(
        previous <= current and shared == fresh_shared and initial == fresh_initial,
        "unbound56_existing_dependency_or_acl_changed",
    )
    added = current - previous
    _need(
        sum(added.values()) == fitted.dependency_count
        and all(
            (row[0], row[1]) in fitted.physical or (row[3], row[4]) in fitted.physical
            for row in added
        ),
        "unbound56_unreviewed_dependency_delta",
    )


async def _inventory(connection, pins, head, phase):
    from sqlalchemy import text

    from app.domain import collector_current_0056_inherited_catalog_v1 as inherited
    from scripts import current0056_fitted_delta_catalog_v1 as fitted_catalog
    from scripts import native_owner_upgrade_0056_v1 as native
    from scripts import retained0017_owner_upgrade_0055_v1 as retained
    from scripts import retained0055_negative_stop_reassert_v1 as negative
    from scripts import retained0055_stop1_preflight_v1 as stop1
    from scripts.native_owner_catalog_snapshot_0058_v1 import LOGICAL_CATALOG

    _need(
        head in {"0055", "0056"} and type(phase) is _Phase, "unbound56_inventory_scope"
    )
    read_only = phase is _Phase.OBSERVE
    inspection = negative._inspection_pins_v1(pins)
    await retained.rows.heap_am.lock_native_owner_marker_v1(connection)
    await native._marker_safe_v2(connection, pins.retained)
    await retained._actor(connection, inspection, head, read_only=read_only)
    if head == "0055":
        state = await negative._observe(
            connection,
            pins,
            negative._Phase.READ if read_only else negative._Phase.WRITE,
        )
        _need(
            negative._digest(state) == stop1._AFTER_SHA256,
            "unbound56_stop1_snapshot_changed",
        )
        await _require_unbound(connection, pins)
        fitted = None
    else:
        tables = retained._target_tables() | native._ADDED_TABLES
        _need(
            all(retained.rows._NAME.fullmatch(name) for name in tables),
            "unbound56_native_table_names",
        )
        await connection.exec_driver_sql(
            "LOCK TABLE "
            + ",".join('public."' + name + '"' for name in sorted(tables))
            + " IN ACCESS SHARE MODE NOWAIT"
        )
        await native._marker_safe_v2(connection, pins.retained)
        document = await connection.scalar(text(inherited.OWNER_CATALOG_SQL))
        observation = (
            inherited._observe_owner_repeatable_read_catalog_v1(document)
            if read_only
            else inherited.observe_owner_readwrite_installation_0056(document)
        )
        _need(
            observation["database_head"] == "0056"
            and observation["role_oid"] == _OWNER_OID
            and observation["execution_authority"] is False,
            "unbound56_inherited_catalog_changed",
        )
        await _require_unbound(connection, pins)
        fitted = await _observe_fitted(connection, phase)
        layouts = await retained._layouts(
            connection,
            inspection,
            expected_names=tables,
        )
        await retained._maintenance(connection, inspection, before_settings=False)
        state = await retained._state(connection, inspection, layouts)
    control = await connection.scalar(negative._CONTROL_SQL)
    negative._control(control, revision=1)
    objects = tuple(
        tuple(row)
        for row in (
            await connection.execute(
                native._ORIGINAL_OBJECTS,
                {
                    "added_tables": sorted(native._ADDED_TABLES),
                    "added_functions": sorted(
                        {
                            name.partition("(")[0]
                            for name in fitted_catalog.INTERNAL
                            | fitted_catalog.APIS.keys()
                        }
                    ),
                },
            )
        ).all()
    )
    _need(
        0 < len(objects) <= 100_000
        and len({row[:3] for row in objects}) == len(objects),
        "unbound56_native_object_inventory",
    )
    size, logical = (await connection.execute(LOGICAL_CATALOG)).one()
    _need(
        type(size) is int
        and 0 < size <= 32 * 1024 * 1024
        and native._pin(logical, native._HEX),
        "unbound56_logical_catalog_inventory",
    )
    projection = {
        "relations": tuple(tuple(row[:3]) for row in state["row_layouts"]),
        "snapshot": {**state, "role_state_sha256": state["roles_sha256"]},
        "objects": objects,
        "acl": state["acl"],
        "logical_sha256": logical,
        "column_metadata": state["column_metadata"],
        "control": control,
    }
    return _Inventory(projection, await _catalog_dependencies(connection), fitted)


def _require_preserved(before, after):
    from scripts import native_owner_upgrade_0056_v1 as native

    _need(
        type(before) is type(after) is _Inventory
        and before.fitted is None
        and type(after.fitted) is _Fitted,
        "unbound56_preservation_scope",
    )
    native._preserved_v2(before.projection, after.projection)
    old_columns = before.projection["column_metadata"]
    old_keys = {row[:2] for row in old_columns}
    _need(
        tuple(row for row in after.projection["column_metadata"] if row[:2] in old_keys)
        == old_columns
        and before.projection["control"] == after.projection["control"],
        "unbound56_original_column_layout_or_stop_changed",
    )
    _require_preserved_dependencies(
        before.dependencies, after.dependencies, after.fitted
    )


async def observe_unbound_stop1_plan_v1(lease, engine, pins, source_archive, sealed):
    """Plan from a genuinely held Stop1 reader. This grants no write lease."""
    from sqlalchemy import text

    from scripts import retained0055_negative_stop_reassert_v1 as negative
    from scripts import retained0055_stop1_preflight_v1 as stop1
    from scripts import retained_pg_bootstrap_custody_v1 as custody

    _dependency_query()
    source = await stop1.observe_owned_stop1(
        lease, engine, pins, source_archive, sealed
    )
    previous = None
    identities = set()
    for _ in range(2):
        async with engine.connect() as connection:
            transaction = await negative._begin(connection, pins, negative._Phase.READ)
            try:
                await custody._replay_owned_connection_v1(lease, connection)
                identity = await connection.scalar(
                    text("SELECT pg_catalog.pg_backend_pid()")
                )
                _need(
                    type(identity) is int
                    and identity > 0
                    and identity not in identities,
                    "unbound56_independent_plan_backend",
                )
                identities.add(identity)
                current = await _inventory(connection, pins, "0055", _Phase.OBSERVE)
                _need(
                    previous is None or current == previous,
                    "unbound56_independent_plan_inventory",
                )
                previous = current
                await custody._replay_owned_connection_v1(lease, connection)
            finally:
                await transaction.rollback()
    _need(
        custody._record(lease).commit_attempts == 0, "unbound56_plan_commit_attempted"
    )
    negative._source(pins, source_archive)
    _dependency_query()
    return {
        "schema": "ctcc.retained0056.unbound_schema_validation_plan.v1",
        "status": "UNBOUND_0055_MAINTENANCE_PLAN_DATA",
        "database_head": "0055",
        "intended_database_target": "0056",
        "control_revision": 1,
        "retained_snapshot_sha256": source["snapshot_sha256"],
        "fitted_reference_pair_sha256": source["reference_pair_sha256"],
        "independent_plan_observations": 2,
        "mutation_performed": False,
        "schema_upgrade_authorized": False,
        "startup_admission": False,
        "account_complete": False,
        "exchange_flat": None,
        "execution_authority": False,
    }
