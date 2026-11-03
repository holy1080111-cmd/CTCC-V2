"""Synthetic metadata mutations for the new55 closure catalog component.

These tests exercise pure component rejection. They do not install a native
seal, authenticate a source, evaluate an account or authorize closure/trading.
"""

from copy import deepcopy

import pytest

from app.domain import collector_current_0055_catalog_v1 as policy
from app.domain import native_filled_flat_closure_schema_v55 as schema
from app.domain import native_leverage_configuration_enforcement_v55 as enforcement
from app.domain import native_zero_fok_closure_schema_v55 as zero_fok


def _component():
    owner, function_oid, table_oid = 16384, 20001, 20002
    function_oids = {
        schema.FUNCTION: function_oid,
        "qualification_ledger_immutable": 20003,
        enforcement.FUNCTION: 20004,
    }
    function = {
        "name": schema.FUNCTION,
        "oid": function_oid,
        "namespace": "public",
        "owner": owner,
        "source": schema.CURRENT_SQL.split("$$")[1],
        "definition": schema.CURRENT_SQL,
        "arguments": "",
        "returns": "trigger",
        "language": "plpgsql",
        "security_definer": False,
        "kind": "f",
        "volatility": "v",
        "parallel": "u",
        "strict": False,
        "leakproof": False,
        "config": ["search_path=pg_catalog, pg_temp"],
        "acl_valid": True,
        "acl": [[0, owner, "EXECUTE", False], [owner, owner, "EXECUTE", False]],
    }
    table = {
        "oid": table_oid,
        "namespace": "public",
        "name": "qualification_reservations",
        "owner": owner,
        "kind": "r",
        "persistence": "p",
        "partition": False,
        "has_subclass": False,
        "rules": False,
        "row_security": False,
        "force_row_security": False,
    }
    triggers = []
    for index, (name, kind, function_name) in enumerate(
        (
            (
                "qualification_reservation_no_delete",
                11,
                "qualification_ledger_immutable",
            ),
            (
                "qualification_reservation_no_truncate",
                34,
                "qualification_ledger_immutable",
            ),
            ("qualification_reservation_update_guard", 19, schema.FUNCTION),
            (enforcement.TRIGGER, 23, enforcement.FUNCTION),
        )
    ):
        triggers.append(
            {
                "oid": 21000 + index,
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
                "definition": "DECLARED SYNTHETIC TRIGGER " + name,
                "function_oid": function_oids[function_name],
                "function_namespace": "public",
                "function_name": function_name,
                "function_owner": owner,
                "function_arguments": 0,
                "function_kind": "f",
            }
        )
    return (
        {"functions": [function], "tables": [table], "triggers": triggers},
        owner,
        {"function_oids": {enforcement.FUNCTION: function_oids[enforcement.FUNCTION]}},
    )


def test_synthetic_component_keeps_only_definition_data():
    value, owner, configuration = _component()
    result = policy._closure55(value, owner=owner, configuration=configuration)
    assert result == {
        "function": schema.CURRENT_SQL,
        "triggers": sorted(
            [[item["name"], item["definition"]] for item in value["triggers"]]
        ),
    }
    assert "native_baseline_admission" not in result
    assert "execution_authority" not in result


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("source", schema.ORIGINAL_SQL.split("$$")[1]),
        pytest.param(
            "source", zero_fok.CURRENT_SQL.split("$$")[1], id="superseded-zero-fok-body"
        ),
        ("owner", 16385),
        ("namespace", "other"),
        ("arguments", "integer"),
        ("security_definer", True),
        ("config", None),
        ("config", ["search_path=public"]),
        ("parallel", "s"),
        ("strict", True),
        ("leakproof", True),
        ("acl_valid", False),
        ("oid", True),
        (
            "acl",
            [
                [0, 16384, "EXECUTE", False],
                [16384, 16384, "EXECUTE", False],
                [16385, 16384, "EXECUTE", False],
            ],
        ),
    ),
)
def test_wrong_closure_function_metadata_denied(field, replacement):
    value, owner, configuration = _component()
    value["functions"][0][field] = deepcopy(replacement)
    with pytest.raises(policy.Current0055CatalogError):
        policy._closure55(value, owner=owner, configuration=configuration)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("owner", 16385),
        ("namespace", "other"),
        ("kind", "v"),
        ("persistence", "t"),
        ("partition", True),
        ("has_subclass", True),
        ("rules", True),
        ("row_security", True),
        ("force_row_security", True),
        ("oid", True),
    ),
)
def test_wrong_actual_reservation_relation_denied(field, replacement):
    value, owner, configuration = _component()
    value["tables"][0][field] = replacement
    with pytest.raises(policy.Current0055CatalogError):
        policy._closure55(value, owner=owner, configuration=configuration)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("enabled", "D"),
        ("kind", 17),
        ("kind", True),
        ("internal", True),
        ("arguments", 1),
        ("argument_bytes", "00"),
        ("columns", "1"),
        ("qual_is_null", False),
        ("constraint_oid", 42),
        ("old_table", "old_rows"),
        ("new_table", "new_rows"),
        ("table_oid", 20003),
        ("function_oid", 20003),
        ("function_namespace", "other"),
        ("function_name", "other"),
        ("function_owner", 16385),
        ("function_arguments", 1),
        ("function_kind", "p"),
        ("definition", ""),
    ),
)
def test_changed_actual_update_trigger_denied(field, replacement):
    value, owner, configuration = _component()
    value["triggers"][2][field] = replacement
    with pytest.raises(policy.Current0055CatalogError):
        policy._closure55(value, owner=owner, configuration=configuration)


@pytest.mark.parametrize(
    "mutation", ("missing", "duplicate", "extra", "missing_function", "extra_function")
)
def test_complete_unique_physical_component_required(mutation):
    value, owner, configuration = _component()
    if mutation == "missing":
        value["triggers"].pop()
    elif mutation == "duplicate":
        value["triggers"][1] = deepcopy(value["triggers"][0])
    elif mutation == "extra":
        value["triggers"].append(deepcopy(value["triggers"][0]))
    elif mutation == "missing_function":
        value["functions"].clear()
    else:
        value["functions"].append(deepcopy(value["functions"][0]))
    with pytest.raises(policy.Current0055CatalogError):
        policy._closure55(value, owner=owner, configuration=configuration)


def test_leverage_enforcement_trigger_must_bind_to_observed_function():
    value, owner, configuration = _component()
    configuration["function_oids"][enforcement.FUNCTION] += 1
    with pytest.raises(policy.Current0055CatalogError):
        policy._closure55(value, owner=owner, configuration=configuration)
