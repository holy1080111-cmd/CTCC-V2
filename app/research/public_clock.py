"""Native observation of host clock health; never adjusts the host clock.

This v1 admits Windows W32Time's documented English diagnostic schema only.
Unsupported locales/providers fail explicitly rather than guessing numeric lines.
The independent exchange probes must additionally bracket each acquisition.
"""

from __future__ import annotations

import base64
import ctypes
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.research.public_market_receipts import (
    ClockStamp,
    PublicReceiptError,
    canonical,
    decode,
    sha,
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


def _first_integer(text):
    match = re.fullmatch(r"([0-9]+)(?:\s*\([^\r\n]*\))?", text)
    if match is None:
        raise PublicReceiptError("os_sync_format_unsupported")
    return int(match[1])


def validate_os_clock(evidence: dict):
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


def native_os_clock() -> dict:
    if os.name != "nt":
        raise PublicReceiptError("native_clock_platform_unsupported")
    buffer = ctypes.create_unicode_buffer(32768)
    get_directory = ctypes.WinDLL("kernel32", use_last_error=True).GetSystemDirectoryW
    get_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
    get_directory.restype = ctypes.c_uint
    length = get_directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise PublicReceiptError("native_clock_probe_unavailable")
    executable = Path(buffer.value) / "WindowsPowerShell/v1.0/powershell.exe"
    if not executable.is_absolute() or not executable.is_file():
        raise PublicReceiptError("native_clock_probe_unavailable")
    try:
        result = subprocess.run(
            [
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(_QUERY.encode("utf-16-le")).decode("ascii"),
            ],
            capture_output=True,
            check=False,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode or result.stderr or len(result.stdout) > 65536:
            raise PublicReceiptError("native_clock_probe_failed")
        diagnostic = decode(result.stdout, maximum=65536)
        evidence = {
            "schema_version": "ctcc.windows_clock_observation.v1",
            "sample": native_stamp(),
            "diagnostic": diagnostic,
            "diagnostic_sha256": sha(canonical(diagnostic)),
        }
        return validate_os_clock(evidence)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublicReceiptError("native_clock_probe_failed") from exc
