"""UNRUN current0052 wiring contracts; no native or complete-union fixture.

Only source ASTs and stage-name grammar are inspected. A positive stage-name
sequence is not a positive native catalog, CI run, migration or union result.
"""

import ast
import inspect
import textwrap

import pytest

from scripts import original_history_0052_toolchain as current
from scripts import verify_final_hermetic as producer
from scripts import verify_linux_shard_union as consumer

NEW_STAGES = (
    "migration-original-history-frame-native-catalog",
    "migration-original-history-frame-empty-before",
    "migration-original-history-frame-empty-downgrade",
    "migration-original-history-frame-empty-readback",
)


def _tree(function):
    return ast.parse(textwrap.dedent(inspect.getsource(function)))


def _container_commands(function):
    commands = {}
    for node in ast.walk(_tree(function)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "container"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and isinstance(node.args[1], ast.List)
        ):
            commands[node.args[0].value] = node.args[1].elts
    return commands


def test_current_producer_measures0052_then_true0051_absence_and_old_parent():
    commands = _container_commands(producer.main)
    capture = commands[NEW_STAGES[0]]
    assert capture[2].id == "ORIGINAL_FRAME_INSTALLATION_NATIVE_CATALOG_CAPTURE_0052"
    before = commands[NEW_STAGES[1]]
    assert before[2].id == "MIGRATION_REVISION_PROBE_0052"
    assert before[3].value == "0052"
    down = commands[NEW_STAGES[2]]
    assert [node.value for node in down] == ["alembic", "downgrade", "0051"]
    absent = commands[NEW_STAGES[3]]
    assert absent[2].id == "ORIGINAL_FRAME_ABSENT_PROBE_0051"
    old_parent = commands["migration-prospective-cutover-empty-before"]
    assert old_parent[2].id == "MIGRATION_REVISION_PROBE_0051"
    assert old_parent[3].value == "0051"
    for stage in ("migration-reupgrade-readback", "migration-denial-readback"):
        assert commands[stage][2].id == "MIGRATION_REVISION_PROBE_0052"
        assert commands[stage][3].value == "0052"


def test_current_capture_sources_are_new0052_while_historical_probes_stay_versioned():
    commands = _container_commands(producer.main)
    for stage, name in (
        (
            "migration-prospective-cutover-native-catalog",
            "PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE_0052",
        ),
        (
            "migration-history-partition-native-catalog",
            "HISTORY_PARTITION_NATIVE_CATALOG_CAPTURE_0052",
        ),
        ("migration-coverage-native-catalog", "COVERAGE_NATIVE_CATALOG_CAPTURE_0052"),
    ):
        assert commands[stage][2].id == name
    assert "before.head!='0051'" in producer.PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE
    assert (
        "before.head!='0052'" in current.PROSPECTIVE_CUTOVER_NATIVE_CATALOG_CAPTURE_0052
    )


@pytest.mark.parametrize("omitted", NEW_STAGES)
def test_union_stage_grammar_requires_each_new0052_measurement(omitted):
    names = (*consumer.COMMON_STAGES, "linux-shard-0")
    assert all(names.count(name) == 1 for name in NEW_STAGES)
    consumer._verify_complete_stage_sequence(
        [{"name": name} for name in names], "shard", 0
    )
    with pytest.raises(
        ValueError, match="^linux_union_stage_inventory_or_order_invalid$"
    ):
        consumer._verify_complete_stage_sequence(
            [{"name": name} for name in names if name != omitted], "shard", 0
        )


def test_union_uses_native_observed0052_frame_verifier_and_exact_current_helpers():
    tree = _tree(consumer.verify_union)
    invoked = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {
        "verify_original_frame_installation_native_catalog_capture_0052",
        "verify_prospective_cutover_native_catalog_capture_0052",
        "verify_history_partition_native_catalog_capture_0052",
        "verify_coverage_native_catalog_capture_0052",
        "verify_current_0052_downgrade_denial_log",
        "verify_history_partition_migration_receipt_0052",
        "history_partition_nonempty_downgrade_readback_0052",
    } <= invoked
    assert (
        "verify_original_frame_installation_native_catalog_capture_0051" not in invoked
    )


def test_refusal_dispatch_adds_only_exact0052_and_keeps_historical0051():
    tree = _tree(producer.Run.command)
    allowed = [
        tuple(item.value for item in node.comparators[0].elts)
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "refusal_source_head"
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Tuple)
    ]
    assert allowed == [(None, "0051", "0052")]
    assert all("0053" not in group for group in allowed)


def test_new_owner_capture_invokes_strict_API_twice_without_seal_off_parameter():
    tree = ast.parse(current.ORIGINAL_FRAME_INSTALLATION_NATIVE_CATALOG_CAPTURE_0052)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "verify_owner_installation_catalog"
    ]
    assert len(calls) == 2
    assert all(not call.keywords for call in calls)


def test_new_owner_capture_rereads_actual_database_head_before_transaction_exit():
    source = current.ORIGINAL_FRAME_INSTALLATION_NATIVE_CATALOG_CAPTURE_0052
    tree = ast.parse(source)
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_sync"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "database_head"
    ]
    assert len(reads) == 2
    assert "final_head!=head" in source
    assert "'database_head':final_head" in source


@pytest.mark.parametrize(
    "path",
    [
        "tests/integration/test_account_history_original_frame_repository_v1.py",
        "tests/integration/test_account_history_original_native_read_v1.py",
        "tests/integration/test_original_frame_owner_installation_v1.py",
    ],
)
def test_new_actual_pg_modules_have_exactly_one_pg_owner_and_no_nondb_shard(path):
    from pathlib import PurePosixPath

    from scripts import verify_linux_shard as selection
    from scripts.verify_windows_regression import POSTGRES_INTEGRATION

    assert path in selection.POSTGRES_TEST_PATHS
    assert PurePosixPath(path).name in POSTGRES_INTEGRATION
    nodeid = path + "::synthetic_ownership_grammar_only"
    assert (
        sum(
            selection.selected_for("postgres", index, nodeid)
            for index in range(selection.POSTGRES_COMPONENT_COUNT)
        )
        == 1
    )
    assert not any(
        selection.selected_for("shard", index, nodeid)
        for index in range(selection.SHARD_COUNT)
    )
