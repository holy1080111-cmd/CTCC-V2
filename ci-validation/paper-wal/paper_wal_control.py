"""Parse the bounded stock PostgreSQL 17.11 C-locale control readback.

The native caller must first pin the executable and the held data directory.
These observations carry no authority to start PostgreSQL or modify a ledger.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

PG_CONTROL_VERSION = 1700
CATALOG_VERSION = 202406281
DIRTY_PRIMARY = "in production"
CLEAN_SHUTDOWN = "shut down"

_CHECKPOINT_COUNTS = (
    "Latest checkpoint's NextOID",
    "Latest checkpoint's NextMultiXactId",
    "Latest checkpoint's NextMultiOffset",
    "Latest checkpoint's oldestXID",
    "Latest checkpoint's oldestXID's DB",
    "Latest checkpoint's oldestActiveXID",
    "Latest checkpoint's oldestMultiXid",
    "Latest checkpoint's oldestMulti's DB",
    "Latest checkpoint's oldestCommitTsXid",
    "Latest checkpoint's newestCommitTsXid",
)
_CONFIG_COUNTS = (
    "max_connections setting",
    "max_worker_processes setting",
    "max_wal_senders setting",
    "max_prepared_xacts setting",
    "max_locks_per_xact setting",
)
_FORMAT_SIZES = (
    "Maximum data alignment",
    "Database block size",
    "Blocks per segment of large relation",
    "WAL block size",
    "Bytes per WAL segment",
    "Maximum length of identifiers",
    "Maximum columns in an index",
    "Maximum size of a TOAST chunk",
    "Size of a large-object chunk",
)
_FIELDS = (
    "pg_control version number",
    "Catalog version number",
    "Database system identifier",
    "Database cluster state",
    "pg_control last modified",
    "Latest checkpoint location",
    "Latest checkpoint's REDO location",
    "Latest checkpoint's REDO WAL file",
    "Latest checkpoint's TimeLineID",
    "Latest checkpoint's PrevTimeLineID",
    "Latest checkpoint's full_page_writes",
    "Latest checkpoint's NextXID",
    *_CHECKPOINT_COUNTS,
    "Time of latest checkpoint",
    "Fake LSN counter for unlogged rels",
    "Minimum recovery ending location",
    "Min recovery ending loc's timeline",
    "Backup start location",
    "Backup end location",
    "End-of-backup record required",
    "wal_level setting",
    "wal_log_hints setting",
    *_CONFIG_COUNTS,
    "track_commit_timestamp setting",
    *_FORMAT_SIZES,
    "Date/time type storage",
    "Float8 argument passing",
    "Data page checksum version",
    "Mock authentication nonce",
)
_BOOLEAN_FIELDS = (
    "Latest checkpoint's full_page_writes",
    "wal_log_hints setting",
    "track_commit_timestamp setting",
)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_UNSIGNED = re.compile(r"(?:0|[1-9][0-9]{0,19})\Z", re.ASCII)
_LSN = re.compile(
    r"(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})\Z", re.ASCII
)
_WAL_FILE = re.compile(r"[0-9A-F]{24}\Z", re.ASCII)
_TIME = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?: [1-9]|[12][0-9]|3[01]) (?:[01][0-9]|2[0-3]):[0-5][0-9]:(?:[0-5][0-9]|60) [0-9]{4}\Z",
    re.ASCII,
)
_MAX_OUTPUT = 32768


class WalControlRefused(ValueError):
    """A fixed denial for incomplete, inconsistent or out-of-scope control data."""


@dataclass(frozen=True, slots=True)
class WalControlState:
    pg_control_version: int
    catalog_version: int
    system_sha256: str
    cluster_state: str
    checkpoint_lsn: str
    redo_lsn: str
    checkpoint_timeline: int
    previous_checkpoint_timeline: int
    execution_authority: bool = field(default=False, init=False)


def _need(condition):
    if not condition:
        raise WalControlRefused("paper_wal_control_refused")


def _uint(value, maximum=(1 << 32) - 1):
    _need(_UNSIGNED.fullmatch(value) is not None)
    number = int(value)
    _need(number <= maximum)
    return number


def _lsn_number(value):
    _need(type(value) is str and _LSN.fullmatch(value) is not None)
    upper, lower = value.split("/")
    return (int(upper, 16) << 32) | int(lower, 16)


def _read_fields(result):
    code = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    _need(type(code) is int and code == 0 and type(stderr) is bytes and not stderr)
    _need(
        type(stdout) is bytes
        and 0 < len(stdout) <= _MAX_OUTPUT
        and stdout.endswith(b"\n")
    )
    try:
        lines = stdout[:-1].decode("ascii").split("\n")
    except UnicodeDecodeError:
        raise WalControlRefused("paper_wal_control_encoding_refused") from None
    _need(len(lines) == len(_FIELDS))
    values = {}
    for label, line in zip(_FIELDS, lines, strict=True):
        key, separator, value = line.partition(":")
        _need(separator == ":" and key == label and 0 < len(line) <= 160)
        value = value.lstrip(" ")
        _need(bool(value) and value == value.rstrip(" ") and value.isprintable())
        values[key] = value
    return values


def _verify_wal_location(values, redo, timeline):
    segment_bytes = _uint(values["Bytes per WAL segment"])
    _need((1 << 20) <= segment_bytes <= (1 << 30))
    _need(segment_bytes & (segment_bytes - 1) == 0)
    log, segment = divmod(redo // segment_bytes, (1 << 32) // segment_bytes)
    file = values["Latest checkpoint's REDO WAL file"]
    _need(_WAL_FILE.fullmatch(file) is not None)
    _need(file == f"{timeline:08X}{log:08X}{segment:08X}")


def _parse(result, *, required_state, expected_system_sha256):
    _need(
        type(expected_system_sha256) is str
        and _SHA.fullmatch(expected_system_sha256) is not None
    )
    values = _read_fields(result)
    version = _uint(values["pg_control version number"])
    catalog = _uint(values["Catalog version number"])
    _need(version == PG_CONTROL_VERSION and catalog == CATALOG_VERSION)
    _need(values["Database cluster state"] == required_state)
    system = values["Database system identifier"]
    _need(_uint(system, (1 << 64) - 1) > 0)
    system_sha256 = hashlib.sha256(system.encode("ascii")).hexdigest()
    _need(system_sha256 == expected_system_sha256)

    checkpoint = values["Latest checkpoint location"]
    redo_lsn = values["Latest checkpoint's REDO location"]
    redo = _lsn_number(redo_lsn)
    _need(0 < redo <= _lsn_number(checkpoint))
    timeline = _uint(values["Latest checkpoint's TimeLineID"])
    previous_timeline = _uint(values["Latest checkpoint's PrevTimeLineID"])
    _need(timeline > 0 and previous_timeline == timeline)
    _verify_wal_location(values, redo, timeline)

    for label in (
        "Minimum recovery ending location",
        "Backup start location",
        "Backup end location",
    ):
        _need(values[label] == "0/0")
    _need(values["Min recovery ending loc's timeline"] == "0")
    _need(values["End-of-backup record required"] == "no")
    _need(values["wal_level setting"] in ("minimal", "replica", "logical"))
    for label in _BOOLEAN_FIELDS:
        _need(values[label] in ("on", "off"))
    for label in (*_CHECKPOINT_COUNTS, *_CONFIG_COUNTS, *_FORMAT_SIZES):
        _uint(values[label])
    xid_parts = values["Latest checkpoint's NextXID"].split(":")
    _need(len(xid_parts) == 2)
    for part in xid_parts:
        _uint(part)
    _lsn_number(values["Fake LSN counter for unlogged rels"])
    for label in ("pg_control last modified", "Time of latest checkpoint"):
        _need(_TIME.fullmatch(values[label]) is not None)
    _need(values["Date/time type storage"] == "64-bit integers")
    _need(values["Float8 argument passing"] in ("by value", "by reference"))
    _need(values["Data page checksum version"] in ("0", "1"))
    _need(_SHA.fullmatch(values["Mock authentication nonce"]) is not None)
    return WalControlState(
        version,
        catalog,
        system_sha256,
        required_state,
        checkpoint,
        redo_lsn,
        timeline,
        previous_timeline,
    )


def admit_dirty_primary(result, *, expected_system_sha256: str) -> WalControlState:
    """Admit the observed dirty primary control state as evidence only."""
    return _parse(
        result,
        required_state=DIRTY_PRIMARY,
        expected_system_sha256=expected_system_sha256,
    )


def verify_clean_shutdown(result, *, prior: WalControlState) -> WalControlState:
    """Verify a clean stop on the same dirty-primary lineage and timeline."""
    _need(type(prior) is WalControlState and prior.cluster_state == DIRTY_PRIMARY)
    _need(
        prior.pg_control_version == PG_CONTROL_VERSION
        and prior.catalog_version == CATALOG_VERSION
    )
    state = _parse(
        result,
        required_state=CLEAN_SHUTDOWN,
        expected_system_sha256=prior.system_sha256,
    )
    _need(
        (state.checkpoint_timeline, state.previous_checkpoint_timeline)
        == (prior.checkpoint_timeline, prior.previous_checkpoint_timeline)
    )
    _need(_lsn_number(state.redo_lsn) >= _lsn_number(prior.redo_lsn))
    _need(_lsn_number(state.checkpoint_lsn) >= _lsn_number(prior.checkpoint_lsn))
    return state
