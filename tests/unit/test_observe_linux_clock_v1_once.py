"""CLI lifecycle/error reporting through explicit synthetic native API seams."""

import ast
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain import linux_native_clock_v1 as native
from app.domain.source_primitives import PublicReceiptError, canonical
from scripts import observe_linux_clock_v1_once as probe


@pytest.fixture
def api_seam(monkeypatch, capsys):
    raw = bytes(208)
    status = {
        "return_state": 0,
        "errno": 0,
        "raw_base64": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_length": 208,
    }
    evidence = {
        "profile_id": "synthetic-cli-only",
        "pll_updates_enabled": False,
        "status": status,
    }
    state = SimpleNamespace(
        calls=[],
        token=object(),
        evidence=evidence,
        payload=canonical(evidence),
        fail=None,
        allow_reuse=False,
    )

    def observe():
        state.calls.append("observe")
        if state.fail is not None:
            raise state.fail
        return state.token

    def consume(token):
        assert token is state.token
        state.calls.append("consume")
        if state.calls.count("consume") > 1 and not state.allow_reuse:
            raise native.LinuxClockError("linux_clock_owned_observation_required")
        return state.payload

    def replay(payload):
        assert payload == state.payload
        state.calls.append("replay")
        return state.evidence

    monkeypatch.setattr(
        probe,
        "clock",
        SimpleNamespace(
            MAX_PAYLOAD_BYTES=native.MAX_PAYLOAD_BYTES,
            LinuxClockError=native.LinuxClockError,
            LinuxKernelStatusError=native.LinuxKernelStatusError,
            observe_linux_clock_v1=observe,
            consume_owned_linux_clock_v1=consume,
            replay_linux_clock_observation_v1=replay,
        ),
    )
    monkeypatch.setattr(probe.sys, "argv", ["observe_linux_clock_v1_once.py"])
    return state


def _record(capsys):
    import json

    captured = capsys.readouterr()
    assert not captured.err and captured.out.count("\n") == 1
    assert len(captured.out.encode("ascii")) <= probe.MAX_OUTPUT_BYTES
    record = json.loads(captured.out)
    assert record["scope"] == "read_only_kernel_observation_only"
    assert record["utc_certified"] is False
    assert record["upstream_freshness_verified"] is False
    assert record["source_authenticity_verified"] is False
    assert record["historical_availability_verified"] is False
    assert record["execution_authority"] is False
    assert record["upstream_server"] is None and record["last_upstream_sync_at"] is None
    return record


def test_once_observe_consume_replay_then_second_consume_denies(api_seam, capsys):
    assert probe.main() == 0
    record = _record(capsys)
    assert api_seam.calls == ["observe", "consume", "replay", "consume"]
    assert record["observation_attempts"] == 1 and record["consume_attempts"] == 2
    assert (
        record["actual_accepted"] is True and record["owned_token_reuse_denied"] is True
    )
    assert record["error_code"] is None
    assert record["original_raw_status_available"] is True
    assert (
        record["original_raw_status_sha256"] == hashlib.sha256(bytes(208)).hexdigest()
    )
    original = base64.b64decode(record["original_evidence_base64"], validate=True)
    assert (
        original == api_seam.payload
        and len(original) == record["original_evidence_length"]
    )
    assert hashlib.sha256(original).hexdigest() == record["evidence_sha256"]


def test_arguments_deny_before_native_api_without_echo(api_seam, capsys):
    probe.sys.argv.append("private-caller-content")
    assert probe.main() == 2
    record = _record(capsys)
    assert record["error_code"] == "linux_clock_probe_arguments_forbidden"
    assert record["observation_attempts"] == 0 and api_seam.calls == []
    assert "private-caller-content" not in str(record)


def test_native_query_failure_keeps_only_fixed_code_and_original_raw_hash(
    api_seam, capsys
):
    status = {**api_seam.evidence["status"], "return_state": -1, "errno": 1}
    api_seam.fail = native.LinuxKernelStatusError(
        "linux_clock_native_api_failed", status
    )
    assert probe.main() == 1
    record = _record(capsys)
    assert api_seam.calls == ["observe"]
    assert record["error_code"] == "linux_clock_native_api_failed"
    assert record["original_raw_status_available"] is True
    assert (
        record["original_status_record_sha256"]
        == hashlib.sha256(canonical(status)).hexdigest()
    )
    assert base64.b64decode(
        record["original_status_record_base64"], validate=True
    ) == canonical(status)
    assert record["original_evidence_base64"] is None
    assert record["actual_accepted"] is False


@pytest.mark.parametrize(
    "error,code",
    [
        (
            RuntimeError("sensitive exception text"),
            "linux_clock_probe_unexpected_failure",
        ),
        (
            native.LinuxClockError("sensitive exception text"),
            "linux_clock_probe_unclassified_clock_error",
        ),
        (PublicReceiptError("clock_jump"), "clock_jump"),
    ],
)
def test_rejection_has_one_attempt_no_traceback_or_exception_echo(
    api_seam, capsys, error, code
):
    api_seam.fail = error
    assert probe.main() == 1
    record = _record(capsys)
    assert api_seam.calls == ["observe"]
    assert record["error_code"] == code
    assert "sensitive exception text" not in str(record)
    assert record["original_raw_status_available"] is False


def test_unexpected_token_reuse_success_is_not_a_pass(api_seam, capsys):
    api_seam.allow_reuse = True
    assert probe.main() == 1
    record = _record(capsys)
    assert record["actual_accepted"] is False
    assert record["owned_token_reuse_denied"] is False
    assert record["error_code"] == "linux_clock_probe_token_reuse_accepted"
    assert api_seam.calls == ["observe", "consume", "replay", "consume"]


def test_oversize_evidence_is_denied_without_retaining_or_replaying_it(
    api_seam, capsys
):
    api_seam.payload = b"x" * (native.MAX_PAYLOAD_BYTES + 1)
    assert probe.main() == 1
    record = _record(capsys)
    assert api_seam.calls == ["observe", "consume"]
    assert record["actual_accepted"] is False
    assert record["original_evidence_base64"] is None
    assert record["error_code"] == "linux_clock_evidence_invalid"


def test_output_budget_failure_does_not_silently_truncate_evidence(api_seam, capsys):
    api_seam.evidence["profile_id"] = "x" * (probe.MAX_OUTPUT_BYTES + 1)
    assert probe.main() == 1
    record = _record(capsys)
    assert record["actual_accepted"] is False
    assert record["error_code"] == "linux_clock_probe_output_budget"
    assert record["original_evidence_base64"] is None
    assert record["original_raw_status_available"] is False


def test_cli_ast_has_no_external_io_or_argument_profile_surface():
    source = Path(probe.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imports <= {"base64", "hashlib", "json", "sys"}
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    assert (
        not main.args.args
        and not main.args.kwonlyargs
        and main.args.vararg is None
        and main.args.kwarg is None
    )
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"open", "input", "eval", "exec"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("return_state", True),
        ("return_state", -2),
        ("return_state", 6),
        ("return_state", "0"),
        ("errno", False),
        ("errno", -1),
        ("errno", 4096),
        ("errno", "1"),
    ],
)
def test_malformed_kernel_record_does_not_get_original_raw_available_marker(
    api_seam, field, value
):
    status = {**api_seam.evidence["status"], field: value}
    markers = probe._status_markers(canonical(status))
    assert markers["original_raw_status_available"] is False
    assert markers["original_status_record_base64"] is None
    assert markers["original_raw_status_sha256"] is None
