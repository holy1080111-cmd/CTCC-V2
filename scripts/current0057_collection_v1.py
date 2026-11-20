"""One exact source57 pytest collection, three PostgreSQL owners and eight shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import verify_linux_shard as previous
from scripts.evidence_json import read_evidence_json

SCHEMA = "ctcc.current0057.collection.v1"
SOURCE_HEAD = "0057"
POSTGRES_COMPONENT_COUNT = 3
SHARD_COUNT = 8
SUCCESSOR_PATHS = frozenset({"tests/integration/test_g12_restricted_writer_0057_pg.py"})
_OBSERVED = set()


def _need(condition):
    if condition is not True:
        raise ValueError("current57_collection_invalid") from None


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
    return (
        SUCCESSOR_PATHS
        if index == 2
        else previous.postgres_paths(index) - SUCCESSOR_PATHS
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
        path not in previous.POSTGRES_TEST_PATHS | SUCCESSOR_PATHS
        and previous.module_shard(path) == index
    )


def report_paths(mode, index):
    _mode(mode, index)
    label = mode + ("" if index is None else "-" + str(index))
    return (
        f"test-results/current57-{label}.xml",
        f"test-results/current57-{label}.collection.json",
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
    group = parser.getgroup("ctcc-current57-collection")
    group.addoption("--ctcc-current57-suite", choices=("full", "postgres", "shard"))
    group.addoption("--ctcc-current57-index", type=int)
    group.addoption("--ctcc-current57-collection")
    group.addoption("--ctcc-current57-progress", action="store_true", default=False)


def pytest_collection_modifyitems(session, config, items):
    mode = config.getoption("--ctcc-current57-suite")
    if mode is None:
        return
    index = config.getoption("--ctcc-current57-index")
    _mode(mode, index)
    target = config.getoption("--ctcc-current57-collection")
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
    session._ctcc_current57_collection = (
        Path(target),
        mode,
        index,
        full,
        [item.nodeid for item in selected],
    )
    if config.getoption("--ctcc-current57-progress"):
        _need(
            mode == "postgres"
            and Path(target).parent == previous.POSTGRES_PROGRESS_PATH.parent
        )
        progress = previous._PostgresCaseProgress(
            previous.POSTGRES_PROGRESS_PATH, index, [item.nodeid for item in selected]
        )
        try:
            _need(
                config.pluginmanager.register(progress, "ctcc_current57_case_progress")
                is not None
            )
        except BaseException:
            progress.pytest_unconfigure()
            raise


def pytest_runtest_logreport(report):
    _OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    del exitstatus
    state = getattr(session, "_ctcc_current57_collection", None)
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
