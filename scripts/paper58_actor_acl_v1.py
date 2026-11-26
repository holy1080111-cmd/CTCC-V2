"""Closed Paper ACL/profile DATA for the fresh-unpinned source58 purpose.

There is no connection, GRANT, recovery or application entry point here.
An inventory supplied as JSON can only be compared as DATA. The owned runtime
must collect it itself after owner-held structural and exact ACL-overlay proof.
"""

from __future__ import annotations

import re

from scripts import paper58_catalog_normalization_v1 as normalizer
from scripts import paper58_owner_preflight_v1 as owner
from scripts import paper58_source_coverage_v1 as source

POLICY_SCHEMA = "ctcc.paper58.fresh_unpinned_actor_acl_policy_data.v1"
INVENTORY_SCHEMA = "ctcc.paper58.restricted_actor_privilege_inventory.v1"
MATCH_SCHEMA = "ctcc.paper58.restricted_actor_privilege_match_data.v1"
ACTOR_FLAGS = (False, False, False, False, True, False, False)
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SEQUENCE_PRIVILEGES = ("SELECT", "UPDATE", "USAGE")
_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z", re.ASCII)

# PostgreSQL17 documents MAINTAIN separately from ordinary CRUD, and table
# grants also supply column privileges. Check both and every grant option.
# https://www.postgresql.org/docs/17/functions-info.html#FUNCTIONS-INFO-ACCESS-TABLE
# https://www.postgresql.org/docs/17/ddl-priv.html


class Paper58ActorAclRefused(ValueError):
    """No catalog identifiers or credentials are included in a refusal."""


def _need(value):
    if value is not True:
        raise Paper58ActorAclRefused("paper58_actor_acl_refused") from None


def _false_authority():
    return {
        "schema_admission": False,
        "paper_actor_admission": False,
        "startup_admission": False,
        "execution_authority": False,
    }


def fresh_unpinned_actor_policy_data_v1(preprovision_catalog, *, actor_oid, actor_name):
    """Compile the explicit policy from the entire approved pre-provision DATA.

    Sequence names and historical column order are obtained only after the
    whole literal matches; they are not guessed from ORM naming or accepted
    from a caller's partial table roster. This still grants no authority.
    """
    matched = owner.require_fresh_unpinned_preprovision_catalog_v1(preprovision_catalog)
    _need(type(actor_oid) is int and 16384 <= actor_oid < 2**32)
    _need(type(actor_name) is str and _NAME.fullmatch(actor_name) is not None)
    _need(not actor_name.startswith("pg_"))
    physical = preprovision_catalog["actor_physical"]
    _need(actor_oid != physical["owner"][0])
    _need(actor_name != physical["owner"][1])
    namespaces = [
        row
        for kind, row, _ in preprovision_catalog["entries"]
        if kind == "namespace" and row["nspname"] == "public"
    ]
    _need(len(namespaces) == 1 and type(namespaces[0]["nspowner"]) is int)
    relations = {
        row["oid"]: row["relname"]
        for kind, row, _ in preprovision_catalog["entries"]
        if kind == "relation" and row["relkind"] == "r"
    }
    _need(len(relations) == 105 and len(set(relations.values())) == 105)
    columns = {name: [] for name in relations.values()}
    for kind, row, _ in preprovision_catalog["entries"]:
        if kind == "attribute" and row["attrelid"] in relations:
            columns[relations[row["attrelid"]]].append((row["attnum"], row["attname"]))
    column_names = {
        name: [column for _, column in sorted(values)]
        for name, values in sorted(columns.items())
    }
    sequences = sorted(
        row["relname"]
        for kind, row, _ in preprovision_catalog["entries"]
        if kind == "relation" and row["relkind"] == "S"
    )
    functions = sorted(
        row["proname"]
        for kind, row, _ in preprovision_catalog["entries"]
        if kind == "function"
    )
    _need(len(sequences) == len(set(sequences)) == 9)
    _need(len(functions) == len(set(functions)) == 161)
    table_grants = dict(source.TABLE_PRIVILEGES)
    _need(len(table_grants) == 9 and sum(map(len, table_grants.values())) == 21)
    _need(set(table_grants) <= set(column_names))
    acl_edges = [
        [physical["database_oid"], "pg_class", oid, 0, "pg_authid", actor_oid, "a"]
        for oid, name in relations.items()
        if name in table_grants
    ]
    acl_edges.extend(
        (
            [
                0,
                "pg_database",
                physical["database_oid"],
                0,
                "pg_authid",
                actor_oid,
                "a",
            ],
            [
                physical["database_oid"],
                "pg_namespace",
                namespaces[0]["oid"],
                0,
                "pg_authid",
                actor_oid,
                "a",
            ],
        )
    )
    return {
        "schema": POLICY_SCHEMA,
        "purpose": owner.PURPOSE,
        "preprovision_catalog_sha256": matched["normalized_catalog_sha256"],
        "source58_scope_sha256": matched["source58_scope_sha256"],
        "database": preprovision_catalog["profile"]["database"],
        "database_oid": physical["database_oid"],
        "owner": physical["owner"][1],
        "owner_oid": physical["owner"][0],
        "public_schema_owner_oid": namespaces[0]["nspowner"],
        "actor": actor_name,
        "actor_oid": actor_oid,
        "actor_flags": list(ACTOR_FLAGS),
        "columns": column_names,
        "sequences": sequences,
        "application_functions": functions,
        "table_grants": {name: list(values) for name, values in table_grants.items()},
        "actor_shared_acl_edges": sorted(acl_edges),
        "database_grants": ["CONNECT"],
        "public_schema_grants": ["USAGE"],
        "public_revocations": {
            "database": ["TEMPORARY"],
            "public_schema": ["CREATE"],
            "application_functions": ["EXECUTE"],
        },
        "grant_options": False,
        "role_memberships": [],
        "explicit_column_grants": [],
        "sequence_grants": [],
        "application_function_grants": [],
        "parameter_grants": [],
        "stock_driver_builtin_acl_policy": "exact_initial_pg_init_privs_or_native_default",
        "pg_control_system_execute": False,
        "bootstrap_owner_continuation_accepted": False,
        **_false_authority(),
    }


