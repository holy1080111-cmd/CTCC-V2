"""Offline checks of the diagnostic observer and its bounded public wire."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from scripts import pg_account_bootstrap_diagnostic as contract
from scripts import pg_account_bootstrap_trace as plugin
from scripts import verify_current0056_hermetic as runner


def _start(monkeypatch):
    stream = io.BytesIO()

    class MemoryStream:
        def write(self, raw):
            return stream.write(raw)

        def flush(self):
            return None

        def fileno(self):
            return 123

    monkeypatch.setattr(plugin, "_stream", MemoryStream())
    monkeypatch.setattr(plugin, "_sequence", 0)
    monkeypatch.setattr(plugin, "_active", None)
    monkeypatch.setattr(plugin.os, "fsync", lambda fd: None)
    plugin.pytest_runtest_logstart(contract.NODES[0], None)
    return stream


def _finish_node(node):
    for when in ("setup", "call", "teardown"):
        plugin.pytest_runtest_logreport(SimpleNamespace(when=when, outcome="passed"))
    plugin.pytest_runtest_logfinish(node, None)


@pytest.mark.asyncio
async def test_original_await_called_once_and_timeout_survives(monkeypatch):
    stream = _start(monkeypatch)
    active = plugin._Capture(1, "regional")
    calls = 0
    token = "private-value-never-exported"

    async def original():
        nonlocal calls
        calls += 1
        raise TimeoutError(token)

    with pytest.raises(TimeoutError, match=token):
        await plugin._observe(active, "bootstrap_recorded", original(), {})
    assert calls == 1
    records, complete, _ = contract.read_trace(stream.getvalue())
    assert not complete
    assert records[-1]["disposition"] == "deadline_timeout"
    assert records[-1]["fixed_code"] is None
    assert token.encode() not in stream.getvalue()


@pytest.mark.asyncio
async def test_nested_timeout_preserves_exact_phase_and_partial_prefix(monkeypatch):
    stream = _start(monkeypatch)
    active = plugin._Capture(1, "current_v7")

    async def source_close():
        active.script_request_ordinal = 17
        active.journal_sequence = 43
        active.journal_last_completed = "raw_finalized"
        raise asyncio.CancelledError

    async def capture():
        try:
            await plugin._observe(active, "source_close", source_close(), {})
        except asyncio.CancelledError:
            return "unchanged"
        raise AssertionError("missing cancellation")

    async def recorded():
        return await plugin._observe(active, "bootstrap_capture", capture(), {})

    assert (
        await plugin._observe(active, "bootstrap_recorded", recorded(), {})
        == "unchanged"
    )
    records, complete, _ = contract.read_trace(stream.getvalue() + b'{"torn":')
    assert not complete
    failure = next(row for row in records if row.get("disposition") == "cancelled")
    assert (failure["phase"], failure["plan_kind"]) == ("source_close", "current_v7")
    assert failure["script_request_ordinal"] == 17
    assert (failure["journal_sequence"], failure["journal_last_completed"]) == (
        43,
        "raw_finalized",
    )
    malformed = [*records[:-1], {**records[-1], "phase": "terminal"}]
    with pytest.raises(ValueError, match="trace_invalid"):
        contract.read_trace(
            b"".join(json.dumps(row).encode() + b"\n" for row in malformed)
        )


def test_junit_reduction_requires_exact_original_case_roster():
    cases = "".join(
        f'<testcase classname="{module}" name="{name}">'
        + ('<failure message="sensitive">detail</failure>' if index == 0 else "")
        + "</testcase>"
        for index, (module, name) in enumerate(contract.CASES)
    )
    full = f"<testsuites><testsuite>{cases}</testsuite></testsuites>".encode()
    assert tuple(contract.read_junit(full).values()) == ("failed", "passed", "passed")
    with pytest.raises(ValueError, match="node_invalid"):
        contract.read_junit(full.replace(b"test_postgresql_typed", b"test_unexpected"))


def test_trace_requires_fixed_order_and_report_closure(monkeypatch):
    stream = _start(monkeypatch)
    _finish_node(contract.NODES[0])
    for node in contract.NODES[1:]:
        plugin.pytest_runtest_logstart(node, None)
        _finish_node(node)
    records, complete, outcomes = contract.read_trace(stream.getvalue())
    assert complete
    assert set(outcomes.values()) == {"passed"}
    assert len(records) == 15
    with pytest.raises(ValueError, match="trace_invalid"):
        contract.read_trace(
            stream.getvalue().replace(
                contract.NODE_DIGESTS[0].encode(), contract.NODE_DIGESTS[1].encode()
            )
        )


def test_exact_diagnostic_mode_excludes_other_component_or_profile(monkeypatch):
    argv = [
        "verify_current0056_hermetic",
        "--archive",
        "synthetic.tar",
        "--archive-sha256",
        "a" * 64,
        "--commit",
        "b" * 40,
        "--tree",
        "c" * 40,
        "--output",
        "synthetic-out",
        "--suite",
        "postgres",
        "--postgres-component-index",
        "1",
        "--account-bootstrap-diagnostic",
    ]
    monkeypatch.setattr("sys.argv", argv)
    parsed, index = runner._arguments()
    assert parsed.account_bootstrap_diagnostic is True and index == 1
    for invalid in (
        [*argv[:-2], "0", argv[-1]],
        [*argv, "--fitted-catalog-diagnostic"],
    ):
        monkeypatch.setattr("sys.argv", invalid)
        with pytest.raises(ValueError, match="runner_arguments_invalid"):
            runner._arguments()
