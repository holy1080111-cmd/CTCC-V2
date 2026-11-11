"""Exercise the actual migration graph and independent historical prefix pins."""

from pathlib import Path
from shutil import copy2, copytree

import pytest

from scripts import current_contract_source_scopes_v1 as scopes
from scripts import historical0052_0053_on_current0056_toolchain as source56
from scripts import manifest
from scripts.verify_migration_identity import source_identity

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source_tree(tmp_path):
    copy2(ROOT / "alembic.ini", tmp_path / "alembic.ini")
    copytree(
        ROOT / "migrations",
        tmp_path / "migrations",
        ignore=lambda _root, names: {"__pycache__"} & set(names),
    )
    return tmp_path


def _manifest(root):
    manifest.write_manifest(root, root / "MANIFEST.sha256")


def test_source55_replays_distinct_pinned_historical_prefixes(source_tree):
    (source_tree / scopes._TAIL["0056"][0]).unlink()
    _manifest(source_tree)
    source = source_identity(source_tree)
    assert source.head == "0055"
    historical = (
        scopes.historical0052_source_scope(source_tree),
        scopes.historical0053_source_scope(source_tree),
        scopes.database0054_source_scope(source_tree),
    )
    current = scopes.actual_current_source_scope(source_tree)
    assert tuple(item.database_target for item in (*historical, current)) == (
        "0052",
        "0053",
        "0054",
        "0055",
    )
    assert current.historical_prefix_sha256 == current.actual_migration_sha256
    assert len({item.historical_prefix_sha256 for item in (*historical, current)}) == 4
    for item in (*historical, current):
        assert item.actual_source_head == source.head
        assert item.actual_migration_sha256 == source.sha256
        assert item.account_complete is item.execution_authority is False
    with pytest.raises(scopes.ContractSourceScopeError):
        scopes.current0054_source_scope(source_tree)


def test_exact_source54_scope_remains_valid_for_its_own_graph(source_tree):
    (source_tree / scopes._TAIL["0056"][0]).unlink()
    (source_tree / scopes._TAIL["0055"][0]).unlink()
    _manifest(source_tree)
    strict = scopes.current0054_source_scope(source_tree)
    assert strict == scopes.database0054_source_scope(source_tree)
    assert strict == scopes.actual_current_source_scope(source_tree)
    assert strict.actual_source_head == strict.database_target == "0054"
    assert strict.actual_migration_sha256 == strict.historical_prefix_sha256


def test_source56_keeps_the_exact_db55_prefix(source_tree):
    _manifest(source_tree)
    source = source_identity(source_tree)
    current = scopes.actual_current_source_scope(source_tree)
    prior = scopes.database0055_source_scope(source_tree)
    assert (
        source.head == current.actual_source_head == current.database_target == "0056"
    )
    assert (
        current.actual_migration_sha256
        == current.historical_prefix_sha256
        == source.sha256
    )
    assert prior.actual_source_head == "0056" and prior.database_target == "0055"
    assert prior.actual_migration_sha256 == source.sha256
    assert prior.historical_prefix_sha256 == scopes._PREFIX["0055"][0]


@pytest.mark.parametrize("target", ["0052", "0053"])
def test_source56_historical_adapter_binds_its_exact_migration_prefix(
    source_tree, target
):
    _manifest(source_tree)
    scope = source56._source(source_tree, target)
    assert scope.actual_source_head == "0056" and scope.database_target == target
    assert scope.historical_prefix_sha256 == scopes._PREFIX[target][0]
    assert scope.account_complete is scope.execution_authority is False


def test_source56_historical_adapter_cannot_relabel_source55(source_tree):
    (source_tree / scopes._TAIL["0056"][0]).unlink()
    _manifest(source_tree)
    with pytest.raises(source56.Current0056HistoricalReadbackError):
        source56._source(source_tree, "0053")


@pytest.mark.parametrize("revision", ["0054", "0055", "0056"])
def test_historical0052_rejects_changed_later_migration_despite_fresh_manifest(
    source_tree, revision
):
    migration = source_tree / scopes._TAIL[revision][0]
    migration.write_bytes(migration.read_bytes() + b"\n# changed fixture\n")
    _manifest(source_tree)
    with pytest.raises(scopes.ContractSourceScopeError):
        scopes.historical0052_source_scope(source_tree)
