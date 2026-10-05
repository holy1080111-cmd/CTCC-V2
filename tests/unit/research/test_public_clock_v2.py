"""Synthetic parser/process boundary checks; not healthy native-clock evidence."""

from __future__ import annotations

import base64
import copy
import io
import itertools
import subprocess
import time
from types import SimpleNamespace

import pytest

from app.domain import native_clock as clock
from app.public_market_source import public_market_capture as capture
from app.public_market_source.public_market_receipts import (
    PublicReceiptError,
    canonical,
    decode,
    sha,
)
from tests.unit.research.public_receipt_fixtures import SyntheticCapture, os_evidence

EN = (
    "Leap Indicator: 0(no warning)\nStratum: 3 (secondary reference)\n"
    "Source: time.windows.com,0x8\nLast Sync Error: 0 (The command completed successfully.)\n"
    "Time since Last Good Sync Time: 10.1250000s\n"
)
ZH = (
    "躍進式指示器: 0(沒有警告)\n組織層: 3 (次要參照)\n來源: time.windows.com,0x8\n"
    "上次同步處理錯誤: 0 (命令已經成功完成。)\n自上次良好同步處理時間後的時間: 10.1250000s\n"
)


def stream(raw):
    return {
        "base64": base64.b64encode(raw).decode("ascii"),
        "length": len(raw),
        "sha256": sha(raw),
        "truncated": False,
    }


def stamp(n=100):
    return {"utc_ns": 1_000_000_000 + n, "monotonic_ns": n}


def probe(raw, command_id, encoding):
    return {
        "schema_version": "ctcc.windows_clock_raw_probe.v1",
        "command_id": command_id,
        "encoding": encoding,
        "request_start": stamp(),
        "completed": stamp(),
        "outcome": "complete",
        "exit_code": 0,
        "stdout": stream(raw),
        "stderr": stream(b""),
    }


def fixture(language="zh"):
    host = {
        "service_state": 4,
        "service_start_type": 2,
        "timezone_id": "Taipei Standard Time",
        "utc_offset_minutes": 480,
        "files": copy.deepcopy(clock._FILES),
    }
    data = {
        "schema_version": "ctcc.windows_clock_diagnostic.v2",
        "profile_id": clock.PROFILE_ID,
        "resource_ids": list(clock.RESOURCE_IDS),
        "clock_domain": copy.deepcopy(clock._CLOCK_DOMAIN),
        "host_before": probe(canonical(host), "host_metadata.v2", "utf-8"),
        "status": probe(
            (ZH if language == "zh" else EN).encode("cp950"), "w32tm_status.v2", "cp950"
        ),
        "host_after": probe(canonical(host), "host_metadata.v2", "utf-8"),
    }
    return resign(
        {
            "schema_version": "ctcc.windows_clock_observation.v2",
            "sample": stamp(),
            "diagnostic": data,
            "diagnostic_sha256": "",
        }
    )


def resign(evidence):
    evidence["diagnostic_sha256"] = sha(canonical(evidence["diagnostic"]))
    return evidence


def host_change(evidence, key, value, *, side="host_before"):
    record = evidence["diagnostic"][side]
    data = decode(base64.b64decode(record["stdout"]["base64"]))
    data[key] = value
    record["stdout"] = stream(canonical(data))


def status_change(evidence, status):
    evidence["diagnostic"]["status"]["stdout"] = stream(status.encode("cp950"))


def test_v1_fixed_golden_bytes_and_identity_remain_unchanged():
    evidence = os_evidence({"utc_ns": 100, "monotonic_ns": 100})
    before = canonical(evidence)
    assert (
        sha(before)
        == "95e6c503ca53fd61ae695004229634ac1be24ec36ae95b308a5e5424b4765965"
    )
    assert clock.validate_os_clock(evidence) is evidence
    assert canonical(evidence) == before
    assert clock.clock_timezone(evidence) == ("Taipei Standard Time", 480)


