"""Synthetic catalog diagnostics. Never install a synthetic native baseline."""

from copy import deepcopy

import pytest

from app.domain import account_history_original_source_catalog_v1 as policy
from app.domain import account_history_partition_catalog_v1 as parent


def declared_source_catalog():
    owner, namespace = 20000, 2200
    table_oids = {table: 40000 + number for number, table in enumerate(policy.TABLES)}
    table_oids["qualification_account_scopes"] = 41000
    function_oids = {
        name: 42000 + number
        for number, name in enumerate(policy.FUNCTION_SOURCE_SHA256)
    }
    index_oids = {
        (row["table"], row["name"]): 43000 + number
        for number, row in enumerate(policy.INDEXES)
    }
    index_oids["qualification_account_scopes", "pk_qualification_account_scopes"] = (
        44000
    )
    ri_functions = {
        name: 45000 + number
        for number, name in enumerate(
            (
                "RI_FKey_check_ins",
                "RI_FKey_check_upd",
                "RI_FKey_restrict_del",
                "RI_FKey_noaction_upd",
            )
        )
    }
    constraints = []
    for spec in policy.CONSTRAINTS:
        index_oid = None
        if spec["kind"] in ("p", "u"):
            index_oid = index_oids[spec["table"], spec["name"]]
        elif spec["kind"] == "f":
            index_oid = index_oids[spec["target"], "pk_" + spec["target"]]
        constraints.append(
            {
                "table": spec["table"],
                "name": spec["name"],
                "table_oid": table_oids[spec["table"]],
                "target_oid": None
                if spec["target"] is None
                else table_oids[spec["target"]],
                "index_oid": index_oid,
                "index_expected_oid": index_oid,
            }
        )
    ri_triggers = []
    for row in policy.EXPECTED_STRUCTURE["ri_triggers"]:
        spec = next(
            spec
            for spec in policy.CONSTRAINTS
            if spec["table"] == row["table"] and spec["name"] == row["constraint"]
        )
        relation = spec["table"] if row["relation"] == "child" else spec["target"]
        other = spec["target"] if row["relation"] == "child" else spec["table"]
        ri_triggers.append(
            {
                "table": row["table"],
                "constraint": row["constraint"],
                "function": row["function"],
                "function_oid": ri_functions[row["function"]],
                "function_expected_oid": ri_functions[row["function"]],
                "relation_oid": table_oids[relation],
                "expected_relation_oid": table_oids[relation],
                "other_oid": table_oids[other],
                "expected_other_oid": table_oids[other],
            }
        )
    value = {
        "schema_version": policy.SCHEMA,
        "structure": deepcopy(policy.EXPECTED_STRUCTURE),
        "identities": {
            "tables": [
                {
                    "name": name,
                    "oid": table_oids[name],
                    "namespace_oid": namespace,
                    "owner_oid": owner,
                }
                for name in policy.TABLES
            ],
            "functions": [
                {
                    "name": name,
                    "oid": function_oids[name],
                    "namespace_oid": namespace,
                    "owner_oid": owner,
                    "catalog_xmin": "1234",
                }
                for name in policy.FUNCTION_SOURCE_SHA256
            ],
            "constraints": constraints,
            "triggers": [
                {
                    "table": row["table"],
                    "name": row["name"],
                    "table_oid": table_oids[row["table"]],
                    "function_oid": function_oids[row["function"]],
                }
                for row in policy.TRIGGERS
            ],
            "ri_triggers": ri_triggers,
        },
        "definitions": {},
    }
    for key, specs in (
        ("constraints", policy.CONSTRAINTS),
        ("indexes", policy.INDEXES),
        ("triggers", policy.TRIGGERS),
    ):
        value["definitions"][key] = [
            {
                "table": row["table"],
                "name": row["name"],
                "definition": "SYNTHETIC_NOT_NATIVE:" + row["name"],
                **(
                    {"expression": "SYNTHETIC_CHECK" if row["kind"] == "c" else None}
                    if key == "constraints"
                    else {}
                ),
            }
            for row in specs
        ]
    value["definitions"]["functions"] = [
        {"name": name, "definition": "SYNTHETIC_NOT_NATIVE:" + name}
        for name in policy.FUNCTION_SOURCE_SHA256
    ]
    previous = {
        "tables": [{"owner_oid": owner, "namespace_oid": namespace}],
        "parents": [{"name": name, "oid": oid} for name, oid in table_oids.items()],
    }
    return value, previous


