"""Closed source-case inventory and redacted evidence policy; no application IO."""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

OLD_MODULE = "tests/integration/test_submission_transport_denial_migration.py"
NEW_MODULE = "tests/integration/test_public_receipt_clock_custody_repository_v1.py"
LAST_TWO = (
    OLD_MODULE
    + "::test_new_acknowledged_response_is_retained_only_as_ineligible_evidence[0041]",
    OLD_MODULE + "::test_active_writer_prevents_partial_upgrade[0041]",
)
NEW_TWELVE = (
    NEW_MODULE + "::test_actual_catalog_acquire_and_independent_recheck",
    *(
        NEW_MODULE + "::test_actual_catalog_drift_denies[" + name + "]"
        for name in (
            "trigger_disabled",
            "bounds_weakened",
            "public_function_execute",
            "direct_table_read",
            "schema_create",
            "wrong_role_execute",
            "search_path_changed",
            "function_body_changed",
            "unique_index_missing",
        )
    ),
    NEW_MODULE + "::test_actual_recheck_failure_remains_poisoned_after_repair",
    NEW_MODULE + "::test_actual_owner_cannot_cross_async_task",
)
EXPERIMENTS = {"A": LAST_TWO, "B": NEW_TWELVE + LAST_TWO}
MAX_EVENTS = 4096
MAX_EVENT_BYTES = 4096
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
EVENT_KEYS = frozenset(
    {"sequence", "kind", "nodeid", "phase", "monotonic_ns", "duration_ns", "outcome"}
)
KINDS = frozenset(
    {
        "phase_start",
        "phase_end",
        "report",
        "helper_start",
        "helper_end",
        "helper_installed",
        "session_end",
    }
)
OUTCOMES = frozenset(
    {
        "RUNNING",
        "RETURNED",
        "PASSED",
        "FAILED",
        "SKIPPED",
        "ERROR",
        "COMPLETE",
        "INCOMPLETE",
    }
)
INSTALL_LABELS = (
    "historical_migrate_helper",
    "body_migrate_helper",
    "reports_setup",
    "ledger_initialize",
    "private_rows",
)
PHASES = frozenset(
    {"setup", "call", "teardown", "session", *INSTALL_LABELS, "catalog_migrate_helper"}
) | frozenset("migration_" + f"{value:04d}" for value in range(17, 48))


def deny():
    raise ValueError("PG_PHASE_DIAGNOSTIC_EVIDENCE_DENIED")


def required(value):
    if not value:
        deny()


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            required(key not in value)
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=pairs)


def validate_event(value, *, experiment, sequence, previous_ns):
    required(
        experiment in EXPERIMENTS and type(value) is dict and set(value) == EVENT_KEYS
    )
    required(
        type(value["sequence"]) is int and value["sequence"] == sequence < MAX_EVENTS
    )
    required(
        value["kind"] in KINDS
        and value["phase"] in PHASES
        and value["outcome"] in OUTCOMES
    )
    required(value["nodeid"] in EXPERIMENTS[experiment] or value["nodeid"] == "SESSION")
    required(
        type(value["monotonic_ns"]) is int
        and previous_ns <= value["monotonic_ns"] <= 2**63 - 1
    )
    required(
        type(value["duration_ns"]) is int
        and 0 <= value["duration_ns"] <= 1_200_000_000_000
    )
    required(len(canonical(value)) <= MAX_EVENT_BYTES)
    return value


def read_phase_prefix(raw, *, experiment):
    """Accept a measured durable prefix; explicitly distinguish incomplete work."""
    required(type(raw) is bytes and 0 < len(raw) <= MAX_JOURNAL_BYTES)
    tail = None
    if not raw.endswith(b"\n"):
        complete, separator, fragment = raw.rpartition(b"\n")
        required(separator and complete)
        tail = {"sha256": hashlib.sha256(fragment).hexdigest(), "bytes": len(fragment)}
        raw = complete + b"\n"
    rows, previous = [], 0
    for line in raw.splitlines():
        required(0 < len(line) <= MAX_EVENT_BYTES)
        row = decode(line)
        validate_event(
            row, experiment=experiment, sequence=len(rows), previous_ns=previous
        )
        previous = row["monotonic_ns"]
        rows.append(row)
    required(rows)
    completed = (
        tail is None
        and rows[-1]["kind"] == "session_end"
        and rows[-1]["outcome"] == "COMPLETE"
    )
    outcomes = {}
    for row in rows:
        if row["kind"] == "report":
            key = row["nodeid"], row["phase"]
            required(key not in outcomes)
            outcomes[key] = row["outcome"]
    all_passed = completed and all(
        outcomes.get((node, phase)) == "PASSED"
        for node in EXPERIMENTS[experiment]
        for phase in ("setup", "call", "teardown")
    )
    installed = [row["phase"] for row in rows if row["kind"] == "helper_installed"]
    expected_install = INSTALL_LABELS + (
        ("catalog_migrate_helper",) if experiment == "B" else ()
    )
    instrumentation = tuple(installed) == expected_install
    return {
        "events": rows,
        "complete": completed,
        "all_expected_phases_passed": all_passed,
        "test_helper_instrumentation_complete": instrumentation,
        "truncated_tail": tail,
        "expected_case_count": len(EXPERIMENTS[experiment]),
        "prefix_measured": True,
    }


def redacted_junit(raw, *, experiment):
    required(
        type(raw) is bytes
        and 0 < len(raw) <= 1024 * 1024
        and b"<!DOCTYPE" not in raw.upper()
    )
    root = ET.fromstring(raw)
    required(root.tag == "testsuites")
    allowed = {
        (node.split("::")[0].removesuffix(".py").replace("/", "."), node.split("::")[1])
        for node in EXPERIMENTS[experiment]
    }
    seen, rows = set(), []
    clean = ET.Element("testsuites")
    suite = ET.SubElement(clean, "testsuite", name="bounded_pg_timing_diagnostic")
    for item in root.findall("./testsuite/testcase"):
        key = item.get("classname"), item.get("name")
        required(key in allowed and key not in seen)
        seen.add(key)
        duration = item.get("time")
        required(
            type(duration) is str
            and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,9})?", duration)
        )
        number = float(duration)
        required(math.isfinite(number) and 0 <= number <= 1200)
        failed = [
            tag for tag in ("failure", "error", "skipped") if item.find(tag) is not None
        ]
        required(len(failed) <= 1)
        outcome = failed[0].upper() if failed else "PASSED"
        case = ET.SubElement(
            suite, "testcase", classname=key[0], name=key[1], time=duration
        )
        if failed:
            ET.SubElement(case, failed[0], message="FIXED_DIAGNOSTIC_" + outcome)
        rows.append(
            {
                "nodeid": key[0].replace(".", "/") + ".py::" + key[1],
                "elapsed_seconds": number,
                "outcome": outcome,
            }
        )
    required(seen == allowed)
    counts = {
        "tests": len(rows),
        "failures": sum(row["outcome"] == "FAILURE" for row in rows),
        "errors": sum(row["outcome"] == "ERROR" for row in rows),
        "skipped": sum(row["outcome"] == "SKIPPED" for row in rows),
    }
    for key, value in counts.items():
        suite.set(key, str(value))
    return {
        "cases": rows,
        "all_passed": all(row["outcome"] == "PASSED" for row in rows),
    }, ET.tostring(clean, encoding="utf-8")