@pytest.mark.parametrize("language", ["en", "zh"])
def test_v2_exact_profile_raw_bytes_pure_replay(language):
    evidence = fixture(language)
    before = canonical(evidence)
    assert clock.validate_os_clock(evidence) is evidence
    assert canonical(evidence) == before
    assert clock.clock_timezone(evidence) == ("Taipei Standard Time", 480)
    assert base64.b64decode(evidence["diagnostic"]["status"]["stdout"]["base64"]) == (
        ZH if language == "zh" else EN
    ).encode("cp950")


@pytest.mark.parametrize(
    "key,value",
    [
        ("service_state", 1),
        ("service_state", True),
        ("service_state", "4"),
        ("service_start_type", 3),
        ("service_start_type", "2"),
        ("timezone_id", ""),
        ("timezone_id", None),
        ("utc_offset_minutes", 841),
        ("utc_offset_minutes", True),
    ],
)
@pytest.mark.parametrize("side", ["host_before", "host_after"])
def test_resigned_host_gate_failures(key, value, side):
    evidence = fixture()
    host_change(evidence, key, value, side=side)
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize(
    "mutation",
    ["sha", "version", "signature", "signature_bool", "missing", "extra", "order"],
)
def test_pinned_files_are_exact_even_when_every_receipt_hash_is_recomputed(mutation):
    evidence = fixture()
    files = copy.deepcopy(clock._FILES)
    if mutation == "sha":
        files[0]["sha256"] = "a" * 64
    elif mutation == "version":
        files[0]["version"] = "10.0.26100.1"
    elif mutation == "signature":
        files[0]["signature_status"] = 1
    elif mutation == "signature_bool":
        files[0]["signature_status"] = False
    elif mutation == "missing":
        files.pop()
    elif mutation == "extra":
        files[0]["verified"] = True
    else:
        files.reverse()
    for side in ("host_before", "host_after"):
        host_change(evidence, "files", files, side=side)
    with pytest.raises(PublicReceiptError, match="profile_unsupported"):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize(
    "status",
    [
        ZH + "Leap Indicator: 0(no warning)\n",
        EN + "來源: time.windows.com,0x8\n",
        ZH + "組織層: 3 (次要參照)\n",
        EN.replace("Leap Indicator", "Leap indicator"),
        ZH.replace("躍進式指示器:", "躍進式指示器："),
        EN.replace("0(no warning)", "3(not synchronized)"),
        EN.replace("Stratum: 3", "Stratum: 0"),
        EN.replace("Stratum: 3", "Stratum: 16"),
        EN.replace("Last Sync Error: 0", "Last Sync Error: 1"),
        EN.replace("time.windows.com,0x8", "Local CMOS Clock"),
        EN.replace("time.windows.com,0x8", "LOCL"),
        EN.replace("10.1250000s", "10.125s"),
        EN.replace("10.1250000s", "-1.0000000s"),
        EN.replace("10.1250000s", "10,1250000s"),
        EN.replace("10.1250000s", "010.1250000s"),
        EN.replace("10.1250000s", "900.0000001s"),
        EN.replace("10.1250000s", "10.1250000秒"),
        EN + "\x00",
        EN + "Source: another.example\n",
    ],
)
def test_localized_required_fields_fail_closed(status):
    evidence = fixture()
    status_change(evidence, status)
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


def test_age_900_boundary_and_measurement_lag_are_not_relaxed():
    evidence = fixture()
    status_change(evidence, ZH.replace("10.1250000s", "900.0000000s"))
    clock.validate_os_clock(resign(evidence))
    evidence["sample"] = stamp(101)
    with pytest.raises(PublicReceiptError, match="os_sync_expired"):
        clock.validate_os_clock(evidence)


