"""Complete current whitelist/CRUD audit; no application or database imports."""

import ast
import copy

import pytest

from scripts import paper58_local_route_scope_v1 as subject
from scripts.paper58_source_coverage_v1 import RUNTIME_TABLE_PRIVILEGES


def test_all_sixteen_real_whitelist_routes_and_two_redirects_are_accounted_for():
    value = subject.route_scope_v1()
    assert len(value["routes"]) == 16 and len(value["redirects"]) == 2
    assert value["total_table_cells"] == 21 and value["total_tables"] == 9
    assert (
        value["startup_admitted"]
        is value["native_trading_execution_authority"]
        is False
    )


def test_original_repository_crud_union_exactly_matches_runtime_plan():
    methods = subject.repository_crud_v1()
    actual = {cell for cells in methods.values() for cell in cells}
    expected = {
        (table, action)
        for table, actions in RUNTIME_TABLE_PRIVILEGES
        for action in actions
    }
    assert actual == expected and len(actual) == 20
    assert methods["save_paper_state"] == ()
    assert set(methods["load_paper_state"]) == {
        ("paper_account_state", "SELECT"),
        ("paper_order_state", "SELECT"),
        ("paper_position_state", "SELECT"),
    }
    assert ("orchestrator_run_state", "SELECT") in methods["counts"]
    assert ("orchestrator_fingerprint_state", "SELECT") in methods["counts"]
    assert not any(
        table.startswith(("analysis_", "native_", "qualification_"))
        for table, _action in actual
    )


def test_offline_analysis_is_not_live_analysis_or_report_database_authority():
    value = subject.route_scope_v1()
    assert value["analysis_snapshot_sql_operations"] == ()
    assert value["analysis_table_grants_required"] == ()
    assert value["separate_analysis_actor_required_for_current_whitelist"] is False
    paths = {path for path, _method, _file, _handler, _scope in value["routes"]}
    assert "/api/analysis/snapshot" in paths
    assert "/api/analysis/{symbol:path}" not in paths
    assert "/api/dashboard/snapshot" not in paths
    assert value["generic_readiness_is_reachability_only"] is True
    assert value["paper_readiness_requires_owned_recovery_context"] is True


def test_new_route_cannot_silently_expand_reviewed_scope(monkeypatch):
    original = subject._tree

    def changed(path):
        tree = copy.deepcopy(original(path))
        if path == "app/main.py":
            statement = next(
                node
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id == "_LOCAL_ANALYSIS_ROUTES"
                    for target in node.targets
                )
            )
            statement.value.keys.append(ast.Constant("/api/analysis/{symbol:path}"))
            statement.value.values.append(ast.Constant("GET"))
        return tree

    monkeypatch.setattr(subject, "_tree", changed)
    with pytest.raises(ValueError, match="paper58_local_route_scope_changed"):
        subject.route_scope_v1()


def test_new_sql_or_provider_callback_in_an_allowed_handler_requires_review(
    monkeypatch,
):
    original = subject._function

    def changed(path, name):
        node = copy.deepcopy(original(path, name))
        if (path, name) == ("app/api/routers/analysis.py", "analyze_imported_snapshot"):
            node.body.append(
                ast.parse("await session.execute('unexpected_sql')").body[0]
            )
        return node

    monkeypatch.setattr(subject, "_function", changed)
    with pytest.raises(ValueError, match="paper58_local_route_scope_changed"):
        subject.route_scope_v1()


@pytest.mark.parametrize(
    "path", ("app/api/routers/analysis.py", "app/paper/service.py")
)
def test_changed_import_or_singleton_binding_cannot_keep_old_route_review(
    monkeypatch, path
):
    original = subject._tree

    def changed(actual):
        tree = copy.deepcopy(original(actual))
        if actual == path:
            tree.body.insert(
                0, ast.parse("from foreign_sql_provider import replacement").body[0]
            )
        return tree

    monkeypatch.setattr(subject, "_tree", changed)
    with pytest.raises(ValueError, match="paper58_local_route_scope_changed"):
        subject.route_scope_v1()
