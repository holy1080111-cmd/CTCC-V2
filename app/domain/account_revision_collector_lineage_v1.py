"""Owned CURRENT lineage prerequisite, never a complete account-revision issuer.

Only the repository's live native-owner handoff records these bytes. This pure
contract can verify persisted bindings, but caller construction grants nothing.
The existing V4 source has no complete history/seed/HWM/portfolio producer.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.trade_qualification import account_capture_journal as journal

SCHEMA = "ctcc.collector_owned_current_revision_lineage.v1"
MAX_DOCUMENT_BYTES = 262144
MAX_CLAIMS_BYTES = 8 * 1024 * 1024
UNRESOLVED = (
    "complete_source_derived_portfolio_snapshot_unavailable",
    "full_history_funding_and_finality_unproven",
    "source_derived_loss_streak_seed_unknown",
    "preserved_native_hwm_and_drawdown_window_unproven",
    "registered_account_region_unverified",
    "complete_account_revision_not_issued",
)
KEYS = frozenset(
    {
        "schema_version",
        "scope",
        "source_reference",
        "source_bundle_sha256",
        "source_policy_sha256",
        "proof_envelope_sha256",
        "native_proof_sha256",
        "native_readback_sha256",
        "custody_id",
        "owner_sampled_at",
        "original_expires_at",
        "packet_completed_at",
        "account_revision",
        "ledger_revision",
        "claims_sha256",
        "claims_binding_kind",
        "control_revision",
        "control_event_sha256",
        "control_state_sha256",
        "db_role_oid",
        "db_backend_pid",
        "db_transaction_id",
        "db_recorded_at",
        "unresolved_obligations",
        "account_revision_issued",
        "account_complete",
        "execution_authority",
        "admission",
    }
)


class CollectorRevisionLineageError(ValueError):
    """Fixed codes only."""


def deny(code):
    raise CollectorRevisionLineageError(code) from None


def _sha(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{64}", value) is None:
        deny("collector_lineage_digest_invalid")


def _time(value):
    if type(value) is not str:
        deny("collector_lineage_clock_invalid")
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        deny("collector_lineage_clock_invalid")
    if result.tzinfo is not UTC or result.isoformat() != value:
        deny("collector_lineage_clock_invalid")
    return result


def decode(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                deny("collector_lineage_noncanonical")
            result[key] = value
        return result

    def number(_value):
        deny("collector_lineage_noncanonical")

    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        deny("collector_lineage_document_bound")
    try:
        result = json.loads(
            raw, object_pairs_hook=pairs, parse_float=number, parse_constant=number
        )
        if journal.canonical(result) != raw:
            deny("collector_lineage_noncanonical")
        return result
    except CollectorRevisionLineageError:
        raise
    except Exception:  # noqa: BLE001 -- private failures are replaced by fixed codes
        deny("collector_lineage_document_invalid")


def validate_document(
    raw,
    *,
    scope,
    reference,
    claims_json,
    account_revision,
    ledger_revision,
    control_revision,
    control_event_sha256,
    control_state_sha256,
):
    """Validate exact stored facts. No True/issued/complete assertion is admitted."""
    if (
        type(scope) is not list
        or len(scope) != 3
        or any(type(item) is not str for item in scope)
        or scope[0] != "demo"
        or re.fullmatch("[0-9]{1,32}", scope[1]) is None
        or re.fullmatch("[A-Z0-9]{1,16}", scope[2]) is None
        or type(reference) is not dict
        or set(reference)
        != {
            "capture_id",
            "head_sha256",
            "plan_sha256",
            "packet_sha256",
            "session_binding_sha256",
        }
        or any(type(item) is not str for item in reference.values())
        or re.fullmatch("[a-f0-9]{32}", reference["capture_id"]) is None
    ):
        deny("collector_lineage_scope_or_reference_invalid")
    for name, item in reference.items():
        if name != "capture_id":
            _sha(item)
    if (
        any(
            type(item) is not int
            for item in (account_revision, ledger_revision, control_revision)
        )
        or not 0 <= account_revision <= ledger_revision
        or control_revision <= 0
    ):
        deny("collector_lineage_revision_invalid")
    _sha(control_event_sha256)
    _sha(control_state_sha256)
    value = decode(raw)
    if (
        type(value) is not dict
        or set(value) != KEYS
        or value["schema_version"] != SCHEMA
    ):
        deny("collector_lineage_schema_invalid")
    if (
        value["scope"] != scope
        or value["source_reference"] != reference
        or value["claims_binding_kind"] != "UNAUTHENTICATED_LEDGER_SNAPSHOT"
        or value["unresolved_obligations"] != list(UNRESOLVED)
        or value["account_revision_issued"] is not False
        or value["account_complete"] is not False
        or value["execution_authority"] is not False
        or value["admission"] != "DENY"
    ):
        deny("collector_lineage_source_not_complete")
    for name in (
        "account_revision",
        "ledger_revision",
        "control_revision",
        "db_role_oid",
        "db_backend_pid",
    ):
        if type(value[name]) is not int or value[name] < (
            0 if name in {"account_revision", "ledger_revision"} else 1
        ):
            deny("collector_lineage_revision_invalid")
    if (
        value["account_revision"] != account_revision
        or value["ledger_revision"] != ledger_revision
        or value["control_revision"] != control_revision
        or value["control_event_sha256"] != control_event_sha256
        or value["control_state_sha256"] != control_state_sha256
    ):
        deny("collector_lineage_binding_changed")
    expected_claims = None
    if claims_json is not None:
        if (
            type(claims_json) is not str
            or not 1 <= len(claims_json.encode("utf-8")) <= MAX_CLAIMS_BYTES
        ):
            deny("collector_lineage_claims_bound")
        expected_claims = journal.digest(claims_json.encode("utf-8"))
    if value["claims_sha256"] != expected_claims or (account_revision == 0) != (
        claims_json is None
    ):
        deny("collector_lineage_claims_changed")
    for name in (
        "source_bundle_sha256",
        "source_policy_sha256",
        "proof_envelope_sha256",
        "native_proof_sha256",
        "native_readback_sha256",
        "control_event_sha256",
        "control_state_sha256",
    ):
        _sha(value[name])
    if (
        type(value["custody_id"]) is not str
        or re.fullmatch("[a-f0-9]{32}", value["custody_id"]) is None
        or type(value["db_transaction_id"]) is not str
        or re.fullmatch("[0-9]{1,20}", value["db_transaction_id"]) is None
    ):
        deny("collector_lineage_database_identity_invalid")
    completed, recorded, sampled, expires = (
        _time(value[name])
        for name in (
            "packet_completed_at",
            "db_recorded_at",
            "owner_sampled_at",
            "original_expires_at",
        )
    )
    if not completed <= recorded <= sampled < expires:
        deny("collector_lineage_clock_order")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class CollectorCurrentRevisionLineageReadback:
    lineage_json: bytes
    lineage_sha256: str

    @property
    def account_revision_issued(self):
        return False

    @property
    def complete_claims_authenticated(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"
