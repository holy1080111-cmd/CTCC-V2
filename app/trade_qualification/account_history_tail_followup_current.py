"""Replay a follow-up current Demo capture after a repeated history query.

Four finite B1 observations cannot establish exchange-wide history finality,
an atomic exchange revision, a native current owner or execution authority.
This offline diagnostic only proves that the fourth original chain was read
and joined to the third, under the same recorded identity and local checkpoint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as source_join
from app.trade_qualification import account_history_tail_prerequisite as tail
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "scope_sha256",
        "source_references",
        "three_chain_receipt_sha256",
        "followup_current_join_receipt_sha256",
        "prior_blocking_reasons",
        "blocking_reasons",
        "history_comparison_findings",
        "bounded_followup_current_query_observed",
        "followup_current_capture_started_at",
        "followup_current_capture_completed_at",
        "validated_at",
        "history_tail_closed",
        "exchange_global_eof_verified",
        "funding_accrual_provenance_verified",
        "future_late_arrival_finality_verified",
        "snapshot",
        "account_complete",
        "source_authenticity_verified",
        "execution_authority",
        "admission",
    }
)
_REQUIRED_BLOCKERS = frozenset(
    {
        "exchange_history_finality_unproven",
        "historical_native_source_unverified",
        "post_continuation_current_native_proof_required",
        "post_continuation_locked_local_revision_readback_required",
        "history_tail_not_atomically_closed",
    }
)
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_history_tail_followup_current.v1",
        "three_chain_policy_sha256": tail.POLICY_SHA256,
        "recorded_join_policy_sha256": source_join.POLICY_SHA256,
        "source": "four_distinct_original_B1_raw_page_chains",
        "chronology": "history_then_current_then_overlapping_history_then_current",
        "identity": "exact_uid_mainuid_session_region_origin_registration_mode_currency",
        "bounded_followup_current_query_observed": True,
        "exchange_global_eof_verified": False,
        "history_tail_closed": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class FollowupCurrentError(ValueError):
    """Fixed denial codes; no private account data in messages."""


def _deny(code):
    raise FollowupCurrentError(code)


def _sha(value):
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _blockers(value):
    return (
        type(value) is list
        and all(type(item) is str for item in value)
        and sorted(set(value)) == value
    )


@dataclass(frozen=True, slots=True, repr=False)
class FollowupCurrentDiagnostic:
    receipt_json: bytes

    def __post_init__(self):
        if (
            type(self.receipt_json) is not bytes
            or not 1 <= len(self.receipt_json) <= MAX_RECEIPT_BYTES
        ):
            _deny("followup_current_receipt_invalid")
        try:
            value = json.loads(self.receipt_json)
            canonical = journal.canonical(value)
        except Exception:  # noqa: BLE001 -- direct construction carries no authority
            _deny("followup_current_receipt_invalid")
        if (
            type(value) is not dict
            or canonical != self.receipt_json
            or set(value) != _RECEIPT_KEYS
            or value.get("schema_version")
            != "ctcc.demo_history_tail_followup_current.v1"
            or value.get("policy_sha256") != POLICY_SHA256
            or any(
                not _sha(value.get(key))
                for key in (
                    "scope_sha256",
                    "three_chain_receipt_sha256",
                    "followup_current_join_receipt_sha256",
                )
            )
            or not _blockers(value.get("prior_blocking_reasons"))
            or not _blockers(value.get("blocking_reasons"))
            or "post_continuation_current_capture_required"
            not in value["prior_blocking_reasons"]
            or "post_continuation_current_capture_required" in value["blocking_reasons"]
            or not _REQUIRED_BLOCKERS.issubset(value["blocking_reasons"])
            or type(value.get("history_comparison_findings")) is not list
            or value.get("bounded_followup_current_query_observed") is not True
            or value.get("history_tail_closed") is not False
            or value.get("exchange_global_eof_verified") is not False
            or value.get("funding_accrual_provenance_verified") is not False
            or value.get("future_late_arrival_finality_verified") is not False
            or value.get("source_authenticity_verified") is not False
            or value.get("snapshot", object()) is not None
            or value.get("account_complete") is not False
            or value.get("execution_authority") is not False
            or value.get("admission") != "DENY"
        ):
            _deny("followup_current_receipt_invalid")
        references = value["source_references"]
        if type(references) is not list or len(references) != 4:
            _deny("followup_current_receipt_invalid")
        try:
            if (
                any(
                    type(reference) is not dict
                    or observed.reference_document(
                        observed.CaptureReference(**reference)
                    )
                    != reference
                    for reference in references
                )
                or len({reference["capture_id"] for reference in references}) != 4
            ):
                _deny("followup_current_receipt_invalid")
            started, finished, validated = (
                datetime.fromisoformat(value[key])
                for key in (
                    "followup_current_capture_started_at",
                    "followup_current_capture_completed_at",
                    "validated_at",
                )
            )
            if (
                any(
                    stamp.tzinfo is None
                    or stamp.utcoffset() is None
                    or stamp.utcoffset().total_seconds() != 0
                    for stamp in (started, finished, validated)
                )
                or not started <= finished <= validated
            ):
                _deny("followup_current_receipt_invalid")
        except FollowupCurrentError:
            raise
        except Exception:  # noqa: BLE001 -- reject malformed saved receipts
            _deny("followup_current_receipt_invalid")

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False

    @property
    def admission(self):
        return "DENY"


def verify_followup_current_diagnostic(
    *,
    history_chain,
    history_reference,
    current_chain,
    current_reference,
    continuation_chain,
    continuation_reference,
    followup_current_chain,
    followup_current_reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
):
    """Reopen all four original B1 chains; publish only a DENY diagnostic."""
    try:
        return _verify(
            history_chain,
            history_reference,
            current_chain,
            current_reference,
            continuation_chain,
            continuation_reference,
            followup_current_chain,
            followup_current_reference,
            scope,
            validated_at,
            expected_policy_sha256,
        )
    except FollowupCurrentError:
        raise
    except Exception:  # noqa: BLE001 -- redact private source/parser failures
        raise FollowupCurrentError("followup_current_source_invalid") from None


def _verify(
    history_chain,
    history_reference,
    current_chain,
    current_reference,
    continuation_chain,
    continuation_reference,
    followup_current_chain,
    followup_current_reference,
    scope,
    validated_at,
    policy_pin,
):
    checked_bootstrap(scope, LedgerScope)
    chains = (
        history_chain,
        current_chain,
        continuation_chain,
        followup_current_chain,
    )
    references = (
        history_reference,
        current_reference,
        continuation_reference,
        followup_current_reference,
    )
    if (
        type(policy_pin) is not str
        or policy_pin != POLICY_SHA256
        or type(validated_at) is not datetime
        or any(
            type(chain) is not tuple
            or not 1 <= len(chain) <= journal.MAX_EVENTS
            or any(type(item) is not journal.JournalReadback for item in chain)
            for chain in chains
        )
    ):
        _deny("followup_current_inputs_invalid")
    if (
        sum(
            len(item.event.event_json)
            + len(item.event.raw_body or b"")
            + len(item.event.packet_payload or b"")
            for chain in chains
            for item in chain
        )
        > MAX_SOURCE_BYTES
    ):
        _deny("followup_current_inputs_invalid")
    source_references = [
        tail._exact_reference(chain, reference)
        for chain, reference in zip(chains, references, strict=True)
    ]
    if len({item["capture_id"] for item in source_references}) != 4:
        _deny("followup_current_distinct_captures_required")
    three = tail.verify_history_tail_prerequisites(
        history_chain=history_chain,
        history_reference=history_reference,
        current_chain=current_chain,
        current_reference=current_reference,
        continuation_chain=continuation_chain,
        continuation_reference=continuation_reference,
        scope=scope,
        validated_at=validated_at,
    )
    latest = source_join.join_recorded_account_sources(
        history_chain=continuation_chain,
        history_reference=continuation_reference,
        current_chain=followup_current_chain,
        current_reference=followup_current_reference,
        scope=scope,
        validated_at=validated_at,
    )
    prior = json.loads(three.receipt_json)
    joined = json.loads(latest.receipt_json)
    if (
        prior["scope_sha256"] != joined["scope_sha256"]
        or joined["current_source_reference"] != source_references[3]
        or joined["history_source_reference"] != source_references[2]
    ):
        _deny("followup_current_scope_or_source_changed")
    if {
        "measured_current_receipt_stale",
        "current_response_interval_exceeds_policy",
    } & set(joined["blocking_reasons"]):
        _deny("followup_current_lease_expired")
    # The earlier diagnostic is immutable. Its fourth-capture prerequisite is
    # met only for this new bounded replay; its other blockers remain intact.
    blockers = set(prior["blocking_reasons"])
    blockers.discard("post_continuation_current_capture_required")
    blockers.update(joined["blocking_reasons"])
    blockers.update(
        {
            "post_continuation_current_native_proof_required",
            "post_continuation_locked_local_revision_readback_required",
            "exchange_history_finality_unproven",
            "historical_native_source_unverified",
        }
    )
    receipt = journal.canonical(
        {
            "schema_version": "ctcc.demo_history_tail_followup_current.v1",
            "policy_sha256": POLICY_SHA256,
            "scope_sha256": prior["scope_sha256"],
            "source_references": source_references,
            "three_chain_receipt_sha256": three.receipt_sha256,
            "followup_current_join_receipt_sha256": latest.receipt_sha256,
            "prior_blocking_reasons": prior["blocking_reasons"],
            "blocking_reasons": sorted(blockers),
            "history_comparison_findings": prior["history_comparison_findings"],
            "bounded_followup_current_query_observed": True,
            "followup_current_capture_started_at": joined["current_capture_started_at"],
            "followup_current_capture_completed_at": joined[
                "current_capture_completed_at"
            ],
            "validated_at": joined["validated_at"],
            "history_tail_closed": False,
            "exchange_global_eof_verified": False,
            "funding_accrual_provenance_verified": False,
            "future_late_arrival_finality_verified": False,
            "snapshot": None,
            "account_complete": False,
            "source_authenticity_verified": False,
            "execution_authority": False,
            "admission": "DENY",
        }
    )
    return FollowupCurrentDiagnostic(receipt)


def verify_followup_current_receipt(
    receipt: FollowupCurrentDiagnostic, **original_inputs
) -> FollowupCurrentDiagnostic:
    """A saved result cannot substitute for exact original-chain replay."""
    if type(receipt) is not FollowupCurrentDiagnostic:
        _deny("followup_current_exact_receipt_required")
    checked = FollowupCurrentDiagnostic(receipt.receipt_json)
    replayed = verify_followup_current_diagnostic(**original_inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("followup_current_receipt_changed")
    return replayed
