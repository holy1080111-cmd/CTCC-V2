"""Closed wire contract for the three original PostgreSQL bootstrap cases."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET

CASES = (
    (
        "tests.integration.test_account_history_prefix_repository",
        "test_cross_page_33_prefix_scale_and_fail_closed",
    ),
    (
        "tests.integration.test_account_locked_current_history_join_v7_repository",
        "test_isolated_postgresql_typed_v7_join_replays_b1_and_denies",
    ),
    (
        "tests.integration.test_account_locked_current_history_join_v7_repository",
        "test_postgresql_typed_pre_http_readback_uses_committed_b1_and_exact_binding",
    ),
)
NODES = tuple(module.replace(".", "/") + ".py::" + name for module, name in CASES)
NODE_DIGESTS = tuple(hashlib.sha256(node.encode()).hexdigest() for node in NODES)
STAGE = "account-bootstrap-diagnostic"
TRACE = "account-bootstrap-phases.log"
JUNIT = "account-bootstrap-original.xml"
EXPORT = "account-bootstrap-diagnostic.json"
MAX_TRACE = 512 * 1024
MAX_REPORT = 1024 * 1024
MAX_RECORDS = 640
MAX_CAPTURES = 40
MAX_SEQUENCE = 2048
SCHEMA = "ctcc.pg_account_bootstrap_phase.v1"
PLAN_KINDS = {"regional", "historical_v5", "current_v7", "other"}
PHASES = {"bootstrap_recorded", "bootstrap_capture", "source_close", "terminal"}
JOURNAL_KINDS = {
    "capture_start",
    "request_start",
    "headers_received",
    "body_progress",
    "body_complete",
    "page_validated",
    "raw_finalized",
    "acquisition_closed",
    "packet_recorded",
    "terminal",
    "other",
}
FIXED_CODES = {
    "account_capture_invalid",
    "account_records_invalid",
    "account_runtime_invalid",
    "account_transport_failed",
    "batch_deadline_exceeded",
    "journal_acquisition_incomplete",
    "journal_chain_byte_bound",
    "journal_commit_readback_unknown",
    "journal_conflicting_source_rows",
    "journal_event_missing",
    "journal_original_owned_packet_required",
    "journal_predecessor_invalid",
    "journal_readback_invalid",
    "journal_receipt_clock_order",
    "journal_secret_scan_incomplete",
    "journal_source_clock_order",
    "ledger_revision_changed_during_capture",
    "publication_barrier_not_crossed",
    "request_deadline_exceeded",
    "runtime_clock_invalid",
}
DISPOSITIONS = {
    "returned",
    "deadline_timeout",
    "cancelled",
    "account_collection_error",
    "account_runtime_error",
    "account_journal_error",
    "database_or_driver_error",
    "other_exception",
}
STATES = {"passed", "failed", "skipped"}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("account_bootstrap_duplicate_key")
    return value


def _record(record):
    common = {"schema", "sequence", "event", "node_sha256"}
    if type(record) is not dict or not common <= record.keys():
        return False
    event = record["event"]
    if event in {"node_start", "node_finish"}:
        return record.keys() == common
    if event == "pytest_report":
        return (
            record.keys() == common | {"when", "outcome"}
            and record["when"] in {"setup", "call", "teardown"}
            and record["outcome"] in STATES
        )
    capture = {"capture_ordinal", "plan_kind"}
    if not capture <= record.keys() or not (
        _integer(record["capture_ordinal"], 1, MAX_CAPTURES)
        and record["plan_kind"] in PLAN_KINDS
    ):
        return False
    base = (
        common
        | capture
        | {
            "phase",
            "journal_sequence",
            "journal_last_completed",
            "script_request_ordinal",
        }
    )
    if not base <= record.keys() or not (
        record["phase"] in PHASES
        and _integer(record["journal_sequence"], 0, MAX_SEQUENCE)
        and _integer(record["script_request_ordinal"], 0, MAX_SEQUENCE)
        and (
            record["journal_last_completed"] is None
            or record["journal_last_completed"] in JOURNAL_KINDS
        )
    ):
        return False
    if event == "phase_start":
        return record.keys() == base
    return (
        event == "phase_finish"
        and record.keys()
        == base
        | {
            "elapsed_ns",
            "disposition",
            "fixed_code",
            "journal_writes",
            "journal_failure",
        }
        and _integer(record["elapsed_ns"], 0, 30 * 60 * 10**9)
        and record["disposition"] in DISPOSITIONS
        and (record["fixed_code"] is None or record["fixed_code"] in FIXED_CODES)
        and (
            record["journal_failure"] is None
            or (
                type(record["journal_failure"]) is list
                and len(record["journal_failure"]) == 3
                and record["journal_failure"][0] in JOURNAL_KINDS
                and record["journal_failure"][1] in DISPOSITIONS - {"returned"}
                and (
                    record["journal_failure"][2] is None
                    or record["journal_failure"][2] in FIXED_CODES
                )
            )
        )
        and type(record["journal_writes"]) is dict
        and all(
            kind in JOURNAL_KINDS
            and type(counters) is list
            and len(counters) == 3
            and _integer(counters[0], 1, MAX_SEQUENCE)
            and _integer(counters[1], 0, record["elapsed_ns"])
            and _integer(counters[2], 0, counters[1])
            for kind, counters in record["journal_writes"].items()
        )
    )


def read_trace(raw):
    """Parse fixed categories only; a torn terminal line is never exported."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_TRACE:
        raise ValueError("account_bootstrap_trace_bound")
    complete, _, tail = raw.rpartition(b"\n")
    lines = complete.split(b"\n") if complete else []
    if not 0 < len(lines) <= MAX_RECORDS:
        raise ValueError("account_bootstrap_trace_bound")
    records, outcomes = [], {}
    active, seen, reports, phases, current_capture = None, [], {}, [], 0
    try:
        for index, line in enumerate(lines, 1):
            record = json.loads(line, object_pairs_hook=_object)
            if (
                not _record(record)
                or record["schema"] != SCHEMA
                or not _integer(record["sequence"], index, index)
                or not re.fullmatch(r"[0-9a-f]{64}", record["node_sha256"])
            ):
                raise ValueError
            event, node = record["event"], record["node_sha256"]
            if event == "node_start":
                if (
                    active is not None
                    or len(seen) == len(NODE_DIGESTS)
                    or node != NODE_DIGESTS[len(seen)]
                ):
                    raise ValueError
                active = node
                seen.append(node)
                reports, phases, current_capture = {}, [], 0
            if active != node:
                raise ValueError
            if event == "phase_start":
                phase = record["phase"]
                capture = record["capture_ordinal"]
                parent = phases[-1][0] if phases else None
                if phase == "bootstrap_recorded":
                    if parent is not None or capture != current_capture + 1:
                        raise ValueError
                    current_capture = capture
                elif (
                    capture != current_capture
                    or parent
                    != {
                        "bootstrap_capture": "bootstrap_recorded",
                        "source_close": "bootstrap_capture",
                        "terminal": "bootstrap_recorded",
                    }[phase]
                ):
                    raise ValueError
                phases.append((phase, capture, record["plan_kind"]))
            if event == "phase_finish" and (
                not phases
                or phases.pop()
                != (record["phase"], record["capture_ordinal"], record["plan_kind"])
            ):
                raise ValueError
            if event == "pytest_report":
                when = record["when"]
                if phases or when in reports:
                    raise ValueError
                reports[when] = record["outcome"]
            if event == "node_finish":
                if phases or not {"setup", "teardown"} <= reports.keys():
                    raise ValueError
                if reports["setup"] == "passed" and "call" not in reports:
                    raise ValueError
                outcomes[node] = (
                    "failed"
                    if "failed" in reports.values()
                    else "skipped"
                    if "skipped" in reports.values()
                    else "passed"
                )
                active = None
            records.append(record)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("account_bootstrap_trace_invalid") from None
    return (
        records,
        not tail and active is None and tuple(seen) == NODE_DIGESTS,
        outcomes,
    )


def read_junit(raw):
    """Reduce original pytest results to exact test IDs and closed outcomes."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_REPORT or b"<!" in raw:
        raise ValueError("account_bootstrap_junit_bound")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("account_bootstrap_junit_invalid") from None
    results = {}
    for case in root.iter("testcase"):
        identity = (case.get("classname"), case.get("name"))
        if identity not in CASES or identity in results:
            raise ValueError("account_bootstrap_junit_node_invalid")
        terminal = [
            child.tag for child in case if child.tag in {"failure", "error", "skipped"}
        ]
        if len(terminal) > 1:
            raise ValueError("account_bootstrap_junit_invalid")
        results[identity] = (
            {"failure": "failed", "error": "failed", "skipped": "skipped"}[terminal[0]]
            if terminal
            else "passed"
        )
    if set(results) != set(CASES):
        raise ValueError("account_bootstrap_junit_node_invalid")
    return {
        digest: results[identity]
        for digest, identity in zip(NODE_DIGESTS, CASES, strict=True)
    }
