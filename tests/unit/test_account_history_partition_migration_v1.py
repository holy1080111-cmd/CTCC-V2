"""Synthetic policy falsifications; no native catalog, account or PG acceptance."""

import copy
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.database.models.account_history_partition import (
    DemoAccountHistoryNativeMember,
    DemoAccountHistoryPartitionCheckpoint,
)
from app.database.repositories import account_history_partition as repository
from app.domain import account_history_partition_catalog_v1 as policy
from scripts import verify_account_history_partition_schema_v1 as adapter

ROOT = Path(__file__).resolve().parents[2]


def synthetic_catalog():
    """Declared catalog-shaped data, never an observed or sealable PG catalog."""
    tables, columns, constraints, indexes, functions, triggers = [], [], [], [], [], []
    parents = [
        {
            "name": name,
            "oid": 100 + index,
            "namespace": "public",
            "namespace_oid": 50,
            "owner_oid": 10,
            "kind": "r",
        }
        for index, name in enumerate(policy.PARENTS)
    ]
    parent_ids = {row["name"]: row["oid"] for row in parents}
    for offset, (table, spec) in enumerate(policy.TABLE_SPECS.items()):
        oid = 200 + offset
        tables.append(
            {
                "name": table,
                "oid": oid,
                "owner_oid": 10,
                "namespace": "public",
                "namespace_oid": 50,
                "owner_superuser": True,
                "kind": "r",
                "persistence": "p",
                "rls": False,
                "force_rls": False,
                "public_access": False,
                "acl_valid": True,
                "table_access": False,
            }
        )
        columns.extend(
            {
                "table": table,
                "name": name,
                "type": dtype,
                "nullable": nullable,
                "position": index,
                "default": "clock_timestamp()" if name == "recorded_at" else None,
                "generated": "",
                "identity": "",
            }
            for index, (name, dtype, nullable) in enumerate(spec["columns"], 1)
        )
        declarations = [("pk_" + table, "p", list(spec["primary_key"]), None, None)]
        declarations += [(name, "c", None, None, None) for name in spec["checks"]]
        declarations += [
            (name, "u", list(keys), None, None) for name, keys in spec["unique"]
        ]
        declarations += [
            (name, "f", list(keys), target, list(target_keys))
            for name, keys, target, target_keys in spec["foreign_keys"]
        ]
        declarations += [
            (name, "t", None, None, None)
            for name, selected, _, _, _, deferred in policy.TRIGGER_SPECS
            if selected == table and deferred
        ]
        for name, kind, keys, target, target_keys in declarations:
            constraints.append(
                {
                    "table": table,
                    "name": name,
                    "kind": kind,
                    "columns": keys,
                    "table_oid": oid,
                    "target": target,
                    "target_columns": target_keys,
                    "target_oid": parent_ids.get(target),
                    "target_namespace": "public" if target else None,
                    "validated": True,
                    "deferrable": kind == "t",
                    "deferred": kind == "t",
                    "delete_action": "r" if kind == "f" else " ",
                    "definition": "TRIGGER DEFERRABLE INITIALLY DEFERRED"
                    if kind == "t"
                    else "SYNTHETIC_DECLARATION_NOT_NATIVE_DEPARSE",
                }
            )
        indexes += [
            {
                "table": table,
                "name": name,
                "columns": list(keys),
                "primary": primary,
                "unique": True,
                "method": "btree",
                "valid": True,
                "ready": True,
                "predicate": None,
                "expressions": None,
                "definition": "SYNTHETIC_NOT_NATIVE_INDEX",
            }
            for name, keys, primary in [
                ("pk_" + table, spec["primary_key"], True),
                *((name, keys, False) for name, keys in spec["unique"]),
            ]
        ]
    for offset, (name, _, returns, volatility, body) in enumerate(
        policy.FUNCTION_SPECS
    ):
        functions.append(
            {
                "name": name,
                "oid": 300 + offset,
                "namespace_oid": 50,
                "namespace": "public",
                "owner_oid": 10,
                "source": policy._body(body),
                "returns": returns,
                "arguments": policy.FUNCTION_SIGNATURES[name],
                "language": "plpgsql",
                "security_definer": True,
                "volatility": volatility.lower()[0],
                "config": ["search_path=pg_catalog, public, pg_temp"],
                "kind": "f",
                "parallel": "u",
                "leakproof": False,
                "strict": False,
                "public_execute": False,
                "acl_valid": True,
                "can_execute": name.endswith(("_append", "_read")),
            }
        )
    by_name = {row["name"]: row for row in functions}
    for name, table, events, level, function, deferred in policy.TRIGGER_SPECS:
        kind = (
            (1 if level == "ROW" else 0)
            | (2 if events.startswith("BEFORE") else 0)
            | sum(
                bit
                for event, bit in (
                    ("INSERT", 4),
                    ("DELETE", 8),
                    ("UPDATE", 16),
                    ("TRUNCATE", 32),
                )
                if event in events
            )
        )
        triggers.append(
            {
                "table": table,
                "name": name,
                "function": function,
                "function_oid": by_name[function]["oid"],
                "function_namespace_oid": 50,
                "function_namespace": "public",
                "enabled": "O",
                "type": kind,
                "deferrable": deferred,
                "deferred": deferred,
                "constraint": deferred,
                "arguments": "",
                "when": None,
            }
        )
    return {
        "schema_version": policy.CATALOG_SCHEMA,
        "server": {
            "version_num": "170011",
            "current_user": "synthetic_runtime",
            "session_user": "synthetic_runtime",
            "role_setting": "none",
            "timezone": "UTC",
            "search_path": "pg_catalog, public, pg_temp",
            "isolation": "read committed",
            "synchronous_commit": "on",
            "fsync": "on",
            "full_page_writes": "on",
            "replication_role": "origin",
            "in_recovery": False,
        },
        "role": {
            "oid": 20,
            "name": "synthetic_runtime",
            "can_login": True,
            "superuser": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
            "bypassrls": False,
            "other_membership": False,
            "can_create": False,
        },
        "parents": parents,
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "functions": functions,
        "triggers": triggers,
        "acl_rows": [],
        "column_acl_valid": True,
    }


