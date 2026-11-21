"""Source inventory only: no catalog, startup, role or recovery acceptance."""

import ast
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import Column, Integer, Table

from scripts import paper58_source_coverage_v1 as subject


def test_complete_current58_relation_metadata_is_present_without_calling_it_admission():
    value = subject.source_coverage_v1()
    assert tuple(item["name"] for item in value["relations"]) == subject.RELATIONS
    assert all(
        item["columns"] and item["source_create_table"] for item in value["relations"]
    )
    assert all(
        set(item)
        == {
            "name",
            "model",
            "source",
            "source_sha256",
            "columns",
            "constraints",
            "indexes",
            "source_create_table",
        }
        for item in value["relations"]
    )
    assert value["source_head"] == "0058" and value["composition_work_remaining"]
    assert value["runtime_observer_implemented"] is False
    assert value["startup_admitted"] is value["execution_authority"] is False
    assert value["database_connections"] == 0
    assert not any(
        path.startswith("app/main") for path in value["migration_and_domain_sources"]
    )


def test_exact_twenty_table_cells_preserve_recovery_reads_without_automation_writes():
    grants = dict(subject.TABLE_PRIVILEGES)
    assert len(grants) == 8 and sum(map(len, grants.values())) == 20
    assert set(grants) <= set(subject.RELATIONS)
    assert (
        grants["orchestrator_run_state"]
        == grants["orchestrator_fingerprint_state"]
        == ("SELECT",)
    )
    assert grants["audit_logs"] == grants["system_events"] == ("SELECT", "INSERT")
    assert not any(name.startswith(("native_", "qualification_")) for name in grants)


def test_client_python_defaults_are_distinct_from_server_defaults():
    table = next(
        item
        for item in subject.source_coverage_v1()["relations"]
        if item["name"] == "paper_account_state"
    )
    columns = {item["name"]: item for item in table["columns"]}
    assert columns["id"]["python_default_present"] is True
    assert columns["id"]["server_default"] is None
    assert columns["persisted_at"]["server_default"] == "now()"
    assert columns["persisted_at"]["python_onupdate_present"] is True


def test_fetched_orm_marker_is_not_mistaken_for_a_null_native_sql_default():
    value = subject.source_coverage_v1()
    markers = [
        column["server_default"]
        for table in value["relations"]
        for column in table["columns"]
        if type(column["server_default"]) is dict
    ]
    assert markers and all(
        item == {"fetched_value_source_marker": True, "native_spec_required": True}
        for item in markers
    )


def test_repeated_audit_never_mutates_genuine_model_constraint_compilation_rules():
    from app.database.base import Base

    original = [
        (constraint, constraint._create_rule)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
    ]
    first, second = subject.source_coverage_v1(), subject.source_coverage_v1()
    assert first == second
    assert all(constraint._create_rule is rule for constraint, rule in original)
    assert any(
        "PRIMARY KEY" in table["source_create_table"] for table in first["relations"]
    )


def test_extra_mutable_metadata_relation_cannot_become_a_source_roster_member():
    from app.database.base import Base

    foreign = Table(
        "unexpected_paper_source_relation",
        Base.metadata,
        Column("id", Integer, primary_key=True),
    )
    try:
        with pytest.raises(ValueError, match="paper58_source_coverage_incomplete"):
            subject.source_coverage_v1()
    finally:
        Base.metadata.remove(foreign)


def test_all_reuse_and_recovery_pointers_exist_without_importing_role_observers():
    for _scope, path, symbol, _restriction in subject.SOURCE_VALIDATORS:
        tree = ast.parse((subject.ROOT / path).read_bytes())
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == symbol
            for node in tree.body
        )
    for path, owner, method in subject.RECOVERY_SOURCES:
        tree = ast.parse((subject.ROOT / path).read_bytes())
        cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == owner
        )
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method
            for node in cls.body
        )
    assert not any("_observe_readonly" == row[2] for row in subject.SOURCE_VALIDATORS)


def test_migration_graph_and_all_declaration_sites_keep_dynamic_override_gaps_explicit():
    value = subject.source_coverage_v1()
    migrations = [
        path
        for path in value["migration_and_domain_sources"]
        if path.startswith("migrations/versions/")
    ]
    assert len(migrations) == 58
    sites = value["sql_definition_sites"]
    assert {"FUNCTION", "TRIGGER"} <= {item["kind"] for item in sites}
    assert any(
        item["name"] == "native_live_controlled_close_prepare_v1" for item in sites
    )
    assert all(item["effective_override_resolution_required"] is True for item in sites)
    assert any(item["dynamic_name_requires_spec_expansion"] for item in sites)


def test_source_inventory_child_refuses_runtime_and_service_imports():
    code = """
import importlib.abc,json,sys
sys.path.insert(0,sys.argv[1])
class NoRuntime(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in ('app.main','app.database.session','app.config.settings','app.paper.service'):
   raise AssertionError('source_inventory_imported_runtime')
sys.meta_path.insert(0,NoRuntime())
from scripts.paper58_source_coverage_v1 import source_coverage_v1
v=source_coverage_v1()
print(json.dumps({'relations':len(v['relations']),'database_connections':v['database_connections'],'startup_admitted':v['startup_admitted']}))
"""
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "SystemDrive", "TEMP", "TMP", "PATH")
        if key in os.environ
    }
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(subject.ROOT)],
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0 and child.stderr == b""
    assert json.loads(child.stdout) == {
        "relations": len(subject.RELATIONS),
        "database_connections": 0,
        "startup_admitted": False,
    }