@pytest.mark.parametrize(
    "raw", [b"\x81", b"\x81\x30", ZH.encode("utf-8"), EN.encode() + b"\x00"]
)
def test_no_guessing_or_replacement_decode(raw):
    evidence = fixture()
    evidence["diagnostic"]["status"]["stdout"] = stream(raw)
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize(
    "key,value",
    [
        ("outcome", "timeout"),
        ("outcome", "incomplete"),
        ("outcome", "overflow"),
        ("exit_code", False),
        ("exit_code", 0x80070426),
        ("exit_code", -2147023834),
        ("encoding", "utf-8"),
        ("command_id", "caller_override"),
    ],
)
def test_status_transport_rejections(key, value):
    evidence = fixture()
    evidence["diagnostic"]["status"][key] = value
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize(
    "mutation",
    [
        "hash",
        "length",
        "bool_length",
        "invalid_base64",
        "truncated",
        "oversize",
        "stderr",
    ],
)
def test_raw_stream_binding_cannot_be_bypassed_with_outer_hash(mutation):
    evidence = fixture()
    record = evidence["diagnostic"]["status"]
    if mutation == "hash":
        record["stdout"]["sha256"] = "a" * 64
    elif mutation == "length":
        record["stdout"]["length"] += 1
    elif mutation == "bool_length":
        record["stderr"]["length"] = False
    elif mutation == "invalid_base64":
        record["stdout"]["base64"] += "!"
    elif mutation == "truncated":
        record["stdout"]["truncated"] = True
    elif mutation == "oversize":
        record["stdout"] = stream(b"x" * (clock._MAX_OUTPUT + 1))
    else:
        record["stderr"] = stream(b"real failure")
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize(
    "mutation", ["top", "nested", "profile", "ids", "extra", "unknown_timezone_schema"]
)
def test_version_markers_are_not_interchangeable(mutation):
    evidence = fixture()
    if mutation == "top":
        evidence["schema_version"] = "ctcc.windows_clock_observation.v1"
    elif mutation == "nested":
        evidence["diagnostic"]["schema_version"] = "v1"
    elif mutation == "profile":
        evidence["diagnostic"]["profile_id"] = "generic_windows"
    elif mutation == "ids":
        evidence["diagnostic"]["resource_ids"][-1] = 2555
    elif mutation == "extra":
        evidence["diagnostic"]["passed"] = True
    else:
        evidence["schema_version"] = "unknown"
    with pytest.raises(PublicReceiptError):
        clock.clock_timezone(resign(evidence))


@pytest.mark.parametrize("change", ["reversal", "jump", "expired", "timezone"])
def test_cross_probe_chronology_and_host_change(change):
    evidence = fixture()
    if change == "reversal":
        evidence["sample"] = stamp(99)
    elif change == "jump":
        evidence["sample"]["utc_ns"] += 5_000_001
    elif change == "expired":
        evidence["sample"] = stamp(5_000_000_101)
    else:
        host_change(evidence, "timezone_id", "UTC", side="host_after")
    with pytest.raises(PublicReceiptError):
        clock.validate_os_clock(resign(evidence))


def fake_native(monkeypatch, tmp_path, observations):
    executable = tmp_path / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic placeholder; never executed")
    monkeypatch.setattr(clock, "_system_directory", lambda: tmp_path)
    monkeypatch.setattr(
        clock, "_windows_clock_domain", lambda: dict(clock._CLOCK_DOMAIN)
    )
    calls = []
    values = iter(observations)

    def capture(command, command_id, encoding, deadline):
        calls.append((command, command_id, encoding))
        return next(values)

    monkeypatch.setattr(clock, "_capture_command", capture)
    monkeypatch.setattr(clock, "native_stamp", stamp)
    return calls


def test_synthetic_native_stopped_manual_retains_failed_raw_without_admission(
    monkeypatch, tmp_path
):
    evidence = fixture()
    for side in ("host_before", "host_after"):
        host_change(evidence, "service_state", 1, side=side)
        host_change(evidence, "service_start_type", 3, side=side)
    status = evidence["diagnostic"]["status"]
    raw = "發生下列錯誤: 服務尚未啟動。 (0x80070426)\n".encode("cp950")
    status["stdout"] = stream(raw)
    status["exit_code"] = 0x80070426
    calls = fake_native(
        monkeypatch,
        tmp_path,
        [evidence["diagnostic"][k] for k in ("host_before", "status", "host_after")],
    )
    with pytest.raises(
        clock.NativeClockObservationError, match="unsynchronized"
    ) as failure:
        clock.native_os_clock()
    retained = decode(failure.value.observation_bytes)
    assert base64.b64decode(retained["diagnostic"]["status"]["stdout"]["base64"]) == raw
    assert "服務" not in str(failure.value)
    assert len(calls) == 3
    assert calls[1][0][1:] == ["/query", "/status", "/verbose"]


