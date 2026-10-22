"""Owned Stage1 originals, local fsync/readback only; saved bytes mint nothing."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from threading import get_ident
from weakref import WeakKeyDictionary, WeakSet

from app.domain.source_primitives import ClockStamp, canonical, decode, sha

MAX_HEALTH = 512 * 1024
MAX_SAMPLE = 128 * 1024
MAX_PACK = 2 * 1024 * 1024
NAMES = (
    "attempt.json",
    "health-before.raw",
    "sample.json",
    "health-after.raw",
    "outcome.json",
)
PHASES = frozenset(
    {
        "source_consumed",
        "health_before",
        "witness",
        "identity",
        "observation",
        "sample_a",
        "sample_sql",
        "sample_materialize",
        "sample_b",
        "sample_retained",
        "health_after",
        "contract",
        "completed",
    }
)
_ISSUER = object()
_OWNERS = WeakKeyDictionary()
_ATTEMPTED = WeakSet()


class Stage1ForensicError(ValueError):
    pass


def _require(value):
    if not value:
        raise Stage1ForensicError("stage1_forensic_unresolved")


def _context():
    task = asyncio.current_task()
    _require(task is not None)
    return os.getpid(), get_ident(), asyncio.get_running_loop(), task


def _finite():
    from app.mie.validation import development_validation_source_collection_v6

    return development_validation_source_collection_v6


class Stage1RetentionOwnerV6:
    __slots__ = ("__weakref__",)

    def __init__(self, issuer):
        _require(issuer is _ISSUER)

    def __copy__(self):
        raise Stage1ForensicError("stage1_retention_not_transferable")

    def __deepcopy__(self, memo):
        raise Stage1ForensicError("stage1_retention_not_transferable")

    def __reduce_ex__(self, protocol):
        raise Stage1ForensicError("stage1_retention_not_transferable")


@dataclass(slots=True)
class _State:
    claim: object
    context: tuple
    phase: str = "source_consumed"
    source_sha256: str | None = None
    expected: dict = field(default_factory=dict)
    acknowledged: set = field(default_factory=set)
    started: bool = False
    finalization_attempted: bool = False
    closed: bool = False


@dataclass(frozen=True, slots=True)
class Stage1ForensicSnapshotV6:
    payload: bytes
    sha256: str
    outcome_sha256: str
    files: tuple
    status: str


def _state(owner):
    _require(type(owner) is Stage1RetentionOwnerV6)
    state = _OWNERS.get(owner)
    _require(state is not None and state.context == _context())
    # Evidence-only validation deliberately permits original-task cancellation
    # and expired budget. It cannot acquire data, refresh a lease or mint a token.
    _finite()._check_claim_evidence(state.claim)
    return state


def require_live(owner):
    state = _state(owner)
    _require(not state.closed and not state.finalization_attempted)
    _finite()._check_claim(state.claim)
    _finite()._check_service(state.claim.state)
    _require(time.monotonic_ns() <= state.claim.deadline_ns)
    _require(not asyncio.current_task().cancelling())
    return state


def _new_retention_owner(claim):
    _finite()._check_claim(claim)
    _finite()._check_service(claim.state)
    _require(claim not in _ATTEMPTED)
    _ATTEMPTED.add(claim)
    result = Stage1RetentionOwnerV6(_ISSUER)
    _OWNERS[result] = _State(claim, _context())
    return result


def require_pending(owner):
    """Memory-only gate before the real source burn; no caller data is trusted."""
    _require(type(owner) is Stage1RetentionOwnerV6)
    state = _OWNERS.get(owner)
    _require(state is not None and state.context == _context() and not state.started)


def start(owner, service, frozen):
    state = require_live(owner)
    _require(not state.started and service is state.claim.state.service)
    source = frozen.contract
    identity = state.claim.state.reader.identity
    _require(source.v2_capture.plan_sha256 == state.claim.claim.capture_plan_sha256)
    _require(
        source.v2_capture.journal_genesis_sha256 == identity.journal_genesis_sha256
    )
    state.source_sha256 = frozen.sha256
    state.started = True
    _finite()._start_stage1(state.claim)
    _write(
        state,
        "attempt.json",
        canonical(
            {
                "schema_version": "ctcc.mie.stage1_attempt.v6",
                "ordinal_claim_sha256": state.claim.claim.canonical_sha256(),
                "source_v3_sha256": frozen.sha256,
                "journal_id": identity.journal_id,
                "journal_genesis_sha256": identity.journal_genesis_sha256,
                "local_fsync_readback_only": True,
                "db0046_custody": False,
                "replay_grants_capability": False,
            }
        ),
    )


def phase(owner, value):
    state = require_live(owner)
    _require(value in PHASES)
    state.phase = value


def _write(state, name, raw):
    _require(name in NAMES and type(raw) is bytes and 0 < len(raw) <= MAX_PACK)
    _require(name not in state.expected)
    # Pin intent before publish so a late fsync/readback failure never adopts
    # subsequently modified bytes as a new expected payload.
    state.expected[name] = sha(raw)
    _finite()._write_stage1(state.claim, name, raw)
    state.acknowledged.add(name)


def health(owner, which, raw):
    state = _state(owner)
    _require(state.started and which in {"before", "after"})
    state.phase = "health_" + which
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_HEALTH)
    _write(state, "health-" + which + ".raw", raw)
    require_live(owner)


def _typed(value):
    kind = type(value)
    if value is None:
        return {"type": "null"}, True
    if kind is bool:
        return {"type": "bool", "value": value}, True
    if kind is int and value.bit_length() <= 4096:
        return {"type": "int", "value": value}, True
    if kind is str and len(value) <= 4096:
        return {"type": "str", "value": value}, True
    if kind is datetime:
        return {
            "type": "datetime",
            "value": value.isoformat(timespec="microseconds"),
            "fold": value.fold,
        }, True
    return {"type": "unsupported"}, False


def sample(
    owner,
    *,
    before,
    after,
    rows,
    sql_requested,
    execute_returned,
    materialized,
    failed_phase,
):
    state = _state(owner)
    _require(state.started and failed_phase in PHASES)
    state.phase = failed_phase
    _require(
        all(type(v) is bool for v in (sql_requested, execute_returned, materialized))
    )
    encoded, complete = [], materialized
    if rows is not None:
        _require(type(rows) is tuple and len(rows) <= 2)
        for row in rows:
            cells = []
            try:
                items = tuple(row.items())
                _require(len(items) <= 16)
                _require(
                    all(type(key) is str and 0 < len(key) <= 80 for key, _ in items)
                )
                _require(len({key for key, _ in items}) == len(items))
                complete = complete and bool(items)
                for key, value in items:
                    item, supported = _typed(value)
                    complete = complete and supported
                    cells.append({"column": key, "decoded": item})
            except Exception:  # noqa: BLE001 - No driver/opaque object repr is retained.
                cells, complete = [], False
            encoded.append(cells)
    else:
        complete = False
    raw = canonical(
        {
            "schema_version": "ctcc.mie.stage1_decoded_sql_sample.v6",
            "driver_values_not_wire_bytes": True,
            "sample_before": None if before is None else before.model_dump(),
            "sample_after": None if after is None else after.model_dump(),
            "sql_requested": sql_requested,
            "execute_returned": execute_returned,
            "materialization_completed": materialized,
            "materialization_limit": 2,
            "rows_may_be_truncated": rows is not None and len(rows) == 2,
            "decoded_values_complete": complete,
            "row_cardinality": "unavailable"
            if rows is None
            else "one"
            if len(rows) == 1
            else "at_least_two"
            if len(rows) == 2
            else "zero",
            "observed_rows": encoded,
            "last_observed_phase": failed_phase,
        }
    )
    _require(len(raw) <= MAX_SAMPLE)
    _write(state, "sample.json", raw)


def require_native_sample_shape(rows):
    _require(type(rows) is tuple and len(rows) == 1)
    row = dict(rows[0])
    integers = {"backend_pid", "database_oid", "login_oid", "server_version_num"}
    dates = {"sampled_at", "transaction_start", "postmaster_start"}
    strings = {"server_build", "transaction_read_only"}
    _require(set(row) == integers | dates | strings | {"transaction_xid"})
    _require(all(type(row[name]) is int for name in integers))
    _require(all(type(row[name]) is datetime for name in dates))
    _require(all(type(row[name]) is str for name in strings))
    _require(row["transaction_xid"] is None or type(row["transaction_xid"]) is str)


def _finalize(owner, *, status, clock_sha256=None):
    state = _state(owner)
    _require(state.started and not state.closed and not state.finalization_attempted)
    state.finalization_attempted = True
    files = _finite()._read_stage1(state.claim)
    _require(
        all(
            name in state.expected and sha(raw) == state.expected[name]
            for name, raw in files.items()
        )
    )
    if status == "completed":
        _require(set(files) == set(NAMES) - {"outcome.json"})
        _require(set(files) <= state.acknowledged)
        _require(type(clock_sha256) is str and len(clock_sha256) == 64)
    outcome = canonical(
        {
            "schema_version": "ctcc.mie.stage1_outcome.v6",
            "ordinal_claim_sha256": state.claim.claim.canonical_sha256(),
            "source_v3_sha256": state.source_sha256,
            "status": status,
            "last_observed_phase": state.phase,
            "clock_v4_sha256": clock_sha256,
            "files": [
                {"name": name, "sha256": sha(files[name])}
                for name in NAMES
                if name in files
            ],
            "missing_files": [name for name in NAMES[:-1] if name not in files],
            "durability_unconfirmed_files": [
                name
                for name in NAMES[:-1]
                if name in files and name not in state.acknowledged
            ],
            "local_fsync_readback_only": True,
            "db0046_custody": False,
            "replay_grants_capability": False,
        }
    )
    _write(state, "outcome.json", outcome)
    state.closed = True
    return readback(owner)


def completed(owner, clock_sha256):
    require_live(owner)
    _OWNERS[owner].phase = "completed"
    return _finalize(owner, status="completed", clock_sha256=clock_sha256)


def failed(owner, *, cancelled):
    # Narrow original-task evidence-only finalizer: no acquisition, retry,
    # timestamp, callback or permission can be obtained from this route.
    state = _state(owner)
    if not state.started or state.closed or state.finalization_attempted:
        return None
    return _finalize(owner, status="cancelled" if cancelled else "failed")


def _hex(value, length=64):
    _require(type(value) is str and len(value) == length)
    _require(all(c in "0123456789abcdef" for c in value))


def _document(raw, keys, schema, maximum=MAX_SAMPLE):
    value = decode(raw, maximum)
    _require(type(value) is dict and set(value) == set(keys))
    _require(value["schema_version"] == schema and canonical(value) == raw)
    return value


def _typed_replay(value):
    _require(type(value) is dict and type(value.get("type")) is str)
    kind = value["type"]
    if kind in {"null", "unsupported"}:
        _require(set(value) == {"type"})
        return kind != "unsupported"
    if kind in {"bool", "int", "str"}:
        _require(set(value) == {"type", "value"})
        expected = {"bool": bool, "int": int, "str": str}[kind]
        _require(type(value["value"]) is expected)
        if kind == "int":
            _require(value["value"].bit_length() <= 4096)
        if kind == "str":
            _require(len(value["value"]) <= 4096)
        return True
    _require(kind == "datetime" and set(value) == {"type", "value", "fold"})
    _require(type(value["value"]) is str and len(value["value"]) <= 64)
    _require(type(value["fold"]) is int and value["fold"] in {0, 1})
    parsed = datetime.fromisoformat(value["value"])
    _require(parsed.isoformat(timespec="microseconds") == value["value"])
    return True


def _sample_replay(raw):
    value = _document(
        raw,
        {
            "schema_version",
            "driver_values_not_wire_bytes",
            "sample_before",
            "sample_after",
            "sql_requested",
            "execute_returned",
            "materialization_completed",
            "materialization_limit",
            "rows_may_be_truncated",
            "decoded_values_complete",
            "row_cardinality",
            "observed_rows",
            "last_observed_phase",
        },
        "ctcc.mie.stage1_decoded_sql_sample.v6",
    )
    _require(value["driver_values_not_wire_bytes"] is True)
    flags = (
        "sql_requested",
        "execute_returned",
        "materialization_completed",
        "decoded_values_complete",
    )
    _require(all(type(value[k]) is bool for k in flags))
    for key in ("sample_before", "sample_after"):
        if value[key] is not None:
            _require(type(value[key]) is dict)
            ClockStamp.model_validate(value[key])
    phase = value["last_observed_phase"]
    expected = {
        "sample_a": (False, False, False, False, False),
        "sample_sql": (True, False, False, True, False),
        "sample_materialize": (True, True, False, True, False),
        "sample_b": (True, True, True, True, False),
        "sample_retained": (True, True, True, True, True),
    }
    _require(type(phase) is str and phase in expected)
    actual = tuple(value[k] for k in flags[:3]) + (
        value["sample_before"] is not None,
        value["sample_after"] is not None,
    )
    _require(actual == expected[phase])
    rows = value["observed_rows"]
    _require(type(rows) is list and len(rows) <= 2)
    _require(
        type(value["materialization_limit"]) is int
        and value["materialization_limit"] == 2
    )
    _require(
        type(value["rows_may_be_truncated"]) is bool
        and value["rows_may_be_truncated"] is (len(rows) == 2)
    )
    materialized = value["materialization_completed"]
    cardinality = (
        "unavailable"
        if not materialized
        else ("zero", "one", "at_least_two")[len(rows)]
    )
    _require(value["row_cardinality"] == cardinality)
    _require(materialized or not rows)
    complete = materialized
    for row in rows:
        _require(type(row) is list and len(row) <= 16)
        names = set()
        if not row:
            complete = False
        for cell in row:
            _require(type(cell) is dict and set(cell) == {"column", "decoded"})
            name = cell["column"]
            _require(type(name) is str and 0 < len(name) <= 80 and name not in names)
            names.add(name)
            supported = _typed_replay(cell["decoded"])
            complete = complete and supported
    _require(value["decoded_values_complete"] is complete)
    return value


def replay_forensic_files_v6(files, *, expected_claim_sha256):
    _require(type(files) is dict and set(files) <= set(NAMES))
    _hex(expected_claim_sha256)
    _require(
        all(
            type(raw) is bytes
            and 0
            < len(raw)
            <= (
                MAX_HEALTH
                if name in {"health-before.raw", "health-after.raw"}
                else MAX_SAMPLE
            )
            for name, raw in files.items()
        )
    )
    header = None
    if "attempt.json" in files:
        header = _document(
            files["attempt.json"],
            {
                "schema_version",
                "ordinal_claim_sha256",
                "source_v3_sha256",
                "journal_id",
                "journal_genesis_sha256",
                "local_fsync_readback_only",
                "db0046_custody",
                "replay_grants_capability",
            },
            "ctcc.mie.stage1_attempt.v6",
        )
        _require(header["ordinal_claim_sha256"] == expected_claim_sha256)
        for key in ("source_v3_sha256", "journal_genesis_sha256"):
            _hex(header[key])
        _hex(header["journal_id"], 32)
    sample_value = (
        _sample_replay(files["sample.json"]) if "sample.json" in files else None
    )
    if sample_value is not None:
        _require("health-before.raw" in files and header is not None)
    if "health-after.raw" in files:
        _require(sample_value is not None)
    outcome = (
        None
        if "outcome.json" not in files
        else _document(
            files["outcome.json"],
            {
                "schema_version",
                "ordinal_claim_sha256",
                "source_v3_sha256",
                "status",
                "last_observed_phase",
                "clock_v4_sha256",
                "files",
                "missing_files",
                "durability_unconfirmed_files",
                "local_fsync_readback_only",
                "db0046_custody",
                "replay_grants_capability",
            },
            "ctcc.mie.stage1_outcome.v6",
        )
    )
    for document in (header, outcome):
        if document is not None:
            _require(
                all(
                    type(document[k]) is bool and document[k] is v
                    for k, v in (
                        ("local_fsync_readback_only", True),
                        ("db0046_custody", False),
                        ("replay_grants_capability", False),
                    )
                )
            )
    if header is None or outcome is None:
        return None
    _require(
        header["ordinal_claim_sha256"]
        == outcome["ordinal_claim_sha256"]
        == expected_claim_sha256
    )
    _require(header["source_v3_sha256"] == outcome["source_v3_sha256"])
    _require(
        outcome["status"] in {"completed", "failed", "cancelled"}
        and outcome["last_observed_phase"] in PHASES
    )
    _require(type(outcome["files"]) is list and type(outcome["missing_files"]) is list)
    _require(
        outcome["files"]
        == [
            {"name": name, "sha256": sha(files[name])}
            for name in NAMES[:-1]
            if name in files
        ]
    )
    _require(
        outcome["missing_files"] == [name for name in NAMES[:-1] if name not in files]
    )
    uncertain = outcome["durability_unconfirmed_files"]
    _require(type(uncertain) is list and all(type(name) is str for name in uncertain))
    _require(
        uncertain
        == [name for name in NAMES[:-1] if name in uncertain and name in files]
    )
    _require(
        all(
            type(doc[k]) is bool and doc[k] is v
            for doc in (header, outcome)
            for k, v in (
                ("local_fsync_readback_only", True),
                ("db0046_custody", False),
                ("replay_grants_capability", False),
            )
        )
    )
    if outcome["status"] == "completed":
        _require(set(files) == set(NAMES) and outcome["missing_files"] == [])
        _hex(outcome["clock_v4_sha256"])
        _require(uncertain == [])
        _require(outcome["last_observed_phase"] == "completed")
        _require(
            sample_value["decoded_values_complete"] is True
            and sample_value["row_cardinality"] == "one"
        )
        _require(
            sample_value["sample_before"] is not None
            and sample_value["sample_after"] is not None
        )
        cells = {
            cell["column"]: cell["decoded"]["type"]
            for cell in sample_value["observed_rows"][0]
        }
        _require(
            set(cells)
            == {
                "sampled_at",
                "backend_pid",
                "database_oid",
                "login_oid",
                "transaction_start",
                "postmaster_start",
                "transaction_xid",
                "server_version_num",
                "server_build",
                "transaction_read_only",
            }
        )
        _require(
            all(
                cells[key] == "int"
                for key in (
                    "backend_pid",
                    "database_oid",
                    "login_oid",
                    "server_version_num",
                )
            )
        )
        _require(
            all(
                cells[key] == "datetime"
                for key in ("sampled_at", "transaction_start", "postmaster_start")
            )
        )
        _require(
            all(
                cells[key] == "str" for key in ("server_build", "transaction_read_only")
            )
        )
        _require(cells["transaction_xid"] in {"null", "str"})
    else:
        _require(
            outcome["clock_v4_sha256"] is None
            and outcome["last_observed_phase"] != "completed"
        )
    pack = canonical(
        {
            "schema_version": "ctcc.mie.stage1_originals_pack.v6",
            "files": [
                {
                    "name": name,
                    "sha256": sha(files[name]),
                    "raw_base64": base64.b64encode(files[name]).decode("ascii"),
                }
                for name in NAMES
                if name in files
            ],
        }
    )
    _require(len(pack) <= MAX_PACK)
    return Stage1ForensicSnapshotV6(
        pack,
        sha(pack),
        sha(files["outcome.json"]),
        tuple((name, sha(files[name])) for name in NAMES if name in files),
        outcome["status"],
    )


def replay_forensic_pack_v6(raw, *, expected_sha256, expected_claim_sha256):
    _require(
        type(raw) is bytes and 0 < len(raw) <= MAX_PACK and sha(raw) == expected_sha256
    )
    value = decode(raw, MAX_PACK)
    _require(
        canonical(value) == raw
        and set(value) == {"schema_version", "files"}
        and value["schema_version"] == "ctcc.mie.stage1_originals_pack.v6"
    )
    files = {}
    for item in value["files"]:
        _require(
            set(item) == {"name", "sha256", "raw_base64"}
            and item["name"] in NAMES
            and item["name"] not in files
        )
        payload = base64.b64decode(item["raw_base64"], validate=True)
        _require(
            sha(payload) == item["sha256"]
            and base64.b64encode(payload).decode("ascii") == item["raw_base64"]
        )
        files[item["name"]] = payload
    snapshot = replay_forensic_files_v6(
        files, expected_claim_sha256=expected_claim_sha256
    )
    _require(snapshot is not None and snapshot.payload == raw)
    return snapshot


def readback(owner):
    state = _state(owner)
    files = _finite()._read_stage1(state.claim)
    _require(
        all(
            name in state.expected and sha(raw) == state.expected[name]
            for name, raw in files.items()
        )
    )
    result = replay_forensic_files_v6(
        files, expected_claim_sha256=state.claim.claim.canonical_sha256()
    )
    _require(result is not None)
    return result
