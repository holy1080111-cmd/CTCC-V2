"""Structural native-member/partition records; portable bytes grant no authority.

The native producer must replay original B1/B3/custody and retain its ownership.
This stdlib module does not authenticate a DTO, perform I/O or issue that owner.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath

PARTITION_SPAN = 32
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_CHAIN_BYTES = 64 * 1024 * 1024
MAX_JSON_NODES = 262144
MAX_JSON_DEPTH = 32
MAX_SEQUENCE = 2**63 - 1
MEMBER_SCHEMA = "ctcc.native_history_member_registration.v1"
LINKAGE_SCHEMA = "ctcc.native_history_member_linkage.v1"
CHECKPOINT_SCHEMA = "ctcc.native_history_partition_checkpoint.v1"


class HistoryPartitionError(ValueError):
    """Fixed codes only; do not disclose account or original source contents."""


def _deny(code):
    raise HistoryPartitionError(code) from None


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


POLICY_BYTES = canonical(
    {
        "schema_version": CHECKPOINT_SCHEMA,
        "member_schema": MEMBER_SCHEMA,
        "partition_span": PARTITION_SPAN,
        "global_sequence": "existing_B3_positive_bigint_never_reset",
        "checkpoint": "exact32_members_full_genesis_and_predecessor_chain",
        "missing_native_registration": "explicit_gap_never_backfilled_by_new_proof",
        "source": "original_B1_B3_DB0046_and_native_files_required_by_held_producer",
        "raw_source_replay": "checkpoint_does_not_replace_original_required_bytes",
        "maximum_record_bytes": MAX_RECORD_BYTES,
        "maximum_cumulative_checkpoint_bytes": MAX_CHAIN_BYTES,
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = digest(POLICY_BYTES)
_REFERENCE_KEYS = {
    "capture_id",
    "head_sha256",
    "plan_sha256",
    "packet_sha256",
    "session_binding_sha256",
}
_COUNTS = (
    "source_facts_through_sequence",
    "coverage_through_sequence",
    "unresolved_findings_through_sequence",
)


def _unknowns():
    return {
        "source_authenticity_verified": False,
        "account_complete": False,
        "execution_authority": False,
        "account_revision": None,
        "clear_stop_permission": False,
        "arm_permission": False,
        "funding_accrual": None,
        "account_history_finality": None,
        "current_history_tail": None,
        "loss_streak_seed": None,
        "historical_native_hwm": None,
    }


def _hash(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _deny("partition_hash_invalid")
    return value


def _number(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_SEQUENCE:
        _deny("partition_integer_invalid")
    return value


def _json(raw, limit=MAX_RECORD_BYTES):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        _deny("partition_json_byte_bound")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                _deny("partition_json_duplicate_key")
            result[key] = value
        return result

    def forbidden(_):
        _deny("partition_json_number_invalid")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_float=forbidden,
            parse_constant=forbidden,
        )
        stack, nodes = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
                _deny("partition_json_structure_bound")
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
            elif type(item) not in {str, int, bool, type(None)}:
                _deny("partition_json_type_invalid")
        if canonical(value) != raw:
            _deny("partition_json_noncanonical")
        return value
    except HistoryPartitionError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _deny("partition_json_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class HistoryPartitionScopeV1:
    environment: str
    account_id: str
    settlement_currency: str

    def __post_init__(self):
        if (
            type(self.environment) is not str
            or type(self.settlement_currency) is not str
            or self.environment != "demo"
            or type(self.account_id) is not str
            or re.fullmatch(r"[0-9]{1,32}", self.account_id) is None
            or self.settlement_currency != "USDT"
        ):
            _deny("partition_scope_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class HistoryPartitionRecordV1:
    """Inert canonical storage proposal; never a native capability or receipt."""

    record_json: bytes

    @property
    def record_sha256(self):
        return digest(self.record_json)


def _scope(value):
    if type(value) is not HistoryPartitionScopeV1:
        _deny("partition_exact_scope_required")
    value.__post_init__()
    return [value.environment, value.account_id, value.settlement_currency]


def _reference(value):
    if type(value) is not dict or set(value) != _REFERENCE_KEYS:
        _deny("partition_reference_invalid")
    if (
        type(value["capture_id"]) is not str
        or re.fullmatch(r"[a-f0-9]{32}", value["capture_id"]) is None
    ):
        _deny("partition_capture_invalid")
    for name in _REFERENCE_KEYS - {"capture_id"}:
        _hash(value[name])
    return value


def _ascii(value):
    if type(value) is not str:
        _deny("partition_exact_ascii_string_required")
    try:
        return value.encode("ascii")
    except UnicodeError:
        _deny("partition_exact_ascii_string_required")


def _b3(raw, *, scope, source_policy):
    value = _json(raw)
    keys = {
        "schema_version",
        "scope_sha256",
        "sequence",
        "previous_sha256",
        "reference",
        "window",
        "policy_sha256",
        "receipt_sha256",
        "anchor_json",
        "anchor_sha256",
        "account_complete",
        "execution_authority",
        "admission",
    }
    keys |= {
        name + suffix
        for name in ("source", "coverage", "finding")
        for suffix in ("_membership_sha256", "_membership_count")
    }
    if type(value) is not dict or set(value) != keys:
        _deny("partition_original_b3_shape_invalid")
    if (
        value["schema_version"] != "ctcc.account_observation_batch.v1"
        or value["scope_sha256"] != digest(canonical(scope))
        or value["policy_sha256"] != source_policy
        or value["account_complete"] is not False
        or value["execution_authority"] is not False
        or value["admission"] != "DENY"
    ):
        _deny("partition_original_b3_identity_invalid")
    number = _number(value["sequence"])
    if number == 1:
        if value["previous_sha256"] is not None:
            _deny("partition_genesis_previous_invalid")
    else:
        _hash(value["previous_sha256"])
    _reference(value["reference"])
    _hash(value["receipt_sha256"])
    for name in ("source", "coverage", "finding"):
        _hash(value[name + "_membership_sha256"])
        _number(value[name + "_membership_count"], zero=True)
    if type(value["anchor_json"]) is not str:
        _deny("partition_original_anchor_invalid")
    anchor_raw = value["anchor_json"].encode("utf-8")
    anchor = _json(anchor_raw)
    if digest(anchor_raw) != _hash(value["anchor_sha256"]):
        _deny("partition_original_anchor_hash_changed")
    expected_keys = {
        "schema_version",
        "policy_sha256",
        "scope_sha256",
        "through_sequence",
        "previous_sequence",
        "previous_event_sha256",
        "previous_anchor_sha256",
        *_COUNTS,
        "current_source_membership_sha256",
        "current_coverage_membership_sha256",
        "verified_witnesses",
        "required_proof_budget_exceeded",
    }
    if (
        type(anchor) is not dict
        or set(anchor) != expected_keys
        or anchor["schema_version"] != "ctcc.account_observation_continuation.v1"
        or anchor["policy_sha256"] != source_policy
        or anchor["scope_sha256"] != value["scope_sha256"]
        or _number(anchor["through_sequence"]) != number
        or _number(anchor["previous_sequence"], zero=True) != number - 1
        or anchor["previous_event_sha256"] != value["previous_sha256"]
        or anchor["current_source_membership_sha256"]
        != value["source_membership_sha256"]
        or anchor["current_coverage_membership_sha256"]
        != value["coverage_membership_sha256"]
        or type(anchor["verified_witnesses"]) is not list
        or type(anchor["required_proof_budget_exceeded"]) is not bool
    ):
        _deny("partition_original_anchor_invalid")
    if number == 1:
        if anchor["previous_anchor_sha256"] is not None:
            _deny("partition_genesis_previous_invalid")
    else:
        _hash(anchor["previous_anchor_sha256"])
    for name in _COUNTS:
        _number(anchor[name], zero=True)
    window = value["window"]
    if type(window) is not dict or set(window) != {"started_at", "ended_at"}:
        _deny("partition_original_window_invalid")
    try:
        times = [
            datetime.fromisoformat(window[name]) for name in ("started_at", "ended_at")
        ]
        if any(
            time.tzinfo != UTC
            or time.isoformat() != window[name]
            or time.microsecond % 1000
            for time, name in zip(times, ("started_at", "ended_at"), strict=True)
        ) or not timedelta(0) <= times[1] - times[0] <= timedelta(days=7):
            _deny("partition_original_window_invalid")
    except (TypeError, ValueError):
        _deny("partition_original_window_invalid")
    return value, anchor


def _inventory(value, *, max_total_bytes):
    if type(value) is not dict or not 1 <= len(value) <= 8192:
        _deny("partition_original_inventory_missing")
    seen = set()
    total = 0
    for name, pin in value.items():
        if (
            type(name) is not str
            or not name
            or not PurePosixPath(name).parts
            or PurePosixPath(name).as_posix() != name
            or name.startswith("/")
            or any(char in name for char in ("\\", ":", "\0"))
            or any(
                part in {".", "..", ".git"} or part.casefold().startswith(".env")
                for part in PurePosixPath(name).parts
            )
            or name.casefold() in seen
        ):
            _deny("partition_original_locator_invalid")
        seen.add(name.casefold())
        if type(pin) is not list or len(pin) != 2:
            _deny("partition_original_inventory_invalid")
        total += _number(pin[0])
        _hash(pin[1])
    if total > max_total_bytes:
        _deny("partition_original_inventory_byte_bound")


def _root_locator(value):
    if type(value) is not dict or set(value) != {"path", "device", "inode"}:
        _deny("partition_original_root_locator_missing")
    path = value["path"]
    if (
        type(path) is not str
        or not 1 <= len(path) <= 4096
        or "\0" in path
        or not (
            PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()
        )
        or ".." in PureWindowsPath(path).parts
        or ".." in PurePosixPath(path).parts
        or any(
            type(value[name]) is not int or not 0 <= value[name] < 2**128
            for name in ("device", "inode")
        )
    ):
        _deny("partition_original_root_locator_invalid")
    return value["device"], value["inode"]


def derive_native_member_registration_v1(
    *,
    scope,
    b3_document_raw,
    native_linkage_raw,
    expected_b3_policy_sha256,
    expected_b3_event_sha256,
):
    """Bind exact original identities, without authenticating portable linkage."""
    scope_doc = _scope(scope)
    source_policy = _hash(expected_b3_policy_sha256)
    event_sha = _hash(expected_b3_event_sha256)
    if type(b3_document_raw) is not bytes or digest(b3_document_raw) != event_sha:
        _deny("partition_original_b3_hash_changed")
    document, _ = _b3(b3_document_raw, scope=scope_doc, source_policy=source_policy)
    linkage = _json(native_linkage_raw)
    keys = {
        "schema_version",
        "scope",
        "b3_sequence",
        "b3_event_sha256",
        "b3_anchor_sha256",
        "latest_source_reference",
        "native_invocation_id",
        "custody_bundle_id",
        "bundle_sha256",
        "proof_sha256",
        "native_policy_sha256",
        "source_manifest_sha256",
        "raw_inventory",
        "companion_inventory",
        "raw_root",
        "companion_root",
        "original_started",
        "native_started",
        "source_readback_complete",
    }
    if (
        type(linkage) is not dict
        or set(linkage) != keys
        or linkage["schema_version"] != LINKAGE_SCHEMA
        or linkage["scope"] != scope_doc
        or _number(linkage["b3_sequence"]) != document["sequence"]
        or linkage["b3_event_sha256"] != event_sha
        or linkage["b3_anchor_sha256"] != document["anchor_sha256"]
        or _reference(linkage["latest_source_reference"]) != document["reference"]
    ):
        _deny("partition_latest_native_member_mismatch")
    for name in ("native_invocation_id", "custody_bundle_id"):
        if (
            type(linkage[name]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", linkage[name]) is None
        ):
            _deny("partition_native_link_identity_invalid")
    for name in (
        "bundle_sha256",
        "proof_sha256",
        "native_policy_sha256",
        "source_manifest_sha256",
    ):
        _hash(linkage[name])
    if (
        _root_locator(linkage["raw_root"]) == _root_locator(linkage["companion_root"])
        or linkage["raw_root"]["path"] == linkage["companion_root"]["path"]
    ):
        _deny("partition_original_distinct_roots_required")
    _inventory(
        linkage["raw_inventory"],
        max_total_bytes=MAX_CHAIN_BYTES + 4 * 1024 * 1024 + 2 * 65536 + 2 * 1024 * 1024,
    )
    _inventory(linkage["companion_inventory"], max_total_bytes=9 * 512 * 1024)
    if (
        "source.bundle.json" not in linkage["raw_inventory"]
        or linkage["raw_inventory"]["source.bundle.json"][1] != linkage["bundle_sha256"]
        or not {"accepted.json", "attempt.claim.json"} <= set(linkage["raw_inventory"])
    ):
        _deny("partition_original_custody_locator_missing")
    previous = None
    for name in ("original_started", "native_started", "source_readback_complete"):
        stamp = linkage[name]
        if type(stamp) is not dict or set(stamp) != {"utc_ns", "monotonic_ns"}:
            _deny("partition_original_clock_invalid")
        pair = (_number(stamp["utc_ns"]), _number(stamp["monotonic_ns"]))
        if previous is not None and any(
            current < prior for current, prior in zip(pair, previous, strict=True)
        ):
            _deny("partition_original_clock_regressed")
        previous = pair
    result = {
        "schema_version": MEMBER_SCHEMA,
        "policy_sha256": POLICY_SHA256,
        "scope": scope_doc,
        "b3_sequence": document["sequence"],
        "b3_event_sha256": event_sha,
        "b3_anchor_sha256": document["anchor_sha256"],
        "b3_receipt_sha256": document["receipt_sha256"],
        "source_reference": document["reference"],
        "native_linkage_json": native_linkage_raw.decode("ascii"),
        **_unknowns(),
    }
    raw = canonical(result)
    _json(raw)
    return HistoryPartitionRecordV1(raw)


def _checkpoint(scope, source_policy, documents, registrations, previous):
    if (
        type(documents) is not tuple
        or type(registrations) is not tuple
        or len(documents) != PARTITION_SPAN
        or len(registrations) != PARTITION_SPAN
    ):
        _deny("partition_exact32_members_required")
    first = 1 if previous is None else previous["last_sequence"] + 1
    prior_event = None if previous is None else previous["terminal_event_sha256"]
    prior_anchor = None if previous is None else previous["terminal_anchor_sha256"]
    prior_counts = (
        (0, 0, 0)
        if previous is None
        else tuple(previous["cumulative_counts"][name] for name in _COUNTS)
    )
    root = (
        digest(
            canonical(
                [
                    POLICY_SHA256,
                    digest(canonical(scope)),
                    "original_B3_partition_genesis",
                ]
            )
        )
        if previous is None
        else previous["ordered_membership_sha256"]
    )
    missing = []
    members = []
    references = set()
    native_invocations = set()
    overflow = False if previous is None else previous["required_proof_budget_exceeded"]
    genesis = (
        None
        if previous is None
        else (previous["genesis_event_sha256"], previous["genesis_anchor_sha256"])
    )
    for sequence, original, registration in zip(
        range(first, first + PARTITION_SPAN), documents, registrations, strict=True
    ):
        document, anchor = _b3(original, scope=scope, source_policy=source_policy)
        if (
            document["sequence"] != sequence
            or document["previous_sha256"] != prior_event
            or anchor["previous_anchor_sha256"] != prior_anchor
        ):
            _deny("partition_global_chain_gap_or_changed")
        counts = tuple(anchor[name] for name in _COUNTS)
        increments = tuple(
            document[name + "_membership_count"]
            for name in ("source", "coverage", "finding")
        )
        if counts != tuple(
            a + b for a, b in zip(prior_counts, increments, strict=True)
        ):
            _deny("partition_cumulative_membership_changed")
        capture_id = document["reference"]["capture_id"]
        if capture_id in references:
            _deny("partition_duplicate_capture")
        references.add(capture_id)
        event = digest(original)
        registration_sha = None
        if registration is None:
            missing.append(sequence)
        else:
            value = _json(registration)
            if (
                type(value) is not dict
                or type(value.get("native_linkage_json")) is not str
            ):
                _deny("partition_native_registration_invalid")
            derived = derive_native_member_registration_v1(
                scope=HistoryPartitionScopeV1(*scope),
                b3_document_raw=original,
                native_linkage_raw=_ascii(value["native_linkage_json"]),
                expected_b3_policy_sha256=source_policy,
                expected_b3_event_sha256=event,
            )
            if derived.record_json != registration:
                _deny("partition_native_registration_changed")
            invocation = _json(_ascii(value["native_linkage_json"]))[
                "native_invocation_id"
            ]
            if invocation in native_invocations:
                _deny("partition_duplicate_native_invocation")
            native_invocations.add(invocation)
            registration_sha = digest(registration)
        root = digest(
            canonical(
                [
                    root,
                    sequence,
                    event,
                    document["anchor_sha256"],
                    document["receipt_sha256"],
                    document["source_membership_sha256"],
                    document["coverage_membership_sha256"],
                    document["finding_membership_sha256"],
                    registration_sha,
                ]
            )
        )
        if genesis is None:
            genesis = (event, document["anchor_sha256"])
        prior_event, prior_anchor, prior_counts = (
            event,
            document["anchor_sha256"],
            counts,
        )
        overflow = overflow or anchor["required_proof_budget_exceeded"]
        members.append(
            {
                "b3_document_json": original.decode("ascii"),
                "native_registration_json": None
                if registration is None
                else registration.decode("ascii"),
            }
        )
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "policy_sha256": POLICY_SHA256,
        "b3_policy_sha256": source_policy,
        "scope": scope,
        "partition_number": (first - 1) // PARTITION_SPAN + 1,
        "first_sequence": first,
        "last_sequence": first + PARTITION_SPAN - 1,
        "member_count": PARTITION_SPAN,
        "previous_checkpoint_sha256": None
        if previous is None
        else digest(canonical(previous)),
        "previous_terminal_event_sha256": None
        if previous is None
        else previous["terminal_event_sha256"],
        "previous_terminal_anchor_sha256": None
        if previous is None
        else previous["terminal_anchor_sha256"],
        "genesis_event_sha256": genesis[0],
        "genesis_anchor_sha256": genesis[1],
        "terminal_event_sha256": prior_event,
        "terminal_anchor_sha256": prior_anchor,
        "ordered_membership_sha256": root,
        "cumulative_counts": dict(zip(_COUNTS, prior_counts, strict=True)),
        "missing_native_registration_sequences": missing,
        "native_registration_count": PARTITION_SPAN - len(missing),
        "missing_native_registrations_through_sequence": len(missing)
        + (
            0
            if previous is None
            else previous["missing_native_registrations_through_sequence"]
        ),
        "required_proof_budget_exceeded": overflow,
        "members": members,
        **_unknowns(),
    }


def _replay(raw_checkpoints, scope, source_policy):
    if type(raw_checkpoints) is not tuple or any(
        type(raw) is not bytes for raw in raw_checkpoints
    ):
        _deny("partition_exact_checkpoint_bytes_required")
    if sum(map(len, raw_checkpoints)) > MAX_CHAIN_BYTES:
        _deny("partition_cumulative_byte_bound")
    previous = None
    seen_captures, seen_invocations, seen_custody = set(), set(), set()
    for raw in raw_checkpoints:
        value = _json(raw)
        if (
            type(value) is not dict
            or type(value.get("members")) is not list
            or len(value["members"]) != PARTITION_SPAN
        ):
            _deny("partition_checkpoint_invalid")
        documents, registrations = [], []
        for member in value["members"]:
            if (
                type(member) is not dict
                or set(member) != {"b3_document_json", "native_registration_json"}
                or type(member["b3_document_json"]) is not str
            ):
                _deny("partition_checkpoint_member_invalid")
            documents.append(_ascii(member["b3_document_json"]))
            registration = member["native_registration_json"]
            if registration is not None and type(registration) is not str:
                _deny("partition_checkpoint_member_invalid")
            registrations.append(None if registration is None else _ascii(registration))
        derived = _checkpoint(
            scope, source_policy, tuple(documents), tuple(registrations), previous
        )
        if canonical(derived) != raw:
            _deny("partition_checkpoint_changed")
        # Run cross-partition identity checks only after each original member
        # and linkage was shape/hash/chain checked by the same reducer.
        for original, registration in zip(documents, registrations, strict=True):
            source = _json(original)["reference"]["capture_id"]
            if source in seen_captures:
                _deny("partition_cross_partition_capture_conflict")
            seen_captures.add(source)
            if registration is not None:
                registered = _json(registration)
                linkage = _json(_ascii(registered["native_linkage_json"]))
                invocation = linkage["native_invocation_id"]
                if invocation in seen_invocations:
                    _deny("partition_cross_partition_invocation_conflict")
                seen_invocations.add(invocation)
                if linkage["custody_bundle_id"] in seen_custody:
                    _deny("partition_custody_binding_conflict")
                seen_custody.add(linkage["custody_bundle_id"])
        previous = derived
    return previous


def build_partition_checkpoint_v1(
    *,
    scope,
    b3_documents,
    native_registrations,
    previous_checkpoints,
    expected_b3_policy_sha256,
    expected_b3_head_sha256,
):
    """Produce a full32 storage proposal after replaying every prior checkpoint."""
    scope_doc, source_policy = _scope(scope), _hash(expected_b3_policy_sha256)
    previous = _replay(previous_checkpoints, scope_doc, source_policy)
    value = _checkpoint(
        scope_doc, source_policy, b3_documents, native_registrations, previous
    )
    if value["terminal_event_sha256"] != _hash(expected_b3_head_sha256):
        _deny("partition_original_head_changed")
    raw = canonical(value)
    _json(raw)
    # Replay the combined population too, retaining cross-partition conflicts.
    _replay((*previous_checkpoints, raw), scope_doc, source_policy)
    return HistoryPartitionRecordV1(raw)


def replay_partition_checkpoints_v1(
    *,
    scope,
    checkpoints,
    expected_checkpoint_sha256,
    expected_b3_policy_sha256,
    expected_b3_head_sha256,
):
    """Replay all linked original B3 records; no checkpoint-only authority cache."""
    previous = _replay(checkpoints, _scope(scope), _hash(expected_b3_policy_sha256))
    if (
        previous is None
        or digest(checkpoints[-1]) != _hash(expected_checkpoint_sha256)
        or previous["terminal_event_sha256"] != _hash(expected_b3_head_sha256)
    ):
        _deny("partition_current_checkpoint_or_head_changed")
    return HistoryPartitionRecordV1(canonical(previous))
