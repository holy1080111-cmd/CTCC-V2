"""Canonical prospective CTCC event cutover operands; never G6 authority.

This historical marker says where CTCC-owned event coverage may BEGIN. It does
not assert complete exchange history, complete financial claims, or permission
to trade. The native owner and separate-session readback issue its custody.
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

SCHEMA = "ctcc.prospective_ctcc_event_cutover.v2"
MAX_BYTES = 16384
NAMESPACE_POLICY = "ctcc-prospective-reservation-namespace-v1"
NATIVE_ENTRY_PREFIX = "CTQ"
NATIVE_PROTECTION_PREFIX = "CTA"
LEGACY_PREFIXES = ("CTCC", "CTCCA", "AUT")
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_EPOCH = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_UID = re.compile(r"[0-9]{1,32}\Z", re.ASCII)
FALSE_FIELDS = (
    "account_complete",
    "account_revision_published",
    "namespace_history_complete",
    "historical_event_history_complete",
    "g6_qualified",
    "execution_authority",
)
FIELDS = frozenset(
    {
        "schema_version",
        "environment",
        "account_id",
        "settlement_currency",
        "epoch_id",
        "generation",
        "active_journal_sequence",
        "active_event_sha256",
        "active_state_sha256",
        "audit_sha256",
        "cutover_id",
        "writer_inventory_sha256",
        "manifest_sha256",
        "credential_session_sha256",
        "source_bundle_sha256",
        "source_current_receipt_sha256",
        "source_native_proof_sha256",
        "source_native_readback_sha256",
        "control_owner_epoch",
        "control_revision",
        "control_event_sha256",
        "control_state_sha256",
        "account_revision",
        "ledger_revision",
        "journal_scan_sha256",
        "journal_scan_json",
        "event_keys_sha256",
        "rows_sha256",
        "transitions_sha256",
        "post_active_readback",
        "coverage_start",
        "namespace_policy",
        "scope_rule",
        "record_kind",
        *FALSE_FIELDS,
    }
)


class ProspectiveCutoverError(ValueError):
    """Fixed redacted denial codes only."""


def deny(code="prospective_cutover_invalid"):
    raise ProspectiveCutoverError(code) from None


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def digest(raw):
    if type(raw) is not bytes:
        deny("prospective_cutover_bytes_required")
    return hashlib.sha256(raw).hexdigest()


def stamp(value):
    if type(value) is not dict or set(value) != {"utc_ns", "monotonic_ns"}:
        deny("prospective_cutover_stamp")
    if any(
        type(n) is not int or not 0 <= n <= 32503680000000000000 for n in value.values()
    ):
        deny("prospective_cutover_stamp")
    return dict(value)


def stamp_utc(value):
    """Same conservative ceil-to-microsecond semantics as native source stamps."""
    ns = stamp(value)["utc_ns"]
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=(ns + 999) // 1000)


def identity(environment, account_id, epoch_id, active_event_sha256):
    return digest(canonical([environment, account_id, epoch_id, active_event_sha256]))


def decode(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES:
        deny("prospective_cutover_wire_bound")

    def pairs(items):
        if len(items) != len({key for key, _ in items}):
            deny("prospective_cutover_duplicate_key")
        return dict(items)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: deny("prospective_cutover_nonfinite"),
        )
        if type(value) is not dict or set(value) != FIELDS or canonical(value) != raw:
            deny("prospective_cutover_wire_shape")
        if (
            value["schema_version"] != SCHEMA
            or value["environment"] != "demo"
            or value["settlement_currency"] != "USDT"
            or type(value["account_id"]) is not str
            or _UID.fullmatch(value["account_id"]) is None
            or type(value["epoch_id"]) is not str
            or _EPOCH.fullmatch(value["epoch_id"]) is None
        ):
            deny("prospective_cutover_scope")
        for key, item in value.items():
            if (key.endswith("_sha256") or key == "cutover_id") and (
                type(item) is not str or _SHA.fullmatch(item) is None
            ):
                deny("prospective_cutover_digest")
        for key in (
            "generation",
            "active_journal_sequence",
            "control_owner_epoch",
            "control_revision",
            "account_revision",
            "ledger_revision",
        ):
            minimum = 0 if key in {"account_revision", "ledger_revision"} else 1
            if type(value[key]) is not int or not minimum <= value[key] <= 2**63 - 1:
                deny("prospective_cutover_revision")
        if value["ledger_revision"] < value["account_revision"]:
            deny("prospective_cutover_revision")
        scan_raw = value["journal_scan_json"]
        if type(scan_raw) is not str or not 1 <= len(scan_raw) <= 4096:
            deny("prospective_cutover_scan_bound")
        scan = json.loads(scan_raw, object_pairs_hook=pairs)
        if (
            type(scan) is not dict
            or canonical(scan).decode("ascii") != scan_raw
            or digest(scan_raw.encode("ascii")) != value["journal_scan_sha256"]
            or scan.get("schema_version") != "ctcc.consumed_event_journal_inspection.v1"
            or scan.get("db_journal_row_chain_verified") is not True
            or any(
                scan.get(key) is not False
                for key in (
                    "db_journal_complete",
                    "external_event_history_complete",
                    "source_authenticity_verified",
                    "execution_authority",
                )
            )
            or scan.get("admission") != "DENY"
            or any(
                scan.get(key) != value[key]
                for key in (
                    "account_revision",
                    "ledger_revision",
                    "event_keys_sha256",
                    "rows_sha256",
                    "transitions_sha256",
                )
            )
        ):
            deny("prospective_cutover_scan_binding")
        if any(value[key] is not False for key in FALSE_FIELDS):
            deny("prospective_cutover_authority_claim")
        before, start = (
            stamp(value["post_active_readback"]),
            stamp(value["coverage_start"]),
        )
        if any(start[key] <= before[key] for key in before):
            deny("prospective_cutover_start_not_after_readback")
        if (
            value["cutover_id"]
            != identity(
                value["environment"],
                value["account_id"],
                value["epoch_id"],
                value["active_event_sha256"],
            )
            or value["namespace_policy"] != NAMESPACE_POLICY
            or value["scope_rule"]
            != "original_trigger_candle_open_strictly_after_start"
            or value["record_kind"] != "prospective_cutover_not_g6_or_order_authority"
        ):
            deny("prospective_cutover_binding")
        return value
    except ProspectiveCutoverError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        deny("prospective_cutover_wire_invalid")


def require_trigger_after_start(raw, *, original_trigger_time, bar_seconds):
    """Scope predicate only. The caller must privately replay the raw candle."""
    value = decode(raw)
    if (
        type(original_trigger_time) is not datetime
        or original_trigger_time.tzinfo is not UTC
        or type(bar_seconds) is not int
        or bar_seconds not in {300, 900, 3600, 14400}
    ):
        deny("prospective_cutover_trigger_time")
    if original_trigger_time - timedelta(seconds=bar_seconds) <= stamp_utc(
        value["coverage_start"]
    ):
        deny("prospective_cutover_trigger_predates_coverage")