def _matrix(actual, expected):
    _need(type(actual) is dict and set(actual) == set(expected))
    for name, flags in expected.items():
        row = actual[name]
        _need(type(row) is dict and set(row) == set(flags))
        _need(all(type(value) is bool for value in row.values()))
        _need(row == flags)


def require_restricted_actor_inventory_data_v1(
    document, *, preprovision_catalog, actor_oid, actor_name
):
    """Compare all effective cells/options, not a success flag or row count.

    APP/native routines are audited separately from stock driver/introspection
    functions. An EXECUTE grant to pg_control_system is never required or
    inferred from the maintenance owner's ability to call it.
    """
    policy = fresh_unpinned_actor_policy_data_v1(
        preprovision_catalog, actor_oid=actor_oid, actor_name=actor_name
    )
    return _compare_restricted_actor_inventory(document, policy)


def _compare_restricted_actor_inventory(document, policy):
    normalizer._plain(document)
    _need(type(document) is dict)
    _need(
        set(document)
        == {
            "schema",
            "profile",
            "actor",
            "database_privileges",
            "database_grant_options",
            "public_schema_privileges",
            "public_schema_grant_options",
            "table_privileges",
            "table_grant_options",
            "column_privileges",
            "column_grant_options",
            "sequence_privileges",
            "sequence_grant_options",
            "application_function_execute",
            "application_function_grant_options",
            "explicit_table_acl_edges",
            "explicit_database_acl_edges",
            "explicit_schema_acl_edges",
            "explicit_column_acl_edges",
            "role_membership_edges",
            "ownership_edges",
            "default_acl_edges",
            "parameter_acl_edges",
            "role_settings",
            "database_settings",
            "database_role_settings",
            "global_settings",
            "stock_function_acl_override_edges",
            "pg_control_system_execute",
            "actor_shared_acl_edges",
            "application_function_count",
        }
    )
    _need(document["schema"] == INVENTORY_SCHEMA)
    profile = document["profile"]
    _need(
        type(profile) is dict
        and set(profile)
        == {
            "database",
            "database_oid",
            "current_user",
            "session_user",
            "role",
            "read_only",
            "search_path",
            "timezone",
            "server_version",
            "tls",
        }
    )
    _need(
        profile
        == {
            "database": policy["database"],
            "database_oid": policy["database_oid"],
            "current_user": policy["actor"],
            "session_user": policy["actor"],
            "role": "none",
            "read_only": "on",
            "search_path": "pg_catalog",
            "timezone": "UTC",
            "server_version": "170011",
            "tls": True,
        }
    )
    _need(type(profile["tls"]) is bool and profile["tls"] is True)
    _need(type(profile["database_oid"]) is int)
    actor = document["actor"]
    _need(type(actor) is dict and set(actor) == {"oid", "name", "flags"})
    _need(type(actor["oid"]) is int and actor["oid"] == policy["actor_oid"])
    _need(actor["name"] == policy["actor"])
    _need(type(actor["flags"]) is list and len(actor["flags"]) == 7)
    _need(all(type(flag) is bool for flag in actor["flags"]))
    _need(tuple(actor["flags"]) == ACTOR_FLAGS)

    _matrix(
        {"database": document["database_privileges"]},
        {"database": {"CONNECT": True, "CREATE": False, "TEMPORARY": False}},
    )
    _matrix(
        {"database": document["database_grant_options"]},
        {"database": {"CONNECT": False, "CREATE": False, "TEMPORARY": False}},
    )
    _matrix(
        {"public": document["public_schema_privileges"]},
        {"public": {"USAGE": True, "CREATE": False}},
    )
    _matrix(
        {"public": document["public_schema_grant_options"]},
        {"public": {"USAGE": False, "CREATE": False}},
    )
    table_flags = {
        table: {
            privilege: privilege in policy["table_grants"].get(table, ())
            for privilege in TABLE_PRIVILEGES
        }
        for table in policy["columns"]
    }
    _matrix(document["table_privileges"], table_flags)
    _matrix(
        document["table_grant_options"],
        {table: dict.fromkeys(TABLE_PRIVILEGES, False) for table in policy["columns"]},
    )
    _need(type(document["column_privileges"]) is dict)
    _need(type(document["column_grant_options"]) is dict)
    _need(set(document["column_privileges"]) == set(policy["columns"]))
    _need(set(document["column_grant_options"]) == set(policy["columns"]))
    for table, columns in policy["columns"].items():
        _matrix(
            document["column_privileges"][table],
            {
                column: {
                    privilege: privilege in policy["table_grants"].get(table, ())
                    for privilege in COLUMN_PRIVILEGES
                }
                for column in columns
            },
        )
        _matrix(
            document["column_grant_options"][table],
            {column: dict.fromkeys(COLUMN_PRIVILEGES, False) for column in columns},
        )
    for key in ("sequence_privileges", "sequence_grant_options"):
        _matrix(
            document[key],
            {
                name: dict.fromkeys(SEQUENCE_PRIVILEGES, False)
                for name in policy["sequences"]
            },
        )
    for key in ("application_function_execute", "application_function_grant_options"):
        row = document[key]
        _need(type(row) is dict and set(row) == set(policy["application_functions"]))
        _need(all(value is False for value in row.values()))
    _need(type(document["application_function_count"]) is int)
    _need(
        document["application_function_count"] == len(policy["application_functions"])
    )
    _need(document["pg_control_system_execute"] is False)

    expected_table_edges = sorted(
        [name, privilege, policy["owner_oid"], policy["actor_oid"], False]
        for name, privileges in policy["table_grants"].items()
        for privilege in privileges
    )
    for key in (
        "explicit_table_acl_edges",
        "explicit_database_acl_edges",
        "explicit_schema_acl_edges",
    ):
        edges = document[key]
        _need(type(edges) is list)
        for edge in edges:
            _need(type(edge) is list and len(edge) == 5)
            _need(type(edge[0]) is str and type(edge[1]) is str)
            _need(type(edge[2]) is int and type(edge[3]) is int)
            _need(type(edge[4]) is bool and edge[4] is False)
    _need(document["explicit_table_acl_edges"] == expected_table_edges)
    _need(
        document["explicit_database_acl_edges"]
        == [
            [
                policy["database"],
                "CONNECT",
                policy["owner_oid"],
                policy["actor_oid"],
                False,
            ]
        ]
    )
    _need(
        document["explicit_schema_acl_edges"]
        == [
            [
                "public",
                "USAGE",
                policy["public_schema_owner_oid"],
                policy["actor_oid"],
                False,
            ]
        ]
    )
    shared = document["actor_shared_acl_edges"]
    _need(type(shared) is list)
    for edge in shared:
        _need(type(edge) is list and len(edge) == 7)
        _need(all(type(edge[index]) is int for index in (0, 2, 3, 5)))
        _need(all(type(edge[index]) is str for index in (1, 4, 6)))
    _need(sorted(shared) == policy["actor_shared_acl_edges"])
    for key in (
        "explicit_column_acl_edges",
        "role_membership_edges",
        "ownership_edges",
        "default_acl_edges",
        "parameter_acl_edges",
        "role_settings",
        "database_settings",
        "database_role_settings",
        "global_settings",
        "stock_function_acl_override_edges",
    ):
        _need(document[key] == [])
    return {
        "schema": MATCH_SCHEMA,
        "purpose": owner.PURPOSE,
        "effective_table_cells": 21,
        "table_grant_targets": 9,
        "tables_audited": len(table_flags),
        "columns_audited": sum(map(len, policy["columns"].values())),
        "sequences_audited": len(policy["sequences"]),
        "application_functions_audited": len(policy["application_functions"]),
        "privilege_data_matches": True,
        "actual_owner_lock_custody_verified": False,
        "actual_actor_connection_verified": False,
        "recovery_verified": False,
        **_false_authority(),
    }
