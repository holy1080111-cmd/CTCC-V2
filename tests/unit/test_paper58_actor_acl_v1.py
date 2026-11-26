"""Adversarial synthetic privilege matrices on genuine catalog shape, no SQL."""

import gzip
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text

from scripts import paper58_actor_acl_v1 as acl
from scripts import paper58_actor_inventory_sql_v1 as capture

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/paper58_fresh_preprovision_v1"
ACTOR_OID, ACTOR_NAME = 50000, "ctcc_paper_synthetic_actor"


@pytest.fixture(scope="module")
def base_catalog():
    return json.loads(
        gzip.decompress((FIXTURE / "paper58-reference-a-raw.json.gz").read_bytes())
    )


@pytest.fixture(scope="module")
def expected_policy(base_catalog):
    return acl.fresh_unpinned_actor_policy_data_v1(
        base_catalog, actor_oid=ACTOR_OID, actor_name=ACTOR_NAME
    )


def _synthetic_inventory(policy):
    """Wire-shape fixture only; every actual actor proof remains pending."""
    table_flags = {
        table: {
            privilege: privilege in policy["table_grants"].get(table, ())
            for privilege in acl.TABLE_PRIVILEGES
        }
        for table in policy["columns"]
    }
    return {
        "schema": acl.INVENTORY_SCHEMA,
        "profile": {
            "database": policy["database"],
            "database_oid": policy["database_oid"],
            "current_user": ACTOR_NAME,
            "session_user": ACTOR_NAME,
            "role": "none",
            "read_only": "on",
            "search_path": "pg_catalog",
            "timezone": "UTC",
            "server_version": "170011",
            "tls": True,
        },
        "actor": {"oid": ACTOR_OID, "name": ACTOR_NAME, "flags": list(acl.ACTOR_FLAGS)},
        "database_privileges": {"CONNECT": True, "CREATE": False, "TEMPORARY": False},
        "database_grant_options": dict.fromkeys(
            ("CONNECT", "CREATE", "TEMPORARY"), False
        ),
        "public_schema_privileges": {"USAGE": True, "CREATE": False},
        "public_schema_grant_options": dict.fromkeys(("USAGE", "CREATE"), False),
        "table_privileges": table_flags,
        "table_grant_options": {
            table: dict.fromkeys(acl.TABLE_PRIVILEGES, False)
            for table in policy["columns"]
        },
        "column_privileges": {
            table: {
                column: {
                    privilege: privilege in policy["table_grants"].get(table, ())
                    for privilege in acl.COLUMN_PRIVILEGES
                }
                for column in columns
            }
            for table, columns in policy["columns"].items()
        },
        "column_grant_options": {
            table: {
                column: dict.fromkeys(acl.COLUMN_PRIVILEGES, False)
                for column in columns
            }
            for table, columns in policy["columns"].items()
        },
        "sequence_privileges": {
            name: dict.fromkeys(acl.SEQUENCE_PRIVILEGES, False)
            for name in policy["sequences"]
        },
        "sequence_grant_options": {
            name: dict.fromkeys(acl.SEQUENCE_PRIVILEGES, False)
            for name in policy["sequences"]
        },
        "application_function_execute": dict.fromkeys(
            policy["application_functions"], False
        ),
        "application_function_grant_options": dict.fromkeys(
            policy["application_functions"], False
        ),
        "application_function_count": len(policy["application_functions"]),
        "actor_shared_acl_edges": [
            edge[:] for edge in policy["actor_shared_acl_edges"]
        ],
        "explicit_table_acl_edges": sorted(
            [name, privilege, policy["owner_oid"], ACTOR_OID, False]
            for name, privileges in policy["table_grants"].items()
            for privilege in privileges
        ),
        "explicit_database_acl_edges": [
            [policy["database"], "CONNECT", policy["owner_oid"], ACTOR_OID, False]
        ],
        "explicit_schema_acl_edges": [
            ["public", "USAGE", policy["public_schema_owner_oid"], ACTOR_OID, False]
        ],
        **{
            name: []
            for name in (
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
            )
        },
        "pg_control_system_execute": False,
        "pg_control_system_acl_owner_only": True,
    }


