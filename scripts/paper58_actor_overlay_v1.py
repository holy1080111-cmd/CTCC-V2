"""Exact raw ACL and dependency overlay over the admitted fresh source58 catalog."""

from __future__ import annotations

from scripts import paper58_catalog_normalization_v1 as normalizer
from scripts import paper58_catalog_reference_v1 as reference
from scripts import paper58_owner_preflight_v1 as owner
from scripts import paper58_source_coverage_v1 as source
from scripts.paper58_actor_acl_v1 import _NAME, _false_authority, _need

SCHEMA = "ctcc.paper58.fresh_unpinned_postprovision_acl_overlay_data.v1"
PURPOSE = "fresh_canonical58_unpinned_owner_after_exact_paper_acl_provisioning"
_ARRAY_DIMS = "_ctcc_catalog_array_dims_v1"
_RIGHT_CODES = {"INSERT": "a", "SELECT": "r", "UPDATE": "w", "DELETE": "d"}

# Run44890988317 observed precisely these implicit function ACLs. The other
# 133 application functions already have explicit owner-only EXECUTE. The
# post-provision state must explicitly exclude PUBLIC on all 161 functions.
_PREPROVISION_IMPLICIT_FUNCTIONS = frozenset(
    {
        "account_bill_archive_claim_guard",
        "account_capture_event_guard",
        "account_observation_batch_guard",
        "account_observation_member_guard",
        "account_observation_membership_guard",
        "demo_automation_control_revision_guard",
        "demo_control_commit_guard",
        "demo_control_journal_guard",
        "demo_control_state_guard",
        "qualification_ledger_immutable",
        "qualification_leverage_configuration_blocked_v55",
        "qualification_leverage_configuration_fence_v55",
        "qualification_leverage_configuration_guard_v55",
        "qualification_leverage_configuration_initial_ledger_v55",
        "qualification_leverage_configuration_local_flat_v55",
        "qualification_native_canonical_v55",
        "qualification_native_clock_v55",
        "qualification_native_filled_flat_data_v55",
        "qualification_native_manifest_v55",
        "qualification_native_number_v55",
        "qualification_native_plain_v55",
        "qualification_native_projection_eligible_v55",
        "qualification_native_protective_flat_data_v58",
        "qualification_native_report_eligible_v55",
        "qualification_native_rows_v55",
        "qualification_reservation_update",
        "qualification_scope_update",
        "qualification_submission_binding",
    }
)


def _acl_projection(row, field, *, catalog, actual_rights, old_rights):
    normalizer.catalog_types.require_catalog_array_metadata_v1(catalog, row)
    acl = row[field]
    _need(type(acl) is list and all(type(cell) is str for cell in acl))
    _need(sorted(acl) == sorted(actual_rights))
    dims = row[_ARRAY_DIMS]
    if field in dims:
        _need(dims[field] == f"[1:{len(actual_rights)}]")
        dims = dims | {field: None if old_rights is None else f"[1:{len(old_rights)}]"}
    return row | {field: old_rights, _ARRAY_DIMS: dims}


def _allocated_identity(document, actor_oid, actor_name):
    physical = document["actor_physical"]
    owner_oid, owner_name = physical["owner"][:2]
    _need(
        type(actor_oid) is int and 16384 <= actor_oid < 2**32 and actor_oid != owner_oid
    )
    _need(type(actor_name) is str and _NAME.fullmatch(actor_name) is not None)
    _need(actor_name != owner_name and not actor_name.startswith("pg_"))
    expected = [
        "pg_authid",
        actor_oid,
        {
            "type": "role",
            "schema": None,
            "name": actor_name,
            "identity": actor_name,
        },
    ]
    _need(sum(row == expected for row in document["references"]) == 1)
    return physical["database_oid"], owner_oid, owner_name


def _function_projection(observed, owner_name):
    owner_execution = [f"{owner_name}=X/{owner_name}"]
    return _acl_projection(
        observed,
        "proacl",
        catalog="pg_proc",
        actual_rights=owner_execution,
        old_rights=None
        if observed["proname"] in _PREPROVISION_IMPLICIT_FUNCTIONS
        else owner_execution,
    )


