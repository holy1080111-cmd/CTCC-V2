"""Closed retained evidence for the exact original DB0052 V3 financial case."""

import hashlib
import json
import xml.etree.ElementTree as ET

MODULE = "tests.integration.test_account_history_original_financial_native_pg_v3"
CASE = "test_pg_financial_distinct_sessions_original33_uses_same_expiry_and_measured_finish"
NODE = MODULE.replace(".", "/") + ".py::" + CASE
NODE_SHA256 = hashlib.sha256(NODE.encode()).hexdigest()
STAGE = "original-financial-v3-expiry"
TRACE = STAGE + ".log"
JUNIT = STAGE + ".xml"
EXPORT = STAGE + "-redacted.json"
SCHEMA = "ctcc.pg_original_financial_v3_expiry_trace.v1"
TRACE_LIMIT = 64 * 1024
TIME_LIMIT_NS = 30 * 60 * 10**9
PHASES = frozenset(
    {
        "capture_prepare",
        "initial_catalog",
        "rpc",
        "decode",
        "wire_to_page",
        "page_handoff",
        "page_replay",
        "page_accepted",
        "final_stream",
        "final_catalog",
        "financial_finish",
        "commit",
        "close",
        "receipt",
    }
)
ERRORS = frozenset(
    {
        "original_pg_original_expiry",
        "original_pg_clock_or_task_changed",
        "original_pg_read_uncertain",
        "original_pg_cleanup_uncertain",
        "original_financial_pg_component_changed",
        "original_financial_pg_policy_mismatch",
    }
)


def _pairs(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError("financial_v3_trace_invalid")
    return obj


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _valid(event, data):
    if event == "start":
        return data == {}
    if event == "budget":
        lease = data.get("persisted_lease_remaining_us")
        boundary = data.get("boundary")
        return (
            set(data)
            == {
                "original_wall_remaining_us",
                "original_monotonic_budget_ns",
                "persisted_lease_remaining_us",
                "boundary",
            }
            and _integer(data["original_wall_remaining_us"], 1, 20_000_000)
            and _integer(data["original_monotonic_budget_ns"], 1, 20_000_000_000)
            and abs(
                data["original_wall_remaining_us"] * 1000
                - data["original_monotonic_budget_ns"]
            )
            <= 1
            and (
                (boundary == "unobserved" and lease is None)
                or (
                    boundary == "control_lease"
                    and _integer(
                        lease,
                        data["original_wall_remaining_us"],
                        data["original_wall_remaining_us"],
                    )
                )
                or (
                    boundary == "request_cap"
                    and _integer(
                        lease, data["original_wall_remaining_us"] + 1, 300_000_000
                    )
                )
            )
        )
    if event == "phase":
        return (
            set(data) == {"page", "phase"}
            and _integer(data["page"], 0, 33)
            and data["phase"] in PHASES
            and (data["page"] > 0)
            == (
                data["phase"]
                in {
                    "rpc",
                    "decode",
                    "wire_to_page",
                    "page_handoff",
                    "page_replay",
                    "page_accepted",
                }
            )
        )
    if event == "fence_no_budget":
        return set(data) == {"wall_remaining_us"} and _integer(
            data["wall_remaining_us"], -(2**63), 0
        )
    if event == "fence_expiry":
        return (
            set(data)
            == {"page", "phase", "wall_remaining_us", "monotonic_remaining_ns", "cause"}
            and _integer(data["page"], 0, 33)
            and data["phase"] in PHASES
            and _integer(data["wall_remaining_us"], -(2**63), 20_000_000)
            and _integer(data["monotonic_remaining_ns"], -TIME_LIMIT_NS, 20_000_000_000)
            and data["cause"]
            == (
                "wall_and_monotonic"
                if data["wall_remaining_us"] <= 0
                and data["monotonic_remaining_ns"] <= 0
                else "wall"
                if data["wall_remaining_us"] <= 0
                else "monotonic"
                if data["monotonic_remaining_ns"] <= 0
                else None
            )
            and data["cause"] in {"wall", "monotonic", "wall_and_monotonic"}
        )
    return (
        event == "finish"
        and set(data) == {"outcome", "closed_code"}
        and data["outcome"] in {"returned", "exception", "cancelled"}
        and (
            data["closed_code"] is None
            or data["outcome"] == "exception"
            and data["closed_code"] in ERRORS
        )
    )


def read_trace(raw):
    """Validate a complete line prefix, preserving a crash as incomplete."""
    if type(raw) is not bytes or not 0 < len(raw) <= TRACE_LIMIT:
        raise ValueError("financial_v3_trace_invalid")
    body, _, tail = raw.rpartition(b"\n")
    events = []
    prior = -1
    seen, phase, page, last_page = set(), None, 0, 0
    try:
        for sequence, line in enumerate(body.splitlines(), 1):
            item = json.loads(line, object_pairs_hook=_pairs)
            if (
                type(item) is not dict
                or set(item)
                != {"schema", "node_sha256", "sequence", "elapsed_ns", "event", "data"}
                or item["schema"] != SCHEMA
                or item["node_sha256"] != NODE_SHA256
                or not _integer(item["sequence"], sequence, sequence)
                or sequence > 300
                or not _integer(item["elapsed_ns"], max(0, prior), TIME_LIMIT_NS)
                or type(item["data"]) is not dict
                or not _valid(item["event"], item["data"])
                or ("finish" in seen)
                or (sequence == 1) != (item["event"] == "start")
                or (
                    item["event"] in {"budget", "fence_expiry", "fence_no_budget"}
                    and item["event"] in seen
                )
            ):
                raise ValueError
            event, data = item["event"], item["data"]
            if event == "phase":
                phase, page = data["phase"], data["page"]
                if page and not last_page <= page <= last_page + 1:
                    raise ValueError
                if page:
                    last_page = page
            if event in {"budget", "fence_no_budget"} and (
                phase != "capture_prepare" or seen & {"budget", "fence_no_budget"}
            ):
                raise ValueError
            if event == "fence_expiry" and (
                "budget" not in seen or (data["phase"], data["page"]) != (phase, page)
            ):
                raise ValueError
            if (
                event == "finish"
                and data["outcome"] == "returned"
                and (
                    "budget" not in seen
                    or phase != "receipt"
                    or last_page != 33
                    or "fence_expiry" in seen
                    or "fence_no_budget" in seen
                )
            ):
                raise ValueError
            seen.add(event)
            prior = item["elapsed_ns"]
            events.append(item)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("financial_v3_trace_invalid") from None
    if not events:
        raise ValueError("financial_v3_trace_invalid")
    return events, not tail and "finish" in seen


def read_junit(raw):
    """Select only the original case disposition, never assertion/driver text."""
    if type(raw) is not bytes or not 0 < len(raw) <= TRACE_LIMIT or b"<!" in raw:
        raise ValueError("financial_v3_junit_invalid")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("financial_v3_junit_invalid") from None
    cases = list(root.iter("testcase"))
    if len(cases) != 1 or (
        cases[0].get("classname"),
        cases[0].get("name"),
    ) != (MODULE, CASE):
        raise ValueError("financial_v3_junit_invalid")
    tags = [
        child.tag for child in cases[0] if child.tag in {"failure", "error", "skipped"}
    ]
    if len(tags) > 1:
        raise ValueError("financial_v3_junit_invalid")
    return tags[0] if tags else "passed"