def _verify(document, base_catalog):
    return acl.require_restricted_actor_inventory_data_v1(
        document,
        preprovision_catalog=base_catalog,
        actor_oid=ACTOR_OID,
        actor_name=ACTOR_NAME,
    )


def test_complete_synthetic_inventory_is_data_only_and_keeps_real_proofs_pending(
    base_catalog, expected_policy
):
    result = _verify(_synthetic_inventory(expected_policy), base_catalog)
    assert result["effective_table_cells"] == 21 and result["table_grant_targets"] == 9
    assert (
        result["tables_audited"] == 105
        and result["application_functions_audited"] == 161
    )
    assert result["sequences_audited"] == 9
    assert result["privilege_data_matches"] is True
    for key in (
        "actual_owner_lock_custody_verified",
        "actual_actor_connection_verified",
        "recovery_verified",
        "schema_admission",
        "paper_actor_admission",
        "startup_admission",
        "execution_authority",
    ):
        assert result[key] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "native_table_select",
        "maintain",
        "table_grant_option",
        "missing_table",
        "extra_table",
        "missing_column",
        "extra_column",
        "column_grant_option",
        "public_temp",
        "schema_create",
        "sequence_usage",
        "app_execute",
        "app_grant_option",
        "pg_control_execute",
        "pg_control_acl_drift",
        "stock_override",
        "membership",
        "ownership",
        "default_acl",
        "parameter_acl",
        "global_setting",
        "stored_role_setting",
        "explicit_column_acl",
        "wrong_database",
        "set_role",
        "numeric_bool",
        "numeric_grant_option",
        "wrong_schema_grantor",
        "extra_implicit_table_grant",
        "extra_database_acl",
        "function_overload",
    ],
)
def test_missing_or_additional_effective_rights_and_metadata_deny(
    expected_policy, mutation
):
    document = _synthetic_inventory(expected_policy)
    if mutation == "native_table_select":
        document["table_privileges"]["qualification_reservations"]["SELECT"] = True
    elif mutation == "maintain":
        document["table_privileges"]["paper_account_state"]["MAINTAIN"] = True
    elif mutation == "table_grant_option":
        document["table_grant_options"]["paper_account_state"]["SELECT"] = True
    elif mutation == "missing_table":
        document["table_privileges"].pop("qualification_reservations")
    elif mutation == "extra_table":
        document["table_privileges"]["unapproved_table"] = {}
    elif mutation == "missing_column":
        document["column_privileges"]["paper_account_state"].pop(
            next(iter(document["column_privileges"]["paper_account_state"]))
        )
    elif mutation == "extra_column":
        document["column_privileges"]["paper_account_state"]["unapproved_column"] = {}
    elif mutation == "column_grant_option":
        next(iter(document["column_grant_options"]["paper_account_state"].values()))[
            "SELECT"
        ] = True
    elif mutation == "public_temp":
        document["database_privileges"]["TEMPORARY"] = True
    elif mutation == "schema_create":
        document["public_schema_privileges"]["CREATE"] = True
    elif mutation == "sequence_usage":
        next(iter(document["sequence_privileges"].values()))["USAGE"] = True
    elif mutation == "app_execute":
        document["application_function_execute"][
            next(iter(document["application_function_execute"]))
        ] = True
    elif mutation == "app_grant_option":
        document["application_function_grant_options"][
            next(iter(document["application_function_grant_options"]))
        ] = True
    elif mutation == "pg_control_execute":
        document["pg_control_system_execute"] = True
    elif mutation == "pg_control_acl_drift":
        document["pg_control_system_acl_owner_only"] = False
    elif mutation == "stock_override":
        document["stock_function_acl_override_edges"].append([1, "pg_read_file"])
    elif mutation == "membership":
        document["role_membership_edges"].append(
            [ACTOR_OID, 10, 10, False, False, True]
        )
    elif mutation == "ownership":
        document["ownership_edges"].append([1, 1259, 50001, 0])
    elif mutation == "default_acl":
        document["default_acl_edges"].append([ACTOR_OID, 0, "EXECUTE"])
    elif mutation == "parameter_acl":
        document["parameter_acl_edges"].append(
            ["session_preload_libraries", ACTOR_OID, "SET", False]
        )
    elif mutation == "global_setting":
        document["global_settings"].append("session_preload_libraries")
    elif mutation == "stored_role_setting":
        document["role_settings"].append("search_path")
    elif mutation == "explicit_column_acl":
        document["explicit_column_acl_edges"].append(
            ["paper_account_state", "id", 10, ACTOR_OID, "SELECT", False]
        )
    elif mutation == "wrong_database":
        document["profile"]["database_oid"] += 1
    elif mutation == "set_role":
        document["profile"]["role"] = "another_role"
    elif mutation == "numeric_bool":
        document["table_privileges"]["paper_account_state"]["SELECT"] = 1
    elif mutation == "numeric_grant_option":
        document["explicit_table_acl_edges"][0][-1] = 0
    elif mutation == "wrong_schema_grantor":
        assert (
            expected_policy["public_schema_owner_oid"] != expected_policy["owner_oid"]
        )
        document["explicit_schema_acl_edges"][0][2] = expected_policy["owner_oid"]
    elif mutation == "extra_database_acl":
        document["actor_shared_acl_edges"].append(
            [0, "pg_database", 942, 0, "pg_authid", ACTOR_OID, "a"]
        )
    elif mutation == "function_overload":
        document["application_function_count"] += 1
    else:
        document["explicit_table_acl_edges"].pop()
    with pytest.raises(acl.Paper58ActorAclRefused) as refused:
        acl._compare_restricted_actor_inventory(document, expected_policy)
    assert refused.value.category in acl._INVENTORY_FIELDS
    assert str(refused.value) == "paper58_actor_acl_refused"
    if mutation == "pg_control_execute":
        assert refused.value.category == "pg_control_system_execute"
    elif mutation == "pg_control_acl_drift":
        assert refused.value.category == "pg_control_system_acl_owner_only"
    elif mutation == "stock_override":
        assert refused.value.category == "stock_function_acl_override_edges"


