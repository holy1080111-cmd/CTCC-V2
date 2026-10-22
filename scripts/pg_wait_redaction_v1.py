"""Bounded, pure redaction of an explicitly supplied PostgreSQL wait sample.

The observer computes query digest/byte count/category inside PostgreSQL. This
module never receives SQL text, opens a connection, or grants execution authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

_ROW_KEYS = frozenset(
    {
        "pid",
        "datname",
        "backend_type",
        "state",
        "wait_event_type",
        "wait_event",
        "blocking_pids",
        "query_sha256",
        "query_bytes",
        "command_category",
    }
)
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_DATABASE_KINDS = (
    (re.compile(r"ctcc\Z"), "PARENT"),
    (re.compile(r"ctcc_migration_test_[a-f0-9]{32}\Z"), "MIGRATION_TEST"),
    (re.compile(r"ctcc_clock_[a-f0-9]{18}\Z"), "CLOCK_TEST"),
    (re.compile(r"ctcc_rt_[a-f0-9]{18}\Z"), "RUNTIME_TEST"),
)
BACKEND_TYPES = frozenset({"client backend", "parallel worker", "autovacuum worker"})
STATES = frozenset(
    {
        "active",
        "idle",
        "idle in transaction",
        "idle in transaction (aborted)",
        "fastpath function call",
        "disabled",
    }
)
COMMAND_CATEGORIES = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "ALTER",
        "DROP",
        "LOCK",
        "GRANT",
        "REVOKE",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SET",
        "SHOW",
        "DO",
        "WITH",
        "SAVEPOINT",
        "RELEASE",
        "RESET",
        "DISCARD",
        "START",
        "END",
    }
)
# A finite diagnostic scope, not an assertion that every PostgreSQL event is
# represented. Any unrecognised type/event is recorded as UNKNOWN and fails.
WAIT_EVENTS = {
    "Lock": frozenset(
        {
            "relation",
            "extend",
            "frozenid",
            "page",
            "tuple",
            "transactionid",
            "virtualxid",
            "spectoken",
            "object",
            "userlock",
            "advisory",
            "applytransaction",
        }
    ),
    "Client": frozenset({"ClientRead", "ClientWrite"}),
    "IO": frozenset(
        {
            "DataFileRead",
            "DataFileWrite",
            "DataFileExtend",
            "DataFileImmediateSync",
            "DataFilePrefetch",
            "DataFileSync",
            "DataFileTruncate",
            "CopyFileRead",
            "CopyFileWrite",
            "CopyFileFlush",
            "ControlFileRead",
            "ControlFileWrite",
            "ControlFileSync",
            "WALRead",
            "WALWrite",
            "WALSync",
            "WALInitSync",
            "WALCopyRead",
            "WALCopyWrite",
            "WALBootstrapSync",
            "VersionFileWrite",
            "VersionFileSync",
        }
    ),
    "LWLock": frozenset(
        {
            "BufferContent",
            "BufferMapping",
            "LockManager",
            "ProcArray",
            "WALInsert",
            "WALWrite",
            "XactBuffer",
            "XactSLRU",
            "MultiXactGen",
            "MultiXactMemberBuffer",
            "MultiXactOffsetBuffer",
        }
    ),
    "BufferPin": frozenset({"BufferPin"}),
    "Timeout": frozenset({"PgSleep"}),
    "IPC": frozenset(
        {
            "BgWorkerStartup",
            "BgWorkerShutdown",
            "ProcSignalBarrier",
            "ParallelFinish",
            "ParallelCreateExit",
            "SyncRep",
        }
    ),
}
_MAX_OUTPUT_BYTES = 1_048_576
_MAX_PID = 2**31 - 1
_DURATION_LIMIT_NS = 630_000_000_000
_LABEL = {
    "pid": re.compile(r"pid-[a-f0-9]{64}\Z"),
    "db": re.compile(r"db-[a-f0-9]{64}\Z"),
}
_REASONS = frozenset(
    {
        "DATABASE_KIND_UNKNOWN",
        "BACKEND_TYPE_UNKNOWN",
        "STATE_UNKNOWN",
        "COMMAND_CATEGORY_UNKNOWN",
        "WAIT_TYPE_UNKNOWN",
        "WAIT_EVENT_UNKNOWN",
    }
)
_REPORT_KEYS = frozenset(
    {
        "schema",
        "status",
        "sample_index",
        "elapsed_monotonic_ns",
        "observed_row_count",
        "rows",
        "reasons",
        "raw_sql_or_identity_exported",
        "execution_authority",
    }
)
_REDACTED_ROW_KEYS = frozenset(
    {
        "pid_label",
        "database_kind",
        "database_label",
        "backend_type",
        "state",
        "wait_event_type",
        "wait_event",
        "blocking_pid_labels",
        "query_sha256",
        "query_bytes",
        "command_category",
    }
)


class PgObserverFormatDenied(ValueError):
    """Fixed public denial; never interpolate observer input or credentials."""

    def __init__(self):
        super().__init__("PG_OBSERVER_FORMAT_DENIED")


def _require(condition):
    if not condition:
        raise PgObserverFormatDenied


def _integer(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class PgWaitPolicyV1:
    max_samples: int
    max_rows: int
    max_blockers_per_row: int
    max_query_bytes: int
    max_duration_ns: int

    def __post_init__(self):
        for value, maximum in (
            (self.max_samples, 64),
            (self.max_rows, 128),
            (self.max_blockers_per_row, 64),
            (self.max_query_bytes, 1_048_576),
            (self.max_duration_ns, _DURATION_LIMIT_NS),
        ):
            _integer(value, 1, maximum)


def _allowed(value, choices, reason, reasons):
    if value is None:
        reasons.add(reason)
        return "UNKNOWN"
    _require(type(value) is str and 0 < len(value) <= 128)
    if not value.isascii() or value not in choices:
        reasons.add(reason)
        return "UNKNOWN"
    return value


class PgWaitFormatterV1:
    __slots__ = ("__elapsed", "__failed", "__next", "__policy", "__secret")

    def __init__(self, *, run_secret, policy):
        _require(type(run_secret) is bytes and len(run_secret) == 32)
        _require(type(policy) is PgWaitPolicyV1)
        self.__secret = run_secret
        self.__policy = policy
        self.__next = 0
        self.__elapsed = None
        self.__failed = False

    def __repr__(self):
        return "<PgWaitFormatterV1 redacted>"

    def __reduce_ex__(self, _protocol):
        raise PgObserverFormatDenied

    def _label(self, domain, payload):
        return (
            domain
            + "-"
            + hmac.new(
                self.__secret,
                domain.encode("ascii") + b"\x00" + payload,
                hashlib.sha256,
            ).hexdigest()
        )

    def _pid(self, value):
        return self._label("pid", str(_integer(value, 1, _MAX_PID)).encode("ascii"))

    def _row(self, row, reasons):
        _require(type(row) is dict and all(type(key) is str for key in row))
        _require(set(row) == _ROW_KEYS)
        pid = self._pid(row["pid"])
        name = row["datname"]
        _require(type(name) is str and 0 < len(name) <= 63)
        encoded_name = name.encode("utf-8")
        _require(len(encoded_name) <= 63)
        kind = next(
            (kind for pattern, kind in _DATABASE_KINDS if pattern.fullmatch(name)),
            "UNKNOWN",
        )
        if kind == "UNKNOWN":
            reasons.add("DATABASE_KIND_UNKNOWN")
        blockers = row["blocking_pids"]
        _require(
            type(blockers) is list
            and len(blockers) <= self.__policy.max_blockers_per_row
        )
        # Retain the observer's blocker order and duplicates; do not deduplicate
        # away a real server observation (including parallel-query aliases).
        blocker_labels = [self._pid(value) for value in blockers]
        digest = row["query_sha256"]
        _require(type(digest) is str and _SHA256.fullmatch(digest) is not None)
        size = _integer(row["query_bytes"], 0, self.__policy.max_query_bytes)
        backend = _allowed(
            row["backend_type"], BACKEND_TYPES, "BACKEND_TYPE_UNKNOWN", reasons
        )
        state = _allowed(row["state"], STATES, "STATE_UNKNOWN", reasons)
        category = _allowed(
            row["command_category"],
            COMMAND_CATEGORIES,
            "COMMAND_CATEGORY_UNKNOWN",
            reasons,
        )
        wait_type, event = row["wait_event_type"], row["wait_event"]
        if wait_type is None and event is None:
            wait_type = event = "NOT_WAITING"
        else:
            wait_type = _allowed(wait_type, WAIT_EVENTS, "WAIT_TYPE_UNKNOWN", reasons)
            event = _allowed(
                event,
                WAIT_EVENTS.get(wait_type, frozenset()),
                "WAIT_EVENT_UNKNOWN",
                reasons,
            )
        return {
            "pid_label": pid,
            "database_kind": kind,
            "database_label": self._label("db", encoded_name),
            "backend_type": backend,
            "state": state,
            "wait_event_type": wait_type,
            "wait_event": event,
            "blocking_pid_labels": blocker_labels,
            "query_sha256": digest,
            "query_bytes": size,
            "command_category": category,
        }

    def format_sample(self, rows, *, sample_index, elapsed_monotonic_ns):
        """Return canonical bytes; any failure permanently latches this owner."""
        try:
            _require(not self.__failed)
            _integer(sample_index, 0, self.__policy.max_samples - 1)
            _require(sample_index == self.__next)
            elapsed = _integer(elapsed_monotonic_ns, 0, self.__policy.max_duration_ns)
            _require(self.__elapsed is None or elapsed >= self.__elapsed)
            _require(type(rows) is list and len(rows) <= self.__policy.max_rows)
            self.__next += 1
            self.__elapsed = elapsed
            reasons, rendered, seen = set(), [], set()
            for row in rows:
                value = self._row(row, reasons)
                _require(value["pid_label"] not in seen)
                seen.add(value["pid_label"])
                rendered.append(value)
            report = {
                "schema": "ctcc.pg_wait_sample.redacted.v1",
                "status": "FAIL" if reasons else "PASS",
                "sample_index": sample_index,
                "elapsed_monotonic_ns": elapsed,
                "observed_row_count": len(rows),
                "rows": rendered,
                "reasons": sorted(reasons),
                "raw_sql_or_identity_exported": False,
                "execution_authority": False,
            }
            raw = json.dumps(
                report,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            _require(0 < len(raw) <= _MAX_OUTPUT_BYTES)
            self.__failed = bool(reasons)
            return raw
        except Exception:  # noqa: BLE001 -- Redact all malformed observer values; latch before fixed denial.
            self.__failed = True
            raise PgObserverFormatDenied from None


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(type(key) is str and key not in result)
        result[key] = value
    return result


def _reject_constant(_value):
    raise PgObserverFormatDenied


def _label_valid(value, domain):
    _require(type(value) is str and _LABEL[domain].fullmatch(value) is not None)


def validate_formatted_sample(raw):
    """Validate redacted canonical shape only; no source/HMAC/PG authority."""
    try:
        _require(type(raw) is bytes and 0 < len(raw) <= _MAX_OUTPUT_BYTES)
        report = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
        _require(type(report) is dict and set(report) == _REPORT_KEYS)
        _require(report["schema"] == "ctcc.pg_wait_sample.redacted.v1")
        _require(type(report["status"]) is str and report["status"] in {"PASS", "FAIL"})
        _require(report["raw_sql_or_identity_exported"] is False)
        _require(report["execution_authority"] is False)
        _integer(report["sample_index"], 0, 63)
        _integer(report["elapsed_monotonic_ns"], 0, _DURATION_LIMIT_NS)
        rows = report["rows"]
        _require(type(rows) is list and len(rows) <= 128)
        _require(_integer(report["observed_row_count"], 0, 128) == len(rows))
        reasons, seen = set(), set()
        for row in rows:
            _require(type(row) is dict and set(row) == _REDACTED_ROW_KEYS)
            _label_valid(row["pid_label"], "pid")
            _label_valid(row["database_label"], "db")
            _require(row["pid_label"] not in seen)
            seen.add(row["pid_label"])
            kind = row["database_kind"]
            _require(
                type(kind) is str
                and kind
                in {
                    "PARENT",
                    "MIGRATION_TEST",
                    "CLOCK_TEST",
                    "RUNTIME_TEST",
                    "UNKNOWN",
                }
            )
            if kind == "UNKNOWN":
                reasons.add("DATABASE_KIND_UNKNOWN")
            for field, allowed, reason in (
                ("backend_type", BACKEND_TYPES, "BACKEND_TYPE_UNKNOWN"),
                ("state", STATES, "STATE_UNKNOWN"),
                ("command_category", COMMAND_CATEGORIES, "COMMAND_CATEGORY_UNKNOWN"),
            ):
                value = row[field]
                _require(
                    type(value) is str and (value in allowed or value == "UNKNOWN")
                )
                if value == "UNKNOWN":
                    reasons.add(reason)
            wait_type, event = row["wait_event_type"], row["wait_event"]
            _require(type(wait_type) is str and type(event) is str)
            if wait_type == "NOT_WAITING" or event == "NOT_WAITING":
                _require(wait_type == event == "NOT_WAITING")
            else:
                _require(wait_type in WAIT_EVENTS or wait_type == "UNKNOWN")
                _require(
                    event in WAIT_EVENTS.get(wait_type, frozenset())
                    or event == "UNKNOWN"
                )
                if wait_type == "UNKNOWN":
                    reasons.add("WAIT_TYPE_UNKNOWN")
                if event == "UNKNOWN":
                    reasons.add("WAIT_EVENT_UNKNOWN")
            blockers = row["blocking_pid_labels"]
            _require(type(blockers) is list and len(blockers) <= 64)
            for value in blockers:
                _label_valid(value, "pid")
            _require(
                type(row["query_sha256"]) is str
                and _SHA256.fullmatch(row["query_sha256"]) is not None
            )
            _integer(row["query_bytes"], 0, 1_048_576)
        supplied = report["reasons"]
        _require(
            type(supplied) is list
            and all(type(value) is str and value in _REASONS for value in supplied)
        )
        _require(supplied == sorted(reasons))
        _require(report["status"] == ("FAIL" if reasons else "PASS"))
        canonical = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        _require(canonical == raw)
        return report
    except Exception:  # noqa: BLE001 -- Decoder exceptions must not export malformed observer bytes.
        raise PgObserverFormatDenied from None
