"""Offline probes of the actual monitor and redacted evidence boundary."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.repositories import account_history_original_financial_read_v3 as v3
from app.database.repositories import account_history_original_native_read_v1 as native
from app.domain import account_history_partition_v1 as records
from scripts import pg_financial_v3_expiry as evidence
from scripts import pg_financial_v3_expiry_trace as plugin
from tests.integration import test_account_history_original_native_read_v1 as original


def _wire_rows(rows):
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def _events(*rows):
    return [
        {
            "schema": evidence.SCHEMA,
            "node_sha256": evidence.NODE_SHA256,
            "sequence": sequence,
            "elapsed_ns": sequence * 100,
            "event": event,
            "data": data,
        }
        for sequence, (event, data) in enumerate(rows, 1)
    ]


async def test_monitor_executes_real_fence_and_observes_actual_deadlines():
    trace = plugin._Trace(original._request)
    trace.start = plugin.time.monotonic_ns()
    trace._record("start")
    trace._phase("capture_prepare", paged=False)
    with trace.monitor():
        lease = datetime.now(UTC) + timedelta(seconds=5)
        request = original._request(
            records.HistoryPartitionScopeV1("demo", "730048", "USDT"),
            (b"synthetic",),
            (1, 0, 0),
            lease,
        )
        fence = native._Fence(request.expires_at)
        fence.check()
        # Advance only this synthetic instance's observed original deadline
        # for a red test. The monitoring code never mutates a production fence.
        fence.monotonic_deadline = fence.monotonic_ns
        with pytest.raises(native.OriginalHistoryPgReadError, match="original_expiry"):
            fence.check()
    assert not trace.broken
    rows, complete = evidence.read_trace(_wire_rows(trace.records))
    assert not complete
    assert [row["event"] for row in rows] == [
        "start",
        "phase",
        "budget",
        "fence_expiry",
    ]
    budget = rows[-2]["data"]
    assert budget["boundary"] == "control_lease"
    assert (
        budget["persisted_lease_remaining_us"] == budget["original_wall_remaining_us"]
    )
    assert rows[-1]["data"]["cause"] == "monotonic"
    assert rows[-1]["data"]["wall_remaining_us"] > 0
    assert rows[-1]["data"]["monotonic_remaining_ns"] <= 0


async def test_monitor_captures_actual_fixed_v3_raise_without_database():
    trace = plugin._Trace()
    with (
        trace.monitor(),
        pytest.raises(
            v3.OriginalHistoryFinancialPgReadError, match="original_pg_request_invalid"
        ),
    ):
        await v3.replay_original_history_financial_from_pg_v3(
            None,
            genesis_request=None,
            expected_checkpoint_sha256=None,
            approved_collector_role_oid=0,
            expected_financial_policy_sha256=None,
        )
    assert not trace.broken
    assert trace.finish == {"outcome": "exception", "closed_code": None}


def test_closed_trace_preserves_only_verified_phase_and_signed_budget():
    rows = _events(
        ("start", {}),
        ("phase", {"phase": "capture_prepare", "page": 0}),
        (
            "budget",
            {
                "original_wall_remaining_us": 20_000_000,
                "original_monotonic_budget_ns": 20_000_000_000,
                "persisted_lease_remaining_us": 20_000_123,
                "boundary": "request_cap",
            },
        ),
        ("phase", {"phase": "rpc", "page": 1}),
        (
            "fence_expiry",
            {
                "page": 1,
                "phase": "rpc",
                "wall_remaining_us": -9,
                "monotonic_remaining_ns": -312,
                "cause": "wall_and_monotonic",
            },
        ),
        (
            "finish",
            {"outcome": "exception", "closed_code": "original_pg_original_expiry"},
        ),
    )
    _, complete = evidence.read_trace(_wire_rows(rows))
    assert complete
    _, complete = evidence.read_trace(_wire_rows(rows[:-1]) + b'{"torn":')
    assert not complete
    for key, value in (
        ("cause", "private-value"),
        ("page", 2),
        ("wall_remaining_us", 120),
    ):
        altered = [*rows]
        altered[-2] = {**rows[-2], "data": {**rows[-2]["data"], key: value}}
        with pytest.raises(ValueError, match="trace_invalid"):
            evidence.read_trace(_wire_rows(altered))


def test_junit_reduction_emits_no_raw_failure_details():
    raw = (
        f'<testsuites><testsuite><testcase classname="{evidence.MODULE}" name="{evidence.CASE}">'
        ' <failure message="secret-looking-value">private</failure>'
        "</testcase></testsuite></testsuites>"
    ).encode()
    assert evidence.read_junit(raw) == {
        "outcome": "failure",
        "exception_class": None,
        "closed_code": None,
    }
    fixed = raw.replace(
        b"secret-looking-value",
        b"RuntimeError: financial_v3_trace_module_changed secret-looking-value",
    )
    result = evidence.read_junit(fixed)
    assert result == {
        "outcome": "failure",
        "exception_class": "RuntimeError",
        "closed_code": "financial_v3_trace_module_changed",
    }
    assert "secret-looking-value" not in json.dumps(result)
    result = evidence.read_junit(
        fixed.replace(
            b"financial_v3_trace_module_changed",
            b"previously_unclassified_dynamic_token",
        )
    )
    assert result["exception_class"] == "RuntimeError" and result["closed_code"] is None
    with pytest.raises(ValueError, match="junit_invalid"):
        evidence.read_junit(raw.replace(evidence.CASE.encode(), b"other_case"))


@pytest.mark.parametrize("qualified", [False, True])
def test_same_collected_module_path_admits_either_pytest_import_name(
    monkeypatch, qualified
):
    monkeypatch.setattr(plugin.sys, "gettrace", lambda: None)
    root = Path(plugin.__file__).resolve().parents[1]
    path = root / evidence.NODE.split("::")[0]
    request = original._request
    real = plugin._Trace
    observed = []

    def trace(request_builder):
        observed.append(request_builder)
        return real(request_builder)

    monkeypatch.setattr(plugin, "_Trace", trace)
    module = SimpleNamespace(
        __name__=evidence.MODULE if qualified else path.stem,
        __file__=str(path),
        original=SimpleNamespace(_request=request),
    )
    item = SimpleNamespace(nodeid=evidence.NODE, module=module, path=path)
    hook = plugin.pytest_runtest_call(item)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    assert observed == [request]

    item.module.__file__ = original.__file__
    with pytest.raises(RuntimeError, match="trace_module_changed"):
        next(plugin.pytest_runtest_call(item))


def test_monitor_can_close_all_33_pages_without_losing_scoped_order():
    timeline = [
        ("start", {}),
        ("phase", {"phase": "capture_prepare", "page": 0}),
        (
            "budget",
            {
                "original_wall_remaining_us": 18_000_000,
                "original_monotonic_budget_ns": 18_000_000_000,
                "persisted_lease_remaining_us": 18_000_000,
                "boundary": "control_lease",
            },
        ),
        ("phase", {"phase": "initial_catalog", "page": 0}),
    ]
    for page in range(1, 34):
        timeline.extend(
            ("phase", {"phase": phase, "page": page})
            for phase in (
                "rpc",
                "decode",
                "wire_to_page",
                "page_handoff",
                "page_replay",
                "page_accepted",
            )
        )
    timeline.extend(
        ("phase", {"phase": phase, "page": 0})
        for phase in (
            "final_stream",
            "final_catalog",
            "financial_finish",
            "commit",
            "close",
            "receipt",
        )
    )
    timeline.append(("finish", {"outcome": "returned", "closed_code": None}))
    _, complete = evidence.read_trace(_wire_rows(_events(*timeline)))
    assert complete
    missing_page = [row for row in timeline if row[1].get("page") != 13]
    with pytest.raises(ValueError, match="trace_invalid"):
        evidence.read_trace(_wire_rows(_events(*missing_page)))
