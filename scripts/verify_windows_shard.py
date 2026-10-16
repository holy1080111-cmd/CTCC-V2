"""Partition one complete Windows pytest collection by whole test module.

Every Windows worker collects the identical reviewed non-DB suite before
deselecting its deterministic share. The union verifier is the only authority
that can call the full Windows regression PASS.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

import pytest

from scripts.verify_windows_regression import (
    NO_DB_INTEGRATION,
    WindowsRegressionError,
    _junit_key,
    _sha256,
    test_selection,
)

SHARD_COUNT = 8
_OBSERVED: set[str] = set()


def module_shard(path: str) -> int:
    """Keep parameterizations and module fixtures on one Windows worker."""
    if (
        type(path) is not str
        or "\\" in path
        or not path.endswith(".py")
        or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
        or not (
            path.startswith("tests/unit/")
            or path in {f"tests/integration/{name}" for name in NO_DB_INTEGRATION}
        )
    ):
        raise WindowsRegressionError("windows_shard_module_path_invalid")
    return (
        int.from_bytes(hashlib.sha256(path.encode()).digest()[:8], "big") % SHARD_COUNT
    )


def selected_for(index: int, nodeid: str) -> bool:
    if type(index) is not int or not 0 <= index < SHARD_COUNT:
        raise WindowsRegressionError("windows_shard_index_invalid")
    if type(nodeid) is not str or "::" not in nodeid:
        raise WindowsRegressionError("windows_shard_nodeid_invalid")
    return module_shard(nodeid.split("::", 1)[0]) == index


def _unfiltered(config) -> None:
    for option_name in (
        "keyword",
        "markexpr",
        "ignore",
        "ignore_glob",
        "deselect",
        "lf",
        "ff",
        "stepwise",
    ):
        if getattr(config.option, option_name, None) not in (None, "", [], (), False):
            raise WindowsRegressionError("windows_collection_filtered")


def pytest_addoption(parser) -> None:
    group = parser.getgroup("ctcc-windows-shard")
    group.addoption("--ctcc-windows-shard-index", type=int)
    group.addoption("--ctcc-windows-collection-json")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items) -> None:
    index = config.getoption("--ctcc-windows-shard-index")
    target = config.getoption("--ctcc-windows-collection-json")
    if type(index) is not int or not 0 <= index < SHARD_COUNT or not target:
        raise WindowsRegressionError("windows_shard_arguments_invalid")
    _unfiltered(config)
    root = Path.cwd().resolve(strict=True)
    if [str(path) for path in config.args] != list(test_selection(root)):
        raise WindowsRegressionError("windows_shard_selection_changed")
    full_cases = []
    nodeids = set()
    keys = set()
    for item in items:
        nodeid = item.nodeid
        path = nodeid.split("::", 1)[0]
        if item.path.relative_to(config.rootpath).as_posix() != path:
            raise WindowsRegressionError("windows_shard_item_path_changed")
        module_shard(path)
        key = _junit_key(nodeid)
        if nodeid in nodeids or key in keys:
            raise WindowsRegressionError("windows_shard_full_collection_duplicate")
        nodeids.add(nodeid)
        keys.add(key)
        full_cases.append({"nodeid": nodeid, "classname": key[0], "name": key[1]})
    if not full_cases:
        raise WindowsRegressionError("windows_shard_full_collection_empty")
    collected_modules = {case["nodeid"].split("::", 1)[0] for case in full_cases}
    required_modules = {
        path.relative_to(root).as_posix()
        for path in (root / "tests" / "unit").rglob("test_*.py")
        if path.is_file()
    } | {f"tests/integration/{name}" for name in NO_DB_INTEGRATION}
    if collected_modules != required_modules:
        raise WindowsRegressionError("windows_shard_full_module_set_changed")
    selected = [item for item in items if selected_for(index, item.nodeid)]
    deselected = [item for item in items if not selected_for(index, item.nodeid)]
    if not selected:
        raise WindowsRegressionError("windows_shard_empty")
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    _OBSERVED.clear()
    session._ctcc_windows_shard_record = {
        "schema": "ctcc.windows_full_collection_shard.v1",
        "shard_index": index,
        "shard_count": SHARD_COUNT,
        "requested_paths": list(test_selection(root)),
        "manifest_sha256": _sha256(root / "MANIFEST.sha256"),
        "python": sys.version,
        "selection_filters_absent": True,
        "full_cases": full_cases,
        "selected_nodeids": [item.nodeid for item in selected],
    }
    session._ctcc_windows_shard_path = Path(target)


def pytest_collection_finish(session) -> None:
    record = getattr(session, "_ctcc_windows_shard_record", None)
    if (
        record is not None
        and [item.nodeid for item in session.items] != record["selected_nodeids"]
    ):
        raise WindowsRegressionError("windows_shard_late_collection_change")


def pytest_runtest_logreport(report) -> None:
    _OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    record = getattr(session, "_ctcc_windows_shard_record", None)
    if record is None:
        return
    del exitstatus
    selected = record["selected_nodeids"]
    if _OBSERVED != set(selected):
        session.exitstatus = 1
    record["observed_nodeids"] = [nodeid for nodeid in selected if nodeid in _OBSERVED]
    record["pytest_exitstatus"] = int(session.exitstatus)
    output = session._ctcc_windows_shard_path
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    if json.loads(output.read_text(encoding="utf-8")) != record:
        session.exitstatus = 1