@pytest.mark.parametrize("index", range(7))
def test_every_actor_role_flag_is_exact(expected_policy, index):
    document = _synthetic_inventory(expected_policy)
    document["actor"]["flags"][index] = not acl.ACTOR_FLAGS[index]
    with pytest.raises(acl.Paper58ActorAclRefused) as refused:
        acl._compare_restricted_actor_inventory(document, expected_policy)
    assert refused.value.category == "actor"


def test_actual_public_schema_owner_drives_grantor_not_the_superuser_name(
    expected_policy,
):
    assert expected_policy["public_schema_owner_oid"] != expected_policy["owner_oid"]
    assert expected_policy["database_grants"] == ["CONNECT"]
    assert expected_policy["public_schema_grants"] == ["USAGE"]
    assert (
        expected_policy["parameter_grants"] == expected_policy["sequence_grants"] == []
    )


def test_source_query_has_no_sqlalchemy_binds_or_application_function_call():
    query = capture.ACTOR_INVENTORY_SQL
    assert text(query)._bindparams == {}
    assert "pg_control_system(" not in query
    assert "pg_catalog.has_function_privilege" in query
    # REL_17_11 pg_proc.dat OID 3019 takes the physical int2 attribute number.
    assert (
        query.count(
            "pg_catalog.has_column_privilege(a.oid,c.oid,x.attnum::pg_catalog.int2,"
        )
        == 2
    )
    assert "pg_catalog.pg_init_privs" in query
    assert "MAINTAIN" in query and "WITH GRANT OPTION" in query
    assert "split_part(v,'=',1)" in query  # Stored-setting values never leave PG.
    assert ";" not in query
    assert not re.search(
        r"(?im)^\s*(GRANT|REVOKE|CREATE ROLE|ALTER ROLE|SET ROLE)\b", query
    )


def test_inventory_foreign_value_cannot_run_equality_or_getters(
    base_catalog, expected_policy
):
    document = _synthetic_inventory(expected_policy)
    calls = []

    class Foreign:
        def __eq__(self, other):
            calls.append("eq")
            return True

    document["pg_control_system_execute"] = Foreign()
    with pytest.raises(ValueError):
        _verify(document, base_catalog)
    assert calls == []
