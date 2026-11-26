"""One exact source58 collection, five PostgreSQL owners and eight shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import verify_linux_shard as previous
from scripts.evidence_json import read_evidence_json

SCHEMA = "ctcc.current0058.collection.v1"
SOURCE_HEAD = "0058"
POSTGRES_COMPONENT_COUNT = 5
SHARD_COUNT = 8
SUCCESSOR_PATHS = frozenset(
    {
        "tests/integration/test_native_live_ledger_v1_pg.py",
        "tests/integration/test_native_live_financial_epoch_v1_pg.py",
        "tests/integration/test_native_live_physical_v1_pg.py",
        "tests/integration/test_native_live_operator_control_v1_pg.py",
        "tests/integration/test_native_live_lifecycle_v1_pg.py",
        "tests/integration/test_native_live_controlled_close_v1_pg.py",
        "tests/integration/test_native_stopped_setup_0058_pg.py",
        "tests/integration/test_native_demo_profile_0058_pg.py",
        "tests/integration/test_native_submission_binding_0058_pg.py",
        "tests/integration/test_native_original_oco_public_0058_pg.py",
        "tests/integration/test_native_original_oco_reporting_0058_pg.py",
        "tests/integration/test_native_protective_hold_schema_v58_pg.py",
        "tests/integration/test_g12_restricted_writer_0057_pg.py",
        "tests/integration/test_owned_history_profiles0057_pg.py",
        "tests/integration/test_terminal_readout_profiles0057_pg.py",
        "tests/integration/test_native_postsubmit_profiles0057_pg.py",
    }
)
# The source58 read-only owner tool observes a genuine disposable0017 clone.
# It belongs to the retained-state owner worker, never successor profile proof
# or an execution-capable unit shard. Frozen56/57 inventories stay unchanged.
RETAINED0017_OWNER_PATHS = frozenset(
    {
        "tests/integration/test_retained0017_owner_plan_v1_pg.py",
        "tests/integration/test_retained0017_catalog_references_v1_pg.py",
    }
)
CANONICAL_OWNER_PATHS = frozenset(
    {
        "tests/integration/test_native_owner_upgrade_0056_v2_pg.py",
        "tests/integration/test_native_owner_upgrade_0057_pg.py",
        "tests/integration/test_native_owner_upgrade_0058_pg.py",
        "tests/integration/test_native_owner_storage_diagnostic_0058_pg.py",
        "tests/integration/test_canonical0055_catalog_reference_v1_pg.py",
    }
)
OWNER_PATHS = RETAINED0017_OWNER_PATHS | CANONICAL_OWNER_PATHS
PAPER58_REFERENCE_PATHS = frozenset(
    {
        "tests/integration/test_paper58_catalog_reference_v1_pg.py",
        "tests/integration/test_paper58_owned_runtime_v1_pg.py",
    }
)
_OBSERVED = set()
# Keep every historical receipt producer together on its original observer.
# Other whole modules split deterministically; the complete collection/JUnit
# union still has to prove every original and new case ran exactly once.
HISTORICAL_PRODUCER_PATHS = frozenset(
    {
        "tests/integration/test_devval_campaign_custody_pg_v1.py",
        "tests/integration/test_gate3_fitted_candidate_custody_pg.py",
    }
)


def _need(condition):
    if condition is not True:
        raise ValueError("current58_collection_invalid") from None


def _mode(mode, index):
    _need(
        mode == "full"
        and index is None
        or mode == "postgres"
        and type(index) is int
        and 0 <= index < POSTGRES_COMPONENT_COUNT
        or mode == "shard"
        and type(index) is int
        and 0 <= index < SHARD_COUNT
    )


def postgres_paths(index):
    _mode("postgres", index)
    if index == 2:
        return SUCCESSOR_PATHS
    parent = index if index < 2 else index - 3
    paths = (
        previous.postgres_paths(parent)
        - SUCCESSOR_PATHS
        - OWNER_PATHS
        - PAPER58_REFERENCE_PATHS
    )
    retained = previous.POSTGRES_ANCHOR_PATHS | HISTORICAL_PRODUCER_PATHS
    moved = frozenset(
        path
        for path in paths
        if path not in retained and previous.module_shard(path) >= 4
    )
    owned = paths - moved if index < 2 else moved
    return (
        owned | OWNER_PATHS
        if index == 3
        else owned | PAPER58_REFERENCE_PATHS
        if index == 0
        else owned
    )


def selected_for(mode, index, node):
    _mode(mode, index)
    _need(type(node) is str)
    path = node.split("::", 1)[0]
    _need(
        type(node) is str
        and path.startswith("tests/")
        and path.endswith(".py")
        and ".." not in Path(path).parts
    )
    if mode == "full":
        return True
    if mode == "postgres":
        return path in postgres_paths(index)
    return (
        path
        not in previous.POSTGRES_TEST_PATHS
        | SUCCESSOR_PATHS
        | OWNER_PATHS
        | PAPER58_REFERENCE_PATHS
        and previous.module_shard(path) == index
    )


def report_paths(mode, index):
    _mode(mode, index)
    label = mode + ("" if index is None else "-" + str(index))
    return (
        f"test-results/current58-{label}.xml",
        f"test-results/current58-{label}.collection.json",
    )


def _record(mode, index, full, selected, observed, exitstatus):
    return {
        "schema": SCHEMA,
        "source_contract_head": SOURCE_HEAD,
        "mode": mode,
        "shard_index": index if mode == "shard" else None,
        "postgres_component_index": index if mode == "postgres" else None,
        "shard_count": SHARD_COUNT,
        "postgres_component_count": POSTGRES_COMPONENT_COUNT,
        "full_nodeids": full,
        "selected_nodeids": selected,
        "observed_nodeids": observed,
        "pytest_exitstatus": exitstatus,
    }


def verify_collection(raw, *, mode, index, report):
    _mode(mode, index)
    _need(type(raw) is bytes and 0 < len(raw) <= 32 * 1024 * 1024)
    record = read_evidence_json(raw)
    _need(
        type(record) is dict and set(record) == set(_record(mode, index, [], [], [], 0))
    )
    full, selected = record["full_nodeids"], record["selected_nodeids"]
    _need(
        type(full) is list
        and bool(full)
        and all(type(node) is str for node in full)
        and len(full) == len(set(full))
    )
    expected = [node for node in full if selected_for(mode, index, node)]
    _need(
        bool(expected)
        and selected == expected
        and record == _record(mode, index, full, expected, expected, 0)
    )
    _need(
        type(record["pytest_exitstatus"]) is int
        and type(record["shard_count"]) is int
        and type(record["postgres_component_count"]) is int
    )
    _need(
        mode == "full"
        or type(
            record["postgres_component_index"]
            if mode == "postgres"
            else record["shard_index"]
        )
        is int
    )
    _need(type(report["tests"]) is int and report["tests"] == len(selected))
    return record


def pytest_addoption(parser):
    group = parser.getgroup("ctcc-current58-collection")
    group.addoption("--ctcc-current58-suite", choices=("full", "postgres", "shard"))
    group.addoption("--ctcc-current58-index", type=int)
    group.addoption("--ctcc-current58-collection")
    group.addoption("--ctcc-current58-progress", action="store_true", default=False)


def pytest_collection_modifyitems(session, config, items):
    mode = config.getoption("--ctcc-current58-suite")
    if mode is None:
        return
    index = config.getoption("--ctcc-current58-index")
    _mode(mode, index)
    target = config.getoption("--ctcc-current58-collection")
    _need(type(target) is str and bool(target))
    full = [item.nodeid for item in items]
    _need(bool(full) and len(full) == len(set(full)))
    _need(
        all(
            item.nodeid.split("::", 1)[0]
            == item.path.relative_to(config.rootpath).as_posix()
            for item in items
        )
    )
    selected = [item for item in items if selected_for(mode, index, item.nodeid)]
    rejected = [item for item in items if not selected_for(mode, index, item.nodeid)]
    _need(bool(selected))
    if rejected:
        config.hook.pytest_deselected(items=rejected)
    items[:] = selected
    _OBSERVED.clear()
    session._ctcc_current58_collection = (
        Path(target),
        mode,
        index,
        full,
        [item.nodeid for item in selected],
    )
    if config.getoption("--ctcc-current58-progress"):
        _need(
            mode == "postgres"
            and Path(target).parent == previous.POSTGRES_PROGRESS_PATH.parent
        )
        progress = previous._PostgresCaseProgress(
            previous.POSTGRES_PROGRESS_PATH, index, [item.nodeid for item in selected]
        )
        try:
            _need(
                config.pluginmanager.register(progress, "ctcc_current58_case_progress")
                is not None
            )
        except BaseException:
            progress.pytest_unconfigure()
            raise


def pytest_runtest_logreport(report):
    _OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    del exitstatus
    state = getattr(session, "_ctcc_current58_collection", None)
    if state is None:
        return
    target, mode, index, full, selected = state
    if _OBSERVED != set(selected):
        session.exitstatus = 1
    value = _record(
        mode,
        index,
        full,
        selected,
        [node for node in selected if node in _OBSERVED],
        int(session.exitstatus),
    )
    with target.open("x", encoding="ascii") as output:
        json.dump(value, output, sort_keys=True, separators=(",", ":"))


def collection_digest(nodes):
    return hashlib.sha256(
        json.dumps(nodes, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