def test_unreviewed_native_binary_profile_prevents_even_readonly_status_execution(
    monkeypatch, tmp_path
):
    evidence = fixture()
    files = copy.deepcopy(clock._FILES)
    files[0]["sha256"] = "a" * 64
    host_change(evidence, "files", files)
    calls = fake_native(monkeypatch, tmp_path, [evidence["diagnostic"]["host_before"]])
    with pytest.raises(clock.NativeClockObservationError, match="profile_unsupported"):
        clock.native_os_clock()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mode", ["normal", "overflow", "timeout", "stderr", "clock_failure"]
)
def test_bounded_subprocess_capture_retains_actual_read_bytes_in_synthetic_process(
    monkeypatch, mode
):
    stdout = (
        b"x" * (clock._MAX_OUTPUT + 1) if mode == "overflow" else b"observed stdout"
    )
    stderr = b"observed stderr" if mode == "stderr" else b""

    class Process:
        def __init__(self, *args, **kwargs):
            self.stdout, self.stderr = io.BytesIO(stdout), io.BytesIO(stderr)
            self.killed = False

        def wait(self, timeout):
            if mode == "timeout" and not self.killed:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 9 if self.killed else 0

        def kill(self):
            self.killed = True

        def poll(self):
            return 9 if self.killed else None

    monkeypatch.setattr(clock.subprocess, "Popen", Process)
    monkeypatch.setattr(clock.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    counter = itertools.count(100)

    def measured_stamp():
        current = next(counter)
        if mode == "clock_failure" and current > 100:
            raise PublicReceiptError("native_clock_sample_unbounded")
        return stamp(current)

    monkeypatch.setattr(clock, "native_stamp", measured_stamp)
    result = clock._capture_command(
        ["synthetic"], "w32tm_status.v2", "cp950", time.perf_counter() + 1
    )
    assert base64.b64decode(result["stdout"]["base64"]) == stdout[: clock._MAX_OUTPUT]
    assert base64.b64decode(result["stderr"]["base64"]) == stderr
    assert result["outcome"] == (
        mode if mode in {"timeout", "overflow", "clock_failure"} else "complete"
    )
    if mode != "normal":
        with pytest.raises(PublicReceiptError):
            clock._check_probe(result, "w32tm_status.v2", "cp950")


def test_expired_budget_does_not_start_a_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not launch")

    monkeypatch.setattr(clock.subprocess, "Popen", forbidden)
    monkeypatch.setattr(clock, "native_stamp", stamp)
    result = clock._capture_command(
        ["synthetic"], "w32tm_status.v2", "cp950", time.perf_counter() - 1
    )
    assert result["outcome"] == "timeout"
    assert result["exit_code"] is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("implementation", "GetTickCount64()"),
        ("monotonic", False),
        ("adjustable", True),
        ("resolution", 0.015625),
        ("resolution", 1e-9),
    ],
)
def test_unknown_or_nonmonotonic_windows_counter_is_rejected(monkeypatch, key, value):
    data = {
        "implementation": "QueryPerformanceCounter()",
        "monotonic": True,
        "adjustable": False,
        "resolution": 1e-7,
    }
    data[key] = value
    monkeypatch.setattr(clock.time, "get_clock_info", lambda _: SimpleNamespace(**data))
    with pytest.raises(PublicReceiptError, match="native_clock_domain_unsupported"):
        clock._windows_clock_domain()


@pytest.mark.parametrize(
    "key,value",
    [
        ("wall_implementation", "GetSystemTimeAsFileTime()"),
        ("monotonic_api", "time.monotonic_ns"),
        ("monotonic_resolution_ns", 15625000),
        ("monotonic", False),
    ],
)
def test_resigned_domain_metadata_cannot_mix_old_clock_source(key, value):
    evidence = fixture()
    evidence["diagnostic"]["clock_domain"][key] = value
    with pytest.raises(PublicReceiptError, match="os_clock_diagnostic_invalid"):
        clock.validate_os_clock(resign(evidence))


