"""Redact exactly two synthetic PostgreSQL original-history test outcomes.

Raw pytest XML and stdout remain in the owned hosted runner scratch. This
module emits only fixed node hashes, phases, fixed refusal codes and durations.
It neither retries a test nor relaxes an original request expiry.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET

CASES = (
    (
        "tests.integration.test_account_history_original_financial_native_pg_v2",
        "test_pg_financial_synthetic_wire_original33_uses_same_expiry_and_measured_finish",
    ),
    (
        "tests.integration.test_native_filled_flat_current55_pg",
        "test_exact55_two_roles_replay_original33_financial_without_authority",
    ),
)
NODES = tuple((module.replace(".", "/") + ".py::" + name) for module, name in CASES)
_FIRST_CODES = frozenset(
    {
        "original_pg_request_invalid",
        "original_pg_factory_invalid",
        "original_pg_session_invalid",
        "original_pg_clock_or_task_changed",
        "original_pg_original_expiry",
        "original_pg_backend_changed",
        "original_pg_request_causality",
        "original_pg_catalog_changed",
        "original_pg_incomplete",
        "original_pg_read_uncertain",
        "original_pg_cleanup_uncertain",
        "original_financial_pg_policy_mismatch",
        "original_financial_pg_component_changed",
    }
)
_SECOND_CLASSES = frozenset(
    {
        "READER_WRAPPER",
        "CURRENT55_CATALOG",
        "ORIGINAL_NATIVE_READER",
        "ORIGINAL_FRAME_WIRE",
        "ORIGINAL_PAGE_MEMO",
        "ORIGINAL_STREAM",
        "ORIGINAL_RAW_BANK",
        "CURRENT55_SQL_POLICY",
        "SQLALCHEMY_PROGRAMMING",
        "SQLALCHEMY_INTEGRITY",
        "SQLALCHEMY_DATA",
        "SQLALCHEMY_OPERATIONAL",
        "SQLALCHEMY_DBAPI",
        "SQLALCHEMY_OTHER",
        "TIMEOUT",
        "KEY_ERROR",
        "ATTRIBUTE_ERROR",
        "TYPE_ERROR",
        "VALUE_ERROR",
        "UNLISTED_EXCEPTION_CLASS",
    }
)
_SECOND_SITES = frozenset(
    {
        "BEFORE_FRAME_RPC_FENCE",
        "FRAME_RPC_AWAIT",
        "AFTER_FRAME_RPC_FENCE",
        "FRAME_CONTEXT_ENTER",
        "FRAME_WIRE_DECODE",
        "FRAME_TO_PAGE",
        "FRAME_PAGE_DIGEST",
        "BEFORE_YIELD_FENCE",
        "FRAME_YIELD",
        "AFTER_YIELD_FENCE",
        "ORIGINAL_DEADLINE_CHECK",
        "ORIGINAL_DEADLINE_DENY",
        "WIRE_PREFLIGHT",
        "WIRE_BIND_VALIDATED_RPC",
        "WIRE_FIXED_DENY",
        "WIRE_EXPIRY_CHECK",
        "WIRE_EXPIRY_DENY",
        "MEMO_BIND_FENCE",
        "MEMO_ORIGINAL_FENCE",
    }
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("original_financial_duplicate_json")
    return result


def _progress(raw: bytes | None) -> tuple[list[dict], str | None]:
    if raw is None:
        return [], None
    if type(raw) is not bytes or not 0 < len(raw) <= 16 * 1024:
        raise ValueError("original_financial_progress_invalid")
    rows = [json.loads(line, object_pairs_hook=_object) for line in raw.splitlines()]
    expected_order = _sha(json.dumps(NODES, separators=(",", ":")).encode("ascii"))
    if not rows or rows[0] != {
        "schema": "ctcc.linux_postgres_progress.v2",
        "event": "collection",
        "postgres_component_index": 0,
        "selected_count": 2,
        "selected_order_sha256": expected_order,
    }:
        raise ValueError("original_financial_progress_invalid")
    active = set()
    finished = set()
    failure = set()
    safe = []
    for row in rows[1:]:
        if type(row) is not dict or row.get("event") not in {
            "case_start",
            "case_failure",
            "case_finish",
        }:
            raise ValueError("original_financial_progress_invalid")
        index = row.get("selection_index")
        event = row["event"]
        expected = (
            {
                "event": event,
                "selection_index": index,
                "source_module": NODES[index].split("::", 1)[0],
                "node_sha256": _sha(NODES[index].encode("ascii")),
            }
            if type(index) is int and 0 <= index < 2
            else None
        )
        if expected is None:
            raise ValueError("original_financial_progress_invalid")
        if event == "case_failure":
            if row.get("phase") not in {"setup", "call", "teardown"}:
                raise ValueError("original_financial_progress_invalid")
            expected["phase"] = row["phase"]
        if row != expected:
            raise ValueError("original_financial_progress_invalid")
        if event == "case_start":
            if index in active or index in finished or index != len(finished):
                raise ValueError("original_financial_progress_invalid")
            active.add(index)
        elif event == "case_finish":
            if index not in active:
                raise ValueError("original_financial_progress_invalid")
            active.remove(index)
            finished.add(index)
        else:
            marker = (index, row["phase"])
            if index not in active or marker in failure:
                raise ValueError("original_financial_progress_invalid")
            failure.add(marker)
        safe.append(
            {
                "event": event,
                "selection_index": index,
                **({"phase": row["phase"]} if event == "case_failure" else {}),
            }
        )
    return safe, _sha(raw)


def _closed_second(raw: str) -> dict:
    if type(raw) is not str or not 0 < len(raw) <= 4096:
        raise ValueError("original_financial_closed_report_invalid")
    data = json.loads(raw, object_pairs_hook=_object)
    if (
        type(data) is not dict
        or data.get("schema") != "ctcc.pg55.original_financial_closed_failure.v1"
        or data.get("account_complete") is not False
        or data.get("execution_authority") is not False
        or type(data.get("chain")) is not list
        or not 1 <= len(data["chain"]) <= 6
        or data.get("original_frame_after_sequence") is not None
        and (
            type(data["original_frame_after_sequence"]) is not int
            or not 0 <= data["original_frame_after_sequence"] <= 33
        )
    ):
        raise ValueError("original_financial_closed_report_invalid")
    classes = []
    sites = []
    for row in data["chain"]:
        if (
            type(row) is not dict
            or set(row) != {"class", "stages", "sites"}
            or row["class"] not in _SECOND_CLASSES
            or type(row["stages"]) is not list
            or len(row["stages"]) > 16
            or type(row["sites"]) is not list
            or len(row["sites"]) > 4
            or any(type(value) is not str for value in row["stages"])
            or any(
                type(value) is not str or value not in _SECOND_SITES
                for value in row["sites"]
            )
        ):
            raise ValueError("original_financial_closed_report_invalid")
        classes.append(row["class"])
        sites.append(row["sites"])
    return {
        "closed_report_sha256": _sha(raw.encode("utf-8")),
        "classes": classes,
        "sites": sites,
        "original_frame_after_sequence": data["original_frame_after_sequence"],
    }


def _junit(raw: bytes | None) -> tuple[list[dict] | None, str | None]:
    if raw is None:
        return None, None
    if type(raw) is not bytes or not 0 < len(raw) <= 4 * 1024 * 1024:
        raise ValueError("original_financial_junit_invalid")
    root = ET.fromstring(raw)
    if root.tag != "testsuites" or len(root) != 1:
        raise ValueError("original_financial_junit_invalid")
    cases = root[0].findall("testcase")
    if len(cases) != 2:
        raise ValueError("original_financial_junit_invalid")
    safe = []
    for index, case in enumerate(cases):
        module, name = CASES[index]
        if case.get("classname") != module or case.get("name") != name:
            raise ValueError("original_financial_junit_invalid")
        status = next(
            (
                tag
                for tag in ("failure", "error", "skipped")
                if case.find(tag) is not None
            ),
            "passed",
        )
        seconds = float(case.get("time", "nan"))
        if not math.isfinite(seconds) or not 0 <= seconds <= 3600:
            raise ValueError("original_financial_junit_invalid")
        properties = case.find("properties")
        items = [] if properties is None else list(properties)
        if len(items) > 16 or any(item.tag != "property" for item in items):
            raise ValueError("original_financial_junit_invalid")
        selected = {}
        for item in items:
            key = item.get("name")
            if key in {
                "ctcc_pg52_original_financial_closed_code_v1",
                "ctcc_pg55_original_financial_closed_failure_v1",
            }:
                if key in selected:
                    raise ValueError("original_financial_junit_invalid")
                selected[key] = item.get("value")
        row = {
            "selection_index": index,
            "node_sha256": _sha(NODES[index].encode("ascii")),
            "status": status,
            "elapsed_seconds": round(seconds, 3),
        }
        if index == 0 and "ctcc_pg52_original_financial_closed_code_v1" in selected:
            code = selected["ctcc_pg52_original_financial_closed_code_v1"]
            if code not in _FIRST_CODES:
                raise ValueError("original_financial_junit_invalid")
            row["closed_code"] = code
        if index == 1 and "ctcc_pg55_original_financial_closed_failure_v1" in selected:
            row["closed_report"] = _closed_second(
                selected["ctcc_pg55_original_financial_closed_failure_v1"]
            )
        safe.append(row)
    return safe, _sha(raw)


def redacted_diagnostic(progress_raw: bytes | None, junit_raw: bytes | None) -> dict:
    """Return only fixed, bounded evidence even when pytest is interrupted."""
    progress, progress_sha = _progress(progress_raw)
    cases, junit_sha = _junit(junit_raw)
    completed = (
        cases is not None
        and len(progress) == 4
        and [row["event"] for row in progress]
        == ["case_start", "case_finish", "case_start", "case_finish"]
        and all(case["status"] == "passed" for case in cases)
    )
    return {
        "schema": "ctcc.current0056.original_financial_pg_diagnostic.v1",
        "status": "PASS_DIAGNOSTIC_ONLY" if completed else "INCOMPLETE",
        "source_test_node_sha256": [_sha(node.encode("ascii")) for node in NODES],
        "progress_sha256": progress_sha,
        "junit_sha256": junit_sha,
        "progress": progress,
        "cases": cases,
        "full_regression_passed": False,
        "gate3_accepted": False,
        "execution_authority": False,
        "provider_requests": 0,
        "order_writes": 0,
    }