def test_cold_host_policy_aliases_work_with_runtime_imports_forbidden():
    program = """
import importlib.abc,sys
sys.path.insert(0,sys.argv[1])
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname=='sqlalchemy' or fullname.startswith(('sqlalchemy.','app.database.','app.trade_qualification.')):
   raise AssertionError('runtime import crossed cold boundary')
sys.meta_path.insert(0,Block())
from app.domain import account_history_partition_catalog_v1 as p
from scripts import verify_account_history_partition_schema_v1 as a
assert a.expected_account_history_partition_schema is p.expected_account_history_partition_schema
assert a.verify_account_history_partition_catalog is p.verify_account_history_partition_catalog
assert a.AccountHistoryPartitionError is p.AccountHistoryPartitionError
before=(p.PG17_DEPARSED_BASELINE,p.PG17_DEPARSED_BASELINE_STATUS)
assert a.expected_account_history_partition_schema()['revision']=='0050'
try:a.verify_account_history_partition_catalog({})
except p.AccountHistoryPartitionError as e:assert str(e)=='account_history_partition_catalog_shape'
else:raise AssertionError('malformed accepted')
assert before==(p.PG17_DEPARSED_BASELINE,p.PG17_DEPARSED_BASELINE_STATUS)
assert 'sqlalchemy' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", program, str(ROOT)],
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
        env={
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR")
            if key in os.environ
        },
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_same_object_policy_owner_and_structural_diagnostics_cannot_seal():
    assert (
        repository.expected_account_history_partition_schema
        is adapter.expected_account_history_partition_schema
        is policy.expected_account_history_partition_schema
    )
    assert (
        repository.verify_account_history_partition_catalog
        is adapter.verify_account_history_partition_catalog
        is policy.verify_account_history_partition_catalog
    )
    assert (
        repository.AccountHistoryPartitionError
        is adapter.AccountHistoryPartitionError
        is policy.AccountHistoryPartitionError
    )
    assert (
        repository.verify_account_history_partition_catalog.__globals__
        is policy.__dict__
    )
    before = policy.PG17_DEPARSED_BASELINE, policy.PG17_DEPARSED_BASELINE_STATUS
    observed = synthetic_catalog()
    assert (
        policy.verify_account_history_partition_catalog(
            observed, require_native_baseline=False
        )
        == policy.expected_account_history_partition_schema()
    )
    if before[0] is None:
        with pytest.raises(
            policy.AccountHistoryPartitionError,
            match="^account_history_partition_native_baseline_pending$",
        ):
            policy.verify_account_history_partition_catalog(observed)
    else:
        with pytest.raises(
            policy.AccountHistoryPartitionError,
            match="^account_history_partition_native_baseline_drift$",
        ):
            policy.verify_account_history_partition_catalog(observed)
    assert before == (
        policy.PG17_DEPARSED_BASELINE,
        policy.PG17_DEPARSED_BASELINE_STATUS,
    )


@pytest.mark.parametrize(
    "category,key",
    [
        ("tables", "oid"),
        ("tables", "owner_oid"),
        ("tables", "namespace_oid"),
        ("parents", "oid"),
        ("functions", "oid"),
        ("functions", "namespace_oid"),
        ("functions", "owner_oid"),
        ("triggers", "function_oid"),
        ("triggers", "function_namespace_oid"),
    ],
)
def test_catalog_refuses_oid_strings_without_normalizing_raw(category, key):
    catalog = synthetic_catalog()
    catalog[category][0][key] = str(catalog[category][0][key])
    before = copy.deepcopy(catalog)
    with pytest.raises(policy.AccountHistoryPartitionError):
        policy.verify_account_history_partition_catalog(
            catalog, require_native_baseline=False
        )
    assert catalog == before


@pytest.mark.parametrize(
    "change",
    [
        "same_name_other_schema_trigger",
        "foreign_key_other_schema",
        "public_execute",
        "table_grant",
        "column_grant",
        "function_source",
        "unsafe_path",
        "missing_constraint",
        "duplicate_constraint",
        "unvalidated_constraint",
        "extra_function",
        "malformed_acl",
    ],
)
def test_catalog_identity_privilege_and_complete_inventory_falsifications(change):
    c = synthetic_catalog()
    if change == "same_name_other_schema_trigger":
        c["triggers"][0].update(
            function_oid=999, function_namespace="foreign", function_namespace_oid=998
        )
    elif change == "foreign_key_other_schema":
        next(row for row in c["constraints"] if row["kind"] == "f").update(
            target_namespace="foreign", target_oid=999
        )
    elif change == "public_execute":
        c["functions"][0]["public_execute"] = True
    elif change in {"table_grant", "column_grant"}:
        c["acl_rows"].append(
            {
                "kind": change.removesuffix("_grant"),
                "object": policy.TABLES[0],
                "owner_oid": 10,
                "grantee_oid": 20,
                "privilege": "SELECT",
                "grantable": False,
            }
        )
    elif change == "function_source":
        c["functions"][0]["source"] += "\n-- drift"
    elif change == "unsafe_path":
        c["functions"][0]["config"] = ["search_path=pg_catalog, public"]
    elif change == "missing_constraint":
        c["constraints"].pop()
    elif change == "duplicate_constraint":
        c["constraints"].append(c["constraints"][0].copy())
    elif change == "unvalidated_constraint":
        c["constraints"][0]["validated"] = False
    elif change == "extra_function":
        c["functions"].append(
            {**c["functions"][0], "name": "account_history_partition_v1_unapproved"}
        )
    else:
        c["tables"][0]["acl_valid"] = False
    with pytest.raises(policy.AccountHistoryPartitionError):
        policy.verify_account_history_partition_catalog(
            c, require_native_baseline=False
        )


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "superuser",
        "set_role",
        "membership",
        "missing_entry",
        "helper_execute",
        "column_access",
    ],
)
def test_runtime_gate_is_independent_of_structural_admin_diagnostics(change):
    c = synthetic_catalog()
    if change == "owner":
        c["role"]["oid"] = 10
    elif change == "superuser":
        c["role"]["superuser"] = True
    elif change == "set_role":
        c["server"]["role_setting"] = "other"
    elif change == "membership":
        c["role"]["other_membership"] = True
    elif change == "missing_entry":
        next(row for row in c["functions"] if row["name"].endswith("_append"))[
            "can_execute"
        ] = False
    elif change == "helper_execute":
        c["functions"][0]["can_execute"] = True
    else:
        c["tables"][0]["table_access"] = True
    with pytest.raises(
        policy.AccountHistoryPartitionError,
        match="^account_history_partition_role_denied$",
    ):
        policy.verify_runtime_role(c)


def test_original_migrations_remain_exact_and_model_matches_sole_policy():
    assert (
        hashlib.sha256(
            (
                ROOT / "migrations/versions/0048_controlled_writer_coverage.py"
            ).read_bytes()
        ).hexdigest()
        == "d9576bf85a16d4b7fd81a28df4631ce061b8ef6747ec99779215922bdf47240b"
    )
    assert (
        hashlib.sha256(
            (
                ROOT
                / "migrations/versions/0049_controlled_writer_pair_guard_definer.py"
            ).read_bytes()
        ).hexdigest()
        == "788555e40447ef14d7957a348927158dc359463b13313afbbe08867116ded33f"
    )
    for model in (
        DemoAccountHistoryNativeMember,
        DemoAccountHistoryPartitionCheckpoint,
    ):
        spec = policy.TABLE_SPECS[model.__tablename__]
        assert [
            (column.name, column.nullable) for column in model.__table__.columns
        ] == [(name, nullable) for name, _, nullable in spec["columns"]]
        assert (
            tuple(column.name for column in model.__table__.primary_key)
            == spec["primary_key"]
        )
        assert {
            constraint.name for constraint in model.__table__.foreign_key_constraints
        } == {name for name, *_ in spec["foreign_keys"]}


def test_sqlalchemy_bind_parser_sees_only_named_entry_operands():
    assert set(text(repository._APPEND_SQL)._bindparams) == {
        "environment",
        "account_id",
        "currency",
        "head",
        "previous",
        "registration",
        "checkpoint",
        "control_revision",
        "account_revision",
        "ledger_revision",
        "expires",
    }
    assert set(text(repository._READ_SQL)._bindparams) == {
        "environment",
        "account_id",
        "currency",
    }
    assert not text(policy.CATALOG_SQL)._bindparams
    assert all(
        not text(statement)._bindparams
        for statement in (
            *policy.TABLE_SQL,
            *policy.FUNCTION_SQL.values(),
            *policy.TRIGGER_SQL,
        )
    )
