"""Metadata contracts only; synthetic catalogs never mint Source admission."""

import json
from copy import deepcopy

import pytest

from app.domain import native_close_intent_catalog_v55 as catalog
from app.domain import native_close_intent_schema_v55 as schema


def _catalog(*, actor=100):
    owner, table_oid = 100, 200
    spec = json.loads(schema.INDEX_SPEC_JSON)
    acl = [[owner, owner, right, False] for right in sorted(catalog._PRIVILEGES)]
    if actor != owner:
        acl.extend([[actor, owner, right, False] for right in ("INSERT", "SELECT")])
    table = {
        "oid": table_oid,
        "namespace": "public",
        "name": "qualification_submission_outcomes",
        "owner": owner,
        "kind": "r",
        "persistence": "p",
        "partition": False,
        "has_subclass": False,
        "rules": False,
        "row_security": False,
        "force_row_security": False,
        "column_acl": False,
        "acl_valid": True,
        "acl": acl,
    }
    index = {
        "oid": 201,
        "namespace": "public",
        "name": spec["index_name"],
        "owner": owner,
        "kind": "i",
        "persistence": "p",
        "options": None,
        "partition": False,
        "table_oid": table_oid,
        "table_namespace": "public",
        "table_name": spec["table"],
        "table_owner": owner,
        "access_method": "btree",
        "natts": 1,
        "keyatts": 1,
        "unique": True,
        "valid": True,
        "ready": True,
        "live": True,
        "immediate": True,
        "primary": False,
        "exclusion": False,
        "replica_identity": False,
        "nulls_not_distinct": False,
        "clustered": False,
        "expressions_is_null": True,
        "predicate": spec["predicate"],
        "definition": schema.INDEX_SQL,
        "key_attnums": [2],
        "key_options": [0],
        "columns": ["reservation_id"],
        "opclasses": [
            {
                "oid": 202,
                "namespace": "pg_catalog",
                "name": "text_ops",
                "default": True,
                "input_type": "text",
                "access_method": "btree",
            }
        ],
        "collations": [
            {
                "oid": 203,
                "namespace": "pg_catalog",
                "name": "default",
                "deterministic": True,
            }
        ],
    }
    triggers = []
    for ordinal, (name, (kind, function)) in enumerate(
        sorted(catalog.TRIGGERS.items())
    ):
        triggers.append(
            {
                "oid": 300 + ordinal,
                "table_oid": table_oid,
                "name": name,
                "enabled": "O",
                "kind": kind,
                "internal": False,
                "arguments": 0,
                "argument_bytes": "",
                "columns": "",
                "qual_is_null": True,
                "constraint_oid": 0,
                "old_table": None,
                "new_table": None,
                "definition": "synthetic DATA " + name,
                "function_oid": 400 + ordinal,
                "function_namespace": "public",
                "function_name": function,
                "function_owner": owner,
                "function_arguments": 0,
                "function_kind": "f",
            }
        )
    return {
        "tables": [table],
        "columns": deepcopy(catalog.COLUMNS),
        "indexes": [index],
        "triggers": triggers,
    }


def test_exact_owner_and_restricted_writer_metadata_share_normalized_definitions():
    owner = catalog.observe_catalog(_catalog(), owner=100, actor=100, owner_mode=True)
    writer = catalog.observe_catalog(
        _catalog(actor=101), owner=100, actor=101, owner_mode=False
    )
    assert owner == writer
    assert set(owner) == {"index", "predicate", "triggers"}
    assert "native_baseline_admission" not in owner


@pytest.mark.parametrize(
    "fault",
    (
        "missing",
        "duplicate",
        "unique",
        "valid",
        "ready",
        "live",
        "immediate",
        "primary",
        "exclusion",
        "nulls",
        "expression",
        "predicate",
        "key",
        "key_bool",
        "option_bool",
        "columns",
        "owner",
        "table",
        "opclass",
        "collation",
        "disabled_trigger",
        "trigger_bool",
        "trigger_function",
        "extra_acl",
        "row_security",
    ),
)
def test_index_relation_predicate_flags_append_only_and_acl_drift_deny(fault):
    value = _catalog()
    row = value["indexes"][0]
    if fault == "missing":
        value["indexes"] = []
    elif fault == "duplicate":
        value["indexes"].append(deepcopy(row))
    elif fault in ("unique", "valid", "ready", "live", "immediate"):
        row[fault] = False
    elif fault in ("primary", "exclusion"):
        row[fault] = True
    elif fault == "nulls":
        row["nulls_not_distinct"] = True
    elif fault == "expression":
        row["expressions_is_null"] = False
    elif fault == "predicate":
        row["predicate"] = "true"
    elif fault == "key":
        row["key_attnums"] = [1]
    elif fault == "key_bool":
        row["key_attnums"] = [True]
    elif fault == "option_bool":
        row["key_options"] = [False]
    elif fault == "columns":
        value["columns"][0][2] = 1
    elif fault == "owner":
        row["owner"] = 101
    elif fault == "table":
        row["table_name"] = "qualification_reservations"
    elif fault == "opclass":
        row["opclasses"][0]["name"] = "text_pattern_ops"
    elif fault == "collation":
        row["collations"][0]["name"] = "C"
    elif fault == "disabled_trigger":
        value["triggers"][0]["enabled"] = "D"
    elif fault == "trigger_bool":
        value["triggers"][0]["arguments"] = False
    elif fault == "trigger_function":
        value["triggers"][0]["function_name"] = "other"
    elif fault == "extra_acl":
        value["tables"][0]["acl"].append([101, 100, "SELECT", False])
    elif fault == "row_security":
        value["tables"][0]["row_security"] = True
    with pytest.raises(catalog.NativeCloseIntentCatalogError):
        catalog.observe_catalog(value, owner=100, actor=100, owner_mode=True)


@pytest.mark.parametrize(
    "fault", ("missing_insert", "update", "truncate", "wrong_actor")
)
def test_runtime_writer_requires_exact_select_insert_only(fault):
    value = _catalog(actor=101)
    actor = 101
    if fault == "missing_insert":
        value["tables"][0]["acl"].remove([101, 100, "INSERT", False])
    elif fault == "update":
        value["tables"][0]["acl"].append([101, 100, "UPDATE", False])
    elif fault == "truncate":
        value["tables"][0]["acl"].append([101, 100, "TRUNCATE", False])
    elif fault == "wrong_actor":
        actor = 102
    with pytest.raises(catalog.NativeCloseIntentCatalogError):
        catalog.observe_catalog(value, owner=100, actor=actor, owner_mode=False)
