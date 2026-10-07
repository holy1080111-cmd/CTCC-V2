"""Partition one immutable Linux pytest collection without dropping any case.

The dedicated PostgreSQL suite owns its reviewed modules. Every other complete
test module belongs to exactly one deterministic shard. Each process records
both the full collection and actual execution for a later cross-run proof.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

POSTGRES_TEST_PATHS = frozenset(
    {
        "tests/integration/test_qualification_ledger_repository.py",
        "tests/integration/test_qualification_submission_intent_repository.py",
        "tests/integration/test_history_submission_intent_repository.py",
        "tests/integration/test_submission_reporting_repository.py",
        "tests/integration/test_qualification_bootstrap_repository.py",
        "tests/integration/test_demo_control_repository.py",
        "tests/integration/test_demo_control_durability_probe.py",
        "tests/integration/test_durable_migration_downgrade.py",
        "tests/integration/test_gate3_canonical_schedule_claim.py",
        "tests/integration/test_gate3_capture_schedule_pin_repository.py",
        "tests/integration/test_gate3_committed_preregistration_seal.py",
        "tests/integration/test_gate3_schedule_publication_ack.py",
        "tests/integration/test_account_ingestion_journal_repository.py",
        "tests/integration/test_account_capture_crash_probe_repository.py",
        "tests/integration/test_account_history_query_verifier_repository.py",
        "tests/integration/test_range_v5_reservation_repository.py",
        "tests/integration/test_account_observation_index_repository.py",
        "tests/integration/test_account_bill_archive_claim_repository.py",
        "tests/integration/test_control_bound_ledger_repository.py",
        "tests/integration/test_ledger_event_observation_repository.py",
        "tests/integration/test_public_receipt_witness_repository.py",
    }
)
SHARD_COUNT = 8
_OBSERVED: set[str] = set()


def module_shard(path: str) -> int:
    """Keep parametrized cases and module fixtures on the same worker."""
    if (
        type(path) is not str
        or not path.startswith("tests/")
        or ".." in Path(path).parts
    ):
        raise ValueError("linux_shard_path_invalid")
    return (
        int.from_bytes(hashlib.sha256(path.encode("utf-8")).digest()[:8], "big")
        % SHARD_COUNT
    )


def selected_for(mode: str, index: int | None, nodeid: str) -> bool:
    path = nodeid.split("::", 1)[0]
    if mode == "postgres":
        return path in POSTGRES_TEST_PATHS
    if mode == "shard" and type(index) is int and 0 <= index < SHARD_COUNT:
        return path not in POSTGRES_TEST_PATHS and module_shard(path) == index
    raise ValueError("linux_shard_selection_invalid")


def pytest_addoption(parser):
    group = parser.getgroup("ctcc-linux-shard")
    group.addoption("--ctcc-linux-suite", choices=("postgres", "shard"))
    group.addoption("--ctcc-linux-shard-index", type=int)
    group.addoption("--ctcc-linux-collection-json")


def pytest_collection_modifyitems(session, config, items):
    mode = config.getoption("--ctcc-linux-suite")
    if mode is None:
        return
    index = config.getoption("--ctcc-linux-shard-index")
    target = config.getoption("--ctcc-linux-collection-json")
    if (
        type(target) is not str
        or not target
        or mode == "postgres"
        and index is not None
        or mode == "shard"
        and (type(index) is not int or not 0 <= index < SHARD_COUNT)
    ):
        raise ValueError("linux_shard_arguments_invalid")
    full = [item.nodeid for item in items]
    if not full or len(full) != len(set(full)):
        raise ValueError("linux_full_collection_missing_or_duplicate")
    for item in items:
        relative = item.path.relative_to(config.rootpath).as_posix()
        if item.nodeid.split("::", 1)[0] != relative:
            raise ValueError("linux_collection_path_mismatch")
    selected = [item for item in items if selected_for(mode, index, item.nodeid)]
    deselected = [item for item in items if not selected_for(mode, index, item.nodeid)]
    if not selected:
        raise ValueError("linux_shard_empty")
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected
    _OBSERVED.clear()
    session._ctcc_linux_collection = {
        "schema": "ctcc.linux_full_collection_shard.v1",
        "mode": mode,
        "shard_index": index,
        "shard_count": SHARD_COUNT,
        "full_nodeids": full,
        "selected_nodeids": [item.nodeid for item in selected],
    }
    session._ctcc_linux_collection_path = Path(target)


def pytest_runtest_logreport(report):
    _OBSERVED.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    record = getattr(session, "_ctcc_linux_collection", None)
    if record is None:
        return
    del exitstatus
    selected = record["selected_nodeids"]
    if _OBSERVED != set(selected):
        session.exitstatus = 1
    record["observed_nodeids"] = [nodeid for nodeid in selected if nodeid in _OBSERVED]
    record["pytest_exitstatus"] = int(session.exitstatus)
    path = session._ctcc_linux_collection_path
    path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