@pytest.mark.parametrize("elapsed", [-1, 1_000_001])
def test_precise_native_bracket_keeps_reversal_and_one_ms_limit(monkeypatch, elapsed):
    monkeypatch.setattr(
        clock, "_windows_clock_domain", lambda: dict(clock._CLOCK_DOMAIN)
    )
    monkeypatch.setattr(clock, "_windows_wall_reader", lambda: lambda: 1_000_000_000)
    values = iter((100, 100 + elapsed))
    monkeypatch.setattr(clock.time, "perf_counter_ns", lambda: next(values))
    with pytest.raises(PublicReceiptError, match="native_clock_sample_unbounded"):
        clock._windows_stamp()


def test_precise_native_stamp_uses_only_pinned_pair_with_integer_utc(monkeypatch):
    monkeypatch.setattr(
        clock, "_windows_clock_domain", lambda: dict(clock._CLOCK_DOMAIN)
    )
    monkeypatch.setattr(clock, "_windows_wall_reader", lambda: lambda: 1_000_000_100)
    values = iter((100, 300))
    monkeypatch.setattr(clock.time, "perf_counter_ns", lambda: next(values))

    def forbidden():
        raise AssertionError("old coarse clock must not be read")

    monkeypatch.setattr(clock.time, "time_ns", forbidden)
    monkeypatch.setattr(clock.time, "monotonic_ns", forbidden)
    assert clock._windows_stamp() == {"utc_ns": 1_000_000_100, "monotonic_ns": 200}


@pytest.mark.parametrize("delta", [0, 1, 10_000_000, 123_456_789])
def test_documented_filetime_epoch_and_100ns_units_are_exact(delta):
    value = 116_444_736_000_000_000 + delta
    assert clock._filetime_ns(value & 0xFFFFFFFF, value >> 32) == delta * 100


@pytest.mark.parametrize("low,high", [(0, 0), (-1, 1), (True, 1), (0, 2**32)])
def test_filetime_missing_and_invalid_values_are_not_filled(low, high):
    with pytest.raises(PublicReceiptError, match="native_clock_filetime_invalid"):
        clock._filetime_ns(low, high)


def test_unavailable_precise_windows_api_is_a_failure(monkeypatch):
    def missing(*args, **kwargs):
        raise OSError("synthetic unavailable API")

    monkeypatch.setattr(clock.ctypes, "WinDLL", missing, raising=False)
    with pytest.raises(PublicReceiptError, match="native_precise_clock_unavailable"):
        clock._windows_wall_reader()


@pytest.mark.parametrize("mode", ["v2", "mixed", "zone_change"])
def test_capture_clock_version_dispatch_preserves_synthetic_authority_boundary(
    monkeypatch, mode
):
    synthetic = SyntheticCapture(monkeypatch, rows=2)
    counter = itertools.count()

    def observation():
        number = next(counter)
        if mode == "mixed" and number == 0:
            return os_evidence(synthetic.stamp())
        evidence = fixture()
        for key in ("host_before", "status", "host_after"):
            evidence["diagnostic"][key]["request_start"] = synthetic.stamp()
            evidence["diagnostic"][key]["completed"] = synthetic.stamp()
        evidence["sample"] = synthetic.stamp()
        if mode == "zone_change" and number > 0:
            for side in ("host_before", "host_after"):
                host_change(evidence, "timezone_id", "UTC", side=side)
        return resign(evidence)

    monkeypatch.setattr(capture, "native_os_clock", observation)
    if mode == "v2":
        receipt, raw_files = synthetic.collect()
        assert receipt.transport_origin == "synthetic_test"
        capture.replay_public_capture(receipt, raw_files)
    else:
        with pytest.raises(
            PublicReceiptError, match="clock_domain_changed|timezone_changed"
        ):
            synthetic.collect()