def _project_preprovision(document, *, actor_oid, actor_name):
    database_oid, owner_oid, owner_name = _allocated_identity(
        document, actor_oid, actor_name
    )
    table_grants = dict(source.TABLE_PRIVILEGES)
    _need(len(table_grants) == 9 and sum(map(len, table_grants.values())) == 21)
    targets, functions, edges, rows = set(), {}, [], []
    for kind, observed, extra in document["entries"]:
        row = observed
        if (
            kind == "relation"
            and observed["relkind"] == "r"
            and observed["relname"] in table_grants
        ):
            name = observed["relname"]
            allowed = {_RIGHT_CODES[right] for right in table_grants[name]}
            rights = "".join(code for code in "arwd" if code in allowed)
            row = _acl_projection(
                observed,
                "relacl",
                catalog="pg_class",
                old_rights=None,
                actual_rights=[
                    f"{owner_name}=arwdDxtm/{owner_name}",
                    f"{actor_name}={rights}/{owner_name}",
                ],
            )
            edge = [
                database_oid,
                "pg_class",
                row["oid"],
                0,
                "pg_authid",
                actor_oid,
                "a",
            ]
            _need(name not in targets and row["relowner"] == owner_oid)
            targets.add(name)
            edges.append(edge)
        elif kind == "function":
            _need(observed["oid"] not in functions)
            functions[observed["oid"]] = observed
            row = _function_projection(observed, owner_name)
        elif kind == "database":
            _need("database" not in targets and observed["oid"] == database_oid)
            row = _acl_projection(
                observed,
                "datacl",
                catalog="pg_database",
                old_rights=None,
                actual_rights=[
                    f"=c/{owner_name}",
                    f"{owner_name}=CTc/{owner_name}",
                    f"{actor_name}=c/{owner_name}",
                ],
            )
            targets.add("database")
            edges.append(
                [0, "pg_database", database_oid, 0, "pg_authid", actor_oid, "a"]
            )
        elif kind == "namespace":
            _need(
                "schema" not in targets
                and observed["nspname"] == "public"
                and observed["nspowner"] == 6171
            )
            base_acl = [
                "pg_database_owner=UC/pg_database_owner",
                "=U/pg_database_owner",
            ]
            row = _acl_projection(
                observed,
                "nspacl",
                catalog="pg_namespace",
                old_rights=base_acl,
                actual_rights=[*base_acl, f"{actor_name}=U/pg_database_owner"],
            )
            targets.add("schema")
            edges.append(
                [
                    database_oid,
                    "pg_namespace",
                    row["oid"],
                    0,
                    "pg_authid",
                    actor_oid,
                    "a",
                ]
            )
        rows.append([kind, row, extra])
    _need(targets == {*table_grants, "database", "schema"})
    _need(
        len(functions) == 161
        and _PREPROVISION_IMPLICIT_FUNCTIONS
        <= {row["proname"] for row in functions.values()}
    )
    # The original query repeats APP routines referenced by saved expressions.
    # Reconcile the duplicate physical OIDs before replaying those callbacks.
    callbacks = []
    for row in document["bootstrap"]["expression_callbacks"]:
        if row["oid"] in functions:
            _need(row == functions[row["oid"]])
            row = _function_projection(row, owner_name)
        callbacks.append(row)
    actor_edges, base_edges = [], []
    for edge in document["shared_dependencies"]:
        _need(type(edge) is list and len(edge) == 7)
        (actor_edges if edge[4:6] == ["pg_authid", actor_oid] else base_edges).append(
            edge
        )
    _need(len(edges) == 11 and sorted(actor_edges) == sorted(edges))
    # The input, including each observed ACL and dependency, stays untouched.
    # This separate view has a single purpose: replay the unchanged structural
    # literal after all allowed ACL changes were matched in the original view.
    return document | {
        "entries": rows,
        "shared_dependencies": base_edges,
        "bootstrap": document["bootstrap"] | {"expression_callbacks": callbacks},
    }, edges


def require_fresh_unpinned_postprovision_catalog_data_v1(
    document, *, actor_oid, actor_name
):
    """Match raw overlay and all other source58 structure, with no runtime admission."""
    normalizer._plain(document)
    owner._require_fresh_unpinned_owner_profile(document)
    projected, edges = _project_preprovision(
        document, actor_oid=actor_oid, actor_name=actor_name
    )
    matched = owner.require_fresh_unpinned_preprovision_catalog_v1(projected)
    return projected, {
        "schema": SCHEMA,
        "purpose": PURPOSE,
        "database_head": "0058",
        "observed_catalog_sha256": reference.digest(reference.canonical(document)),
        "projected_normalized_catalog_sha256": matched["normalized_catalog_sha256"],
        "source58_scope_sha256": matched["source58_scope_sha256"],
        "new_shared_acl_edges": len(edges),
        "postprovision_acl_overlay_verified": True,
        "bootstrap_owner_continuation_accepted": False,
        **_false_authority(),
    }


async def capture_fresh_unpinned_postprovision_owner_v1(
    connection, *, actor_oid, actor_name
):
    """Capture under actual owner locks, then compare the separately scoped overlay."""
    from sqlalchemy import text

    document, measurement = await owner._capture_fresh_unpinned_owner_catalog(
        connection
    )
    projected, matched = require_fresh_unpinned_postprovision_catalog_data_v1(
        document,
        actor_oid=actor_oid,
        actor_name=actor_name,
    )
    # The original wire query does not carry proacl dimensions. Check its
    # genuinely installed values separately without modifying that query.
    _need(
        await connection.scalar(
            text("""
SELECT (SELECT pg_catalog.count(*)=161 AND pg_catalog.bool_and(
 pg_catalog.array_dims(p.proacl)='[1:1]') FROM pg_catalog.pg_proc p
 WHERE p.pronamespace='public'::pg_catalog.regnamespace)
 AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_db_role_setting
 WHERE setrole=CAST(:actor_oid AS pg_catalog.oid))
"""),
            {"actor_oid": actor_oid},
        )
        is True
    )
    return document, projected, matched | measurement