def test_legacy_source_contract_is_complete_and_sealed_default_rejects_synthetic():
    value, previous = declared_source_catalog()
    assert len(policy.TABLES) == 5
    assert len(policy.CONSTRAINTS) == 27
    assert len(policy.TRIGGERS) == 16
    assert len(policy.EXPECTED_STRUCTURE["ri_triggers"]) == 20
    assert len(policy.INDEXES) == 9
    assert len(policy.FUNCTION_SOURCE_SHA256) == 5
    observed = policy.verify_source_catalog(
        value, previous, require_native_baseline=False
    )
    assert len(observed) == 64
    assert policy.PG17_NATIVE_BASELINE_SHA256 == (
        "5a20251836bfbbf0523d050e7eb475f0a7a74b39b1ba5532130b76dcf6e3c780"
    )
    assert policy.PG17_NATIVE_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    with pytest.raises(
        parent.AccountHistoryPartitionError,
        match="^original_frame_source_deparser_drift$",
    ):
        policy.verify_source_catalog(value, previous)


@pytest.mark.parametrize(
    "change",
    [
        "insert_disabled",
        "immutable_missing",
        "truncate_changed",
        "function_body",
        "function_oid",
        "function_owner",
        "constraint_missing",
        "constraint_unvalidated",
        "foreign_target",
        "index_invalid",
        "index_binding",
        "ri_disabled",
        "ri_missing",
        "ri_oid",
        "column",
        "extra_column",
        "rules",
        "partition",
        "inheritance",
        "rls",
        "fake_xmin",
    ],
)
def test_original_source_drift_denies_before_native_seal_decision(change):
    value, previous = declared_source_catalog()
    structure, identities = value["structure"], value["identities"]
    if change == "insert_disabled":
        structure["triggers"][0]["enabled"] = "D"
    elif change == "immutable_missing":
        structure["triggers"] = [
            row
            for row in structure["triggers"]
            if row["name"] != "account_capture_event_immutable"
        ]
    elif change == "truncate_changed":
        next(
            row
            for row in structure["triggers"]
            if row["name"] == "account_capture_event_no_truncate"
        )["type"] = 32
    elif change == "function_body":
        structure["functions"][0]["source_sha256"] = "f" * 64
    elif change == "function_oid":
        identities["functions"][0]["oid"] += 100
    elif change == "function_owner":
        identities["functions"][0]["owner_oid"] += 100
    elif change == "constraint_missing":
        structure["constraints"].pop()
    elif change == "constraint_unvalidated":
        structure["constraints"][0]["validated"] = False
    elif change == "foreign_target":
        next(row for row in identities["constraints"] if row["target_oid"] is not None)[
            "target_oid"
        ] += 100
    elif change == "index_invalid":
        structure["indexes"][0]["valid"] = False
    elif change == "index_binding":
        next(row for row in identities["constraints"] if row["index_oid"] is not None)[
            "index_oid"
        ] += 100
    elif change == "ri_disabled":
        structure["ri_triggers"][0]["enabled"] = "D"
    elif change == "ri_missing":
        structure["ri_triggers"].pop()
    elif change == "ri_oid":
        identities["ri_triggers"][0]["function_oid"] += 100
    elif change == "column":
        structure["columns"][0]["nullable"] = True
    elif change == "extra_column":
        structure["columns"].append(deepcopy(structure["columns"][0]))
    elif change == "rules":
        structure["tables"][0]["has_rules"] = True
    elif change == "partition":
        structure["tables"][0]["partition"] = True
    elif change == "inheritance":
        structure["tables"][0]["inherited"] = True
    elif change == "rls":
        structure["tables"][0]["rls"] = True
    else:
        identities["functions"][0]["catalog_xmin"] = True
    with pytest.raises(parent.AccountHistoryPartitionError):
        policy.verify_source_catalog(value, previous, require_native_baseline=False)


def test_changed_check_expression_is_measured_not_accepted_as_a_new_seal():
    value, previous = declared_source_catalog()
    original = policy.verify_source_catalog(
        value, previous, require_native_baseline=False
    )
    row = next(
        row
        for row in value["definitions"]["constraints"]
        if row["expression"] is not None
    )
    row["expression"] = "true"
    changed = policy.verify_source_catalog(
        value, previous, require_native_baseline=False
    )
    assert changed != original
    # A diagnostic hash is never installed as an observed baseline.
    assert policy.PG17_NATIVE_BASELINE_SHA256 == (
        "5a20251836bfbbf0523d050e7eb475f0a7a74b39b1ba5532130b76dcf6e3c780"
    )
    assert policy.PG17_NATIVE_BASELINE_STATUS == "SEALED_FROM_REVIEWED_NATIVE_PG17"
    with pytest.raises(
        parent.AccountHistoryPartitionError,
        match="^original_frame_source_deparser_drift$",
    ):
        policy.verify_source_catalog(value, previous)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("PG17_NATIVE_BASELINE_SHA256", None),
        ("PG17_NATIVE_BASELINE_STATUS", "PENDING_ACTUAL_ISOLATED_PG_VALIDATION"),
    ],
)
def test_missing_reviewed_source_seal_still_denies_default(monkeypatch, field, value):
    catalog, previous = declared_source_catalog()
    monkeypatch.setattr(policy, field, value)
    with pytest.raises(
        parent.AccountHistoryPartitionError,
        match="^original_frame_source_native_baseline_pending$",
    ):
        policy.verify_source_catalog(catalog, previous)
