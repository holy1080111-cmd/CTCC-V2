"""Native observation of host clock health; never adjusts the host clock.

Historical v1 English replay is unchanged. Native v2 observations retain bytes
and admit only the explicitly reviewed Windows binary/resource profile.
The independent exchange probes must additionally bracket each acquisition.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock, Thread
from weakref import WeakKeyDictionary

from app.public_market_source.public_market_receipts import (
    ClockStamp,
    PublicReceiptError,
    canonical,
    decode,
    sha,
    validate_stamps,
)

MAX_SYNC_AGE_SECONDS = 900
_QUERY = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$service = Get-Service -Name W32Time
$zone = Get-TimeZone
$w32tm = [System.IO.Path]::Combine([Environment]::SystemDirectory, 'w32tm.exe')
$raw = & $w32tm /query /status /verbose 2>&1
$code = $LASTEXITCODE
[ordered]@{
  service_state = [int]$service.Status
  service_start_type = [int]$service.StartType
  timezone_id = $zone.Id
  utc_offset_minutes = [int][TimeZoneInfo]::Local.GetUtcOffset([DateTime]::UtcNow).TotalMinutes
  status_exit_code = $code
  status_text = ($raw -join "`n")
} | ConvertTo-Json -Compress
"""


def native_stamp() -> dict:
    if os.name == "nt":
        return _windows_stamp()
    before = time.monotonic_ns()
    wall = time.time_ns()
    after = time.monotonic_ns()
    if after < before or after - before > 1_000_000:
        raise PublicReceiptError("native_clock_sample_unbounded")
    # UTC conversion is checked independently; do not use floating timestamp().
    now = datetime.now(UTC)
    local = now.astimezone()
    if local.astimezone(UTC) != now:
        raise PublicReceiptError("utc_conversion_failed")
    return ClockStamp(utc_ns=wall, monotonic_ns=(before + after) // 2).model_dump()


class _FileTime(ctypes.Structure):
    # Documented FILETIME: two DWORDs, little-endian low word then high word.
    _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))


_CLOCK_DOMAIN = {
    "schema_version": "ctcc.windows_precise_clock_domain.v1",
    "wall_implementation": "GetSystemTimePreciseAsFileTime()",
    "wall_unit_ns": 100,
    "wall_precision_bound_ns": 1000,
    "monotonic_api": "time.perf_counter_ns",
    "monotonic_implementation": "QueryPerformanceCounter()",
    "monotonic_resolution_ns": 100,
    "monotonic": True,
    "adjustable": False,
}


def _windows_clock_domain():
    info = time.get_clock_info("perf_counter")
    if (
        info.implementation != "QueryPerformanceCounter()"
        or info.monotonic is not True
        or info.adjustable is not False
        or info.resolution != 1e-7
    ):
        raise PublicReceiptError("native_clock_domain_unsupported")
    return dict(_CLOCK_DOMAIN)


def _filetime_ns(low, high):
    if any(type(v) is not int or not 0 <= v <= 0xFFFFFFFF for v in (low, high)):
        raise PublicReceiptError("native_clock_filetime_invalid")
    wall = (((high << 32) | low) - 116_444_736_000_000_000) * 100
    if not 0 <= wall <= 32_503_680_000_000_000_000:
        raise PublicReceiptError("native_clock_filetime_invalid")
    return wall


def _windows_wall_reader():
    try:
        function = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).GetSystemTimePreciseAsFileTime
        function.argtypes = (ctypes.POINTER(_FileTime),)
        function.restype = None
    except (AttributeError, OSError) as exc:
        raise PublicReceiptError("native_precise_clock_unavailable") from exc

    def read():
        value = _FileTime()
        function(ctypes.byref(value))
        return _filetime_ns(value.low, value.high)

    return read


def _windows_stamp():
    _windows_clock_domain()
    read_wall = _windows_wall_reader()
    before = time.perf_counter_ns()
    wall = read_wall()
    after = time.perf_counter_ns()
    if after < before or after - before > 1_000_000:
        raise PublicReceiptError("native_clock_sample_unbounded")
    now = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=wall // 1000)
    if now.astimezone().astimezone(UTC) != now:
        raise PublicReceiptError("utc_conversion_failed")
    return ClockStamp(utc_ns=wall, monotonic_ns=(before + after) // 2).model_dump()


def _first_integer(text):
    match = re.fullmatch(r"([0-9]+)(?:\s*\([^\r\n]*\))?", text)
    if match is None:
        raise PublicReceiptError("os_sync_format_unsupported")
    return int(match[1])


def _validate_v1(evidence: dict):
    """Pure diagnostic replay; a passed dict does not mint native admission."""
    canonical(evidence)
    if (
        set(evidence) != {"schema_version", "sample", "diagnostic", "diagnostic_sha256"}
        or evidence["schema_version"] != "ctcc.windows_clock_observation.v1"
    ):
        raise PublicReceiptError("os_clock_evidence_invalid")
    ClockStamp.model_validate(evidence["sample"])
    data = evidence["diagnostic"]
    if (
        type(data) is not dict
        or set(data)
        != {
            "service_state",
            "service_start_type",
            "timezone_id",
            "utc_offset_minutes",
            "status_exit_code",
            "status_text",
        }
        or sha(canonical(data)) != evidence["diagnostic_sha256"]
    ):
        raise PublicReceiptError("os_clock_diagnostic_invalid")
    if (
        type(data["service_state"]) is not int
        or data["service_state"] != 4
        or type(data["service_start_type"]) is not int
        or data["service_start_type"] != 2
        or type(data["status_exit_code"]) is not int
        or data["status_exit_code"] != 0
    ):
        raise PublicReceiptError("os_time_service_unsynchronized")
    if (
        type(data["timezone_id"]) is not str
        or not 1 <= len(data["timezone_id"]) <= 128
        or type(data["utc_offset_minutes"]) is not int
        or not -840 <= data["utc_offset_minutes"] <= 840
    ):
        raise PublicReceiptError("os_timezone_unknown")
    status = data["status_text"]
    if type(status) is not str or not 1 <= len(status) <= 16000:
        raise PublicReceiptError("os_sync_format_unsupported")
    fields = {}
    for line in status.splitlines():
        key, delimiter, value = line.partition(":")
        if delimiter:
            if key in fields:
                raise PublicReceiptError("os_sync_format_unsupported")
            fields[key] = value.strip()
    required = {
        "Leap Indicator",
        "Stratum",
        "Source",
        "Last Sync Error",
        "Time since Last Good Sync Time",
    }
    if not required <= set(fields):
        raise PublicReceiptError("os_sync_locale_unsupported")
    if (
        _first_integer(fields["Leap Indicator"]) != 0
        or not 1 <= _first_integer(fields["Stratum"]) <= 15
        or _first_integer(fields["Last Sync Error"]) != 0
    ):
        raise PublicReceiptError("os_time_service_unsynchronized")
    source = fields["Source"]
    if re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9.:-]{2,252}(?:,0x[0-9a-fA-F]+)?", source
    ) is None or source.lower() in {
        "free-running system clock",
        "local cmos clock",
        "loCl".lower(),
    }:
        raise PublicReceiptError("os_sync_source_unknown")
    age = fields["Time since Last Good Sync Time"]
    if (
        re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,9})?s", age) is None
        or Decimal(age[:-1]) > MAX_SYNC_AGE_SECONDS
    ):
        raise PublicReceiptError("os_sync_expired")
    return evidence


PROFILE_ID = "ctcc.w32tm.26100_9278.en_US_zh_TW.cp950.v1"
RESOURCE_IDS = [2501, 2502, 2508, 2535, 2536]
_ENGLISH = (
    "Leap Indicator",
    "Stratum",
    "Source",
    "Last Sync Error",
    "Time since Last Good Sync Time",
)
_CHINESE = (
    "躍進式指示器",
    "組織層",
    "來源",
    "上次同步處理錯誤",
    "自上次良好同步處理時間後的時間",
)
_FILES = [
    {
        "slot": "w32tm.exe",
        "sha256": "ce8721f50788d4041b278fe05d47c02c1f6718dd5390f316e4d8eccb20eaebc1",
        "version": "10.0.26100.9278",
        "signature_status": 0,
    },
    {
        "slot": "en-US/w32tm.exe.mui",
        "sha256": "b9bf75e27b71fafa5aca897040282535fa789d8cecdf70a384f71d1b4c47e3ca",
        "version": "10.0.26100.1",
        "signature_status": 0,
    },
    {
        "slot": "zh-TW/w32tm.exe.mui",
        "sha256": "f246a33da5f287a6ffa905adbeae62c22782e8e1072db9c50519f603cd177d57",
        "version": "10.0.26100.1",
        "signature_status": 0,
    },
]
_HOST_QUERY_V2 = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
foreach ($module in @('Microsoft.PowerShell.Management', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Security')) {
  Import-Module ([IO.Path]::Combine($PSHOME, "Modules/$module/$module.psd1"))
}
$PSModuleAutoLoadingPreference = 'None'
$service = Get-Service -Name W32Time
$zone = Get-TimeZone
$files = foreach ($slot in @('w32tm.exe', 'en-US/w32tm.exe.mui', 'zh-TW/w32tm.exe.mui')) {
  $path = [IO.Path]::Combine([Environment]::SystemDirectory, $slot)
  $file = Get-Item -LiteralPath $path
  $signature = Get-AuthenticodeSignature -LiteralPath $path
  [ordered]@{
    slot = $slot
    sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    version = ('{0}.{1}.{2}.{3}' -f $file.VersionInfo.FileMajorPart, $file.VersionInfo.FileMinorPart, $file.VersionInfo.FileBuildPart, $file.VersionInfo.FilePrivatePart)
    signature_status = [int]$signature.Status
  }
}
[ordered]@{
  service_state = [int]$service.Status
  service_start_type = [int]$service.StartType
  timezone_id = $zone.Id
  utc_offset_minutes = [int][TimeZoneInfo]::Local.GetUtcOffset([DateTime]::UtcNow).TotalMinutes
  files = @($files)
} | ConvertTo-Json -Depth 5 -Compress
"""
_MAX_OUTPUT = 32768
_MAX_OBSERVATION_NS = 5_000_000_000


class NativeClockObservationError(PublicReceiptError):
    """Rejected diagnostics retained explicitly; never printed as exception text.

    observation_bytes is portable diagnostic data, not a healthy clock receipt
    or native acquisition authority. Retention here is in memory, not a journal.
    """

    def __init__(self, code: str, observation: dict):
        super().__init__(code)
        self.observation_bytes = canonical(observation)


_CLOCK_ISSUER = object()
_CLOCK_RESULTS = WeakKeyDictionary()
_CLOCK_RESULTS_LOCK = Lock()
MAX_CLOCK_PAYLOAD = 512 * 1024
CLOCK_RESULT_VERSION = "ctcc.public.owned_clock_observation.v1"


class _OwnedClockObservation:
    # Payload and scope live outside the carrier. Ordinary object copying or
    # deserialization cannot mint an identity registered by the native adapter.
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        if issuer is not _CLOCK_ISSUER:
            raise PublicReceiptError("owned_clock_observation_required")

    def __copy__(self):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __deepcopy__(self, memo):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __reduce_ex__(self, protocol):
        raise PublicReceiptError("owned_clock_observation_not_transferable")

    def __reduce__(self):
        raise PublicReceiptError("owned_clock_observation_not_transferable")


def replay_clock_observation(payload):
    """Negative observation integrity, never admission of an unhealthy clock."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_CLOCK_PAYLOAD:
        raise PublicReceiptError("clock_observation_invalid")
    value = decode(payload, MAX_CLOCK_PAYLOAD)
    if (
        canonical(value) != payload
        or type(value) is not dict
        or set(value) != {"schema_version", "outcome", "observation"}
        or value["schema_version"] != CLOCK_RESULT_VERSION
        or value["outcome"] not in {"accepted", "rejected", "unobserved"}
    ):
        raise PublicReceiptError("clock_observation_invalid")
    observation = value["observation"]
    if value["outcome"] == "unobserved":
        if observation is not None:
            raise PublicReceiptError("clock_observation_invalid")
        return value
    if value["outcome"] == "accepted":
        validate_os_clock(observation)
        return value
    if type(observation) is not dict or set(observation) != {"diagnostic"}:
        raise PublicReceiptError("clock_observation_invalid")
    diagnostic = observation["diagnostic"]
    if (
        type(diagnostic) is not dict
        or set(diagnostic)
        != {
            "schema_version",
            "profile_id",
            "resource_ids",
            "clock_domain",
            "host_before",
            "status",
            "host_after",
        }
        or diagnostic["schema_version"] != "ctcc.windows_clock_diagnostic.v2"
        or diagnostic["profile_id"] != PROFILE_ID
        or canonical(diagnostic["resource_ids"]) != canonical(RESOURCE_IDS)
        or canonical(diagnostic["clock_domain"]) != canonical(_CLOCK_DOMAIN)
    ):
        raise PublicReceiptError("clock_observation_invalid")
    missing = False
    for name, command, encoding in (
        ("host_before", "host_metadata.v2", "utf-8"),
        ("status", "w32tm_status.v2", "cp950"),
        ("host_after", "host_metadata.v2", "utf-8"),
    ):
        probe = diagnostic[name]
        if probe is None:
            missing = True
            continue
        if (
            missing
            or type(probe) is not dict
            or set(probe)
            != {
                "schema_version",
                "command_id",
                "encoding",
                "request_start",
                "completed",
                "outcome",
                "exit_code",
                "stdout",
                "stderr",
            }
            or probe["schema_version"] != "ctcc.windows_clock_raw_probe.v1"
            or probe["command_id"] != command
            or probe["encoding"] != encoding
            or probe["outcome"]
            not in {
                "complete",
                "failed",
                "timeout",
                "incomplete",
                "overflow",
                "clock_failure",
            }
            or (probe["exit_code"] is not None and type(probe["exit_code"]) is not int)
        ):
            raise PublicReceiptError("clock_observation_invalid")
        # Do not impose causal/health admission on retained negative samples.
        ClockStamp.model_validate(probe["request_start"])
        if probe["completed"] is not None:
            ClockStamp.model_validate(probe["completed"])
        for stream in (probe["stdout"], probe["stderr"]):
            if type(stream) is not dict or type(stream.get("truncated")) is not bool:
                raise PublicReceiptError("clock_observation_invalid")
            _stream_bytes({**stream, "truncated": False})
    return value


def _owned_clock_payload(value, attempt, stage):
    if type(value) is not _OwnedClockObservation:
        raise PublicReceiptError("owned_clock_observation_required")
    with _CLOCK_RESULTS_LOCK:
        entry = _CLOCK_RESULTS.pop(value, None)
    if entry is None or entry[0] is not attempt or entry[1] != stage:
        raise PublicReceiptError("owned_clock_observation_required")
    payload = entry[2]
    replay_clock_observation(payload)
    return payload


def _observe_owned_clock(attempt, stage):
    """No supplied exception, diagnostic, clock callback or path is accepted."""
    from app.public_market_source.public_attempt_journal import _OwnedAttempt

    if type(attempt) is not _OwnedAttempt or type(stage) is not str:
        raise PublicReceiptError("owned_clock_observation_required")
    with _CLOCK_RESULTS_LOCK:
        attempt.claim_clock_stage(stage)
    try:
        observation = native_os_clock()
        outcome = "accepted"
    except NativeClockObservationError as exc:
        if type(exc) is not NativeClockObservationError:
            raise PublicReceiptError("owned_clock_observation_unavailable") from None
        observation = decode(exc.observation_bytes, MAX_CLOCK_PAYLOAD)
        outcome = "rejected"
    except PublicReceiptError:
        # Setup failed before there was a returned raw observation.
        observation, outcome = None, "unobserved"
    payload = canonical(
        {
            "schema_version": CLOCK_RESULT_VERSION,
            "outcome": outcome,
            "observation": observation,
        }
    )
    replay_clock_observation(payload)
    result = _OwnedClockObservation(_CLOCK_ISSUER)
    with _CLOCK_RESULTS_LOCK:
        _CLOCK_RESULTS[result] = (attempt, stage, payload)
    return result


def _stream_record(raw: bytes, *, truncated=False):
    return {
        "base64": base64.b64encode(raw).decode("ascii"),
        "sha256": sha(raw),
        "length": len(raw),
        "truncated": truncated,
    }


def _stream_bytes(value):
    if (
        type(value) is not dict
        or set(value) != {"base64", "sha256", "length", "truncated"}
        or type(value["base64"]) is not str
        or len(value["base64"]) > 4 * ((_MAX_OUTPUT + 2) // 3)
        or type(value["length"]) is not int
        or not 0 <= value["length"] <= _MAX_OUTPUT
        or value["truncated"] is not False
    ):
        raise PublicReceiptError("native_clock_output_invalid")
    try:
        raw = base64.b64decode(value["base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PublicReceiptError("native_clock_output_invalid") from exc
    if (
        base64.b64encode(raw).decode("ascii") != value["base64"]
        or len(raw) != value["length"]
        or sha(raw) != value["sha256"]
    ):
        raise PublicReceiptError("native_clock_output_invalid")
    return raw


def _check_probe(record, command_id, encoding):
    if (
        type(record) is not dict
        or set(record)
        != {
            "schema_version",
            "command_id",
            "encoding",
            "request_start",
            "completed",
            "outcome",
            "exit_code",
            "stdout",
            "stderr",
        }
        or record["schema_version"] != "ctcc.windows_clock_raw_probe.v1"
        or record["command_id"] != command_id
        or record["encoding"] != encoding
        or record["outcome"] != "complete"
        or type(record["exit_code"]) is not int
        or record["exit_code"] != 0
    ):
        raise PublicReceiptError("native_clock_probe_failed")
    validate_stamps((record["request_start"], record["completed"]))
    raw = _stream_bytes(record["stdout"])
    if _stream_bytes(record["stderr"]):
        raise PublicReceiptError("native_clock_probe_failed")
    return raw


def _host_record(record):
    data = decode(_check_probe(record, "host_metadata.v2", "utf-8"), _MAX_OUTPUT)
    if (
        type(data) is not dict
        or set(data)
        != {
            "service_state",
            "service_start_type",
            "timezone_id",
            "utc_offset_minutes",
            "files",
        }
        or canonical(data["files"]) != canonical(_FILES)
    ):
        raise PublicReceiptError("os_clock_profile_unsupported")
    if (
        type(data["service_state"]) is not int
        or data["service_state"] != 4
        or type(data["service_start_type"]) is not int
        or data["service_start_type"] != 2
    ):
        raise PublicReceiptError("os_time_service_unsynchronized")
    if (
        type(data["timezone_id"]) is not str
        or not 1 <= len(data["timezone_id"]) <= 128
        or type(data["utc_offset_minutes"]) is not int
        or not -840 <= data["utc_offset_minutes"] <= 840
    ):
        raise PublicReceiptError("os_timezone_unknown")
    return data


def _localized_status(raw):
    try:
        status = raw.decode("cp950", errors="strict")
    except UnicodeError as exc:
        raise PublicReceiptError("os_sync_encoding_unsupported") from exc
    if not 1 <= len(status) <= 16000 or "\ufffd" in status or "\x00" in status:
        raise PublicReceiptError("os_sync_format_unsupported")
    fields = {}
    for line in status.splitlines():
        key, delimiter, value = line.partition(":")
        if delimiter:
            if key in fields:
                raise PublicReceiptError("os_sync_format_unsupported")
            fields[key] = value.strip()
    en, zh = set(_ENGLISH) & fields.keys(), set(_CHINESE) & fields.keys()
    if en and zh:
        raise PublicReceiptError("os_sync_locale_unsupported")
    labels = _ENGLISH if en == set(_ENGLISH) else _CHINESE
    if not set(labels) <= fields.keys():
        raise PublicReceiptError("os_sync_locale_unsupported")
    selected = dict(zip(_ENGLISH, (fields[key] for key in labels), strict=True))
    age = selected[_ENGLISH[-1]]
    if re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.[0-9]{7}s", age) is None:
        raise PublicReceiptError("os_sync_format_unsupported")
    return selected


def _validate_v2(evidence):
    if set(evidence) != {"schema_version", "sample", "diagnostic", "diagnostic_sha256"}:
        raise PublicReceiptError("os_clock_evidence_invalid")
    data = evidence["diagnostic"]
    if (
        type(data) is not dict
        or set(data)
        != {
            "schema_version",
            "profile_id",
            "resource_ids",
            "clock_domain",
            "host_before",
            "status",
            "host_after",
        }
        or data["schema_version"] != "ctcc.windows_clock_diagnostic.v2"
        or data["profile_id"] != PROFILE_ID
        or canonical(data["resource_ids"]) != canonical(RESOURCE_IDS)
        or canonical(data["clock_domain"]) != canonical(_CLOCK_DOMAIN)
        or sha(canonical(data)) != evidence["diagnostic_sha256"]
    ):
        raise PublicReceiptError("os_clock_diagnostic_invalid")
    before, after = _host_record(data["host_before"]), _host_record(data["host_after"])
    if canonical(before) != canonical(after):
        raise PublicReceiptError("os_clock_host_changed")
    fields = _localized_status(_check_probe(data["status"], "w32tm_status.v2", "cp950"))
    stamps = [
        data["host_before"]["request_start"],
        data["host_before"]["completed"],
        data["status"]["request_start"],
        data["status"]["completed"],
        data["host_after"]["request_start"],
        data["host_after"]["completed"],
        evidence["sample"],
    ]
    validate_stamps(stamps)
    elapsed = stamps[-1]["monotonic_ns"] - stamps[0]["monotonic_ns"]
    if elapsed > _MAX_OBSERVATION_NS:
        raise PublicReceiptError("native_clock_probe_expired")
    # Conservatively age from request start, not the unknown response instant.
    lag = stamps[-1]["monotonic_ns"] - data["status"]["request_start"]["monotonic_ns"]
    if Decimal(fields[_ENGLISH[-1]][:-1]) + Decimal(lag) / 10**9 > MAX_SYNC_AGE_SECONDS:
        raise PublicReceiptError("os_sync_expired")
    diagnostic = {key: value for key, value in after.items() if key != "files"}
    diagnostic.update(
        status_exit_code=0,
        status_text="\n".join(f"{k}: {v}" for k, v in fields.items()),
    )
    _validate_v1(
        {
            "schema_version": "ctcc.windows_clock_observation.v1",
            "sample": evidence["sample"],
            "diagnostic": diagnostic,
            "diagnostic_sha256": sha(canonical(diagnostic)),
        }
    )
    return evidence


def validate_os_clock(evidence: dict):
    """Pure replay only: neither version nor caller data grants authority."""
    canonical(evidence)
    if type(evidence) is not dict:
        raise PublicReceiptError("os_clock_evidence_invalid")
    if evidence.get("schema_version") == "ctcc.windows_clock_observation.v1":
        return _validate_v1(evidence)
    if evidence.get("schema_version") == "ctcc.windows_clock_observation.v2":
        return _validate_v2(evidence)
    raise PublicReceiptError("os_clock_evidence_invalid")


def clock_timezone(evidence):
    validate_os_clock(evidence)
    data = evidence["diagnostic"]
    if evidence["schema_version"] == "ctcc.windows_clock_observation.v2":
        data = _host_record(data["host_after"])
    return data["timezone_id"], data["utc_offset_minutes"]


def _capture_command(command, command_id, encoding, deadline):
    started = native_stamp()
    chunks = [bytearray(), bytearray()]
    truncated, failed = [False, False], [False, False]
    outcome, code = "failed", None
    process = None

    def read_pipe(pipe, index):
        try:
            while block := pipe.read(4096):
                remaining = _MAX_OUTPUT - len(chunks[index])
                chunks[index].extend(block[:remaining])
                if len(block) > remaining:
                    truncated[index] = True
                    process.kill()
                    break
        except OSError:
            failed[index] = True
        finally:
            try:
                pipe.close()
            except OSError:
                failed[index] = True

    try:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            outcome = "timeout"
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            readers = [
                Thread(target=read_pipe, args=(pipe, i), daemon=True)
                for i, pipe in enumerate((process.stdout, process.stderr))
            ]
            for reader in readers:
                reader.start()
            try:
                code = process.wait(timeout=max(0.001, deadline - time.perf_counter()))
                outcome = "complete"
            except subprocess.TimeoutExpired:
                outcome = "timeout"
                process.kill()
                code = process.wait(timeout=1)
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers) or any(failed):
                outcome = "incomplete"
            if any(truncated):
                outcome = "overflow"
    except (OSError, subprocess.SubprocessError):
        outcome = "failed"
        if process is not None and process.poll() is None:
            process.kill()
    try:
        completed = native_stamp()
    except PublicReceiptError:
        # Retain measured bytes even when their ending clock cannot be proven.
        # None is an explicit incomplete observation, never a replacement time.
        completed, outcome = None, "clock_failure"
    return {
        "schema_version": "ctcc.windows_clock_raw_probe.v1",
        "command_id": command_id,
        "encoding": encoding,
        "request_start": started,
        "completed": completed,
        "outcome": outcome,
        "exit_code": code,
        "stdout": _stream_record(bytes(chunks[0]), truncated=truncated[0]),
        "stderr": _stream_record(bytes(chunks[1]), truncated=truncated[1]),
    }


def _system_directory():
    if os.name != "nt":
        raise PublicReceiptError("native_clock_platform_unsupported")
    buffer = ctypes.create_unicode_buffer(32768)
    get_directory = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW
    get_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    get_directory.restype = ctypes.c_uint
    length = get_directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise PublicReceiptError("native_clock_probe_unavailable")
    return Path(buffer.value)


def native_os_clock() -> dict:
    directory = _system_directory()
    executable = directory / "WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_absolute() or not executable.is_file():
        raise PublicReceiptError("native_clock_probe_unavailable")
    metadata_command = [
        str(executable),
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        base64.b64encode(_HOST_QUERY_V2.encode("utf-16-le")).decode("ascii"),
    ]
    domain = _windows_clock_domain()
    deadline = time.perf_counter() + _MAX_OBSERVATION_NS / 10**9
    data = {
        "schema_version": "ctcc.windows_clock_diagnostic.v2",
        "profile_id": PROFILE_ID,
        "resource_ids": list(RESOURCE_IDS),
        "clock_domain": domain,
        "host_before": _capture_command(
            metadata_command, "host_metadata.v2", "utf-8", deadline
        ),
        "status": None,
        "host_after": None,
    }
    try:
        # Profile check precedes execution of the native utility. Unhealthy host
        # state still rejects, but its read-only status bytes remain observable.
        host = decode(
            _check_probe(data["host_before"], "host_metadata.v2", "utf-8"), _MAX_OUTPUT
        )
        if type(host) is not dict or canonical(host.get("files")) != canonical(_FILES):
            raise PublicReceiptError("os_clock_profile_unsupported")
        data["status"] = _capture_command(
            [str(directory / "w32tm.exe"), "/query", "/status", "/verbose"],
            "w32tm_status.v2",
            "cp950",
            deadline,
        )
        data["host_after"] = _capture_command(
            metadata_command, "host_metadata.v2", "utf-8", deadline
        )
        evidence = {
            "schema_version": "ctcc.windows_clock_observation.v2",
            "sample": native_stamp(),
            "diagnostic": data,
            "diagnostic_sha256": sha(canonical(data)),
        }
        return validate_os_clock(evidence)
    except PublicReceiptError as exc:
        raise NativeClockObservationError(str(exc), {"diagnostic": data}) from exc
