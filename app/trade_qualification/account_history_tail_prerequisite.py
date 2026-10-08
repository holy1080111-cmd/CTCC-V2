"""Replay a later history overlap against original Demo B1 history/current chains.

This is a bounded, offline diagnostic. A second query can discover a late or
conflicting row, but matching finite queries cannot establish exchange-wide
atomicity or prove that future late records will never arrive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as source_join
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.demo_history_tail_prerequisite.v1",
        "environment": "demo",
        "region": "global",
        "history_plan": "ctcc.demo_account_plan.v5",
        "current_plan": "ctcc.demo_current_account_plan.v6",
        "history_query_policy_sha256": history.POLICY_SHA256,
        "recorded_join_policy_sha256": source_join.POLICY_SHA256,
        "source": "three_distinct_original_B1_raw_page_chains",
        "chronology": "history_then_current_then_overlapping_history",
        "identity": "exact_uid_mainuid_session_region_origin_registration_mode_currency",
        "history_tail_closed": False,
        "future_late_arrival_finality": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)


class HistoryTailPrerequisiteError(ValueError):
    """Only fixed public-safe errors; no private account data in messages."""


def _deny(code):
    raise HistoryTailPrerequisiteError(code)


@dataclass(frozen=True, slots=True, repr=False)
class HistoryTailPrerequisite:
    receipt_json: bytes

    def __post_init__(self):
        if (
            type(self.receipt_json) is not bytes
            or not 1 <= len(self.receipt_json) <= MAX_RECEIPT_BYTES
        ):
            _deny("history_tail_receipt_invalid")
        try:
            value = json.loads(self.receipt_json)
            canonical = journal.canonical(value)
        except Exception:  # noqa: BLE001 -- direct construction carries no authority
            _deny("history_tail_receipt_invalid")
        if (
            type(value) is not dict
            or canonical != self.receipt_json
            or value.get("schema_version") != "ctcc.demo_history_tail_prerequisite.v1"
            or value.get("policy_sha256") != POLICY_SHA256
            or value.get("history_tail_closed") is not False
            or value.get("account_complete") is not False
            or value.get("execution_authority") is not False
            or value.get("source_authenticity_verified") is not False
            or value.get("exchange_global_eof_verified") is not False
            or value.get("funding_accrual_provenance_verified") is not False
            or value.get("future_late_arrival_finality_verified") is not False
            or value.get("snapshot", object()) is not None
            or value.get("admission") != "DENY"
        ):
            _deny("history_tail_receipt_invalid")

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


def _exact_reference(chain, reference):
    document = observed.reference_document(reference)
    if observed.reference_document(observed.source_reference(chain)) != document:
        _deny("history_tail_source_reference_mismatch")
    return document


def _packet(chain, reference):
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        _deny("history_tail_original_packet_required")
    return capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )


def verify_history_tail_prerequisites(
    *,
    history_chain,
    history_reference,
    current_chain,
    current_reference,
    continuation_chain,
    continuation_reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
):
    """Replay original chains; never promote repeated history into finality."""
    try:
        return _verify(
            history_chain,
            history_reference,
            current_chain,
            current_reference,
            continuation_chain,
            continuation_reference,
            scope,
            validated_at,
            expected_policy_sha256,
        )
    except HistoryTailPrerequisiteError:
        raise
    except Exception:  # noqa: BLE001 -- redact all private source/SQL failures
        raise HistoryTailPrerequisiteError("history_tail_source_invalid") from None


def _verify(
    old_chain,
    old_ref,
    current_chain,
    current_ref,
    next_chain,
    next_ref,
    scope,
    validated_at,
    policy_pin,
):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(policy_pin) is not str
        or policy_pin != POLICY_SHA256
        or type(validated_at) is not datetime
        or any(
            type(chain) is not tuple
            or not 1 <= len(chain) <= journal.MAX_EVENTS
            or any(type(item) is not journal.JournalReadback for item in chain)
            for chain in (old_chain, current_chain, next_chain)
        )
    ):
        _deny("history_tail_inputs_invalid")
    if (
        sum(
            len(item.event.event_json)
            + len(item.event.raw_body or b"")
            + len(item.event.packet_payload or b"")
            for chain in (old_chain, current_chain, next_chain)
            for item in chain
        )
        > MAX_SOURCE_BYTES
    ):
        _deny("history_tail_inputs_invalid")
    references = [
        _exact_reference(chain, reference)
        for chain, reference in (
            (old_chain, old_ref),
            (current_chain, current_ref),
            (next_chain, next_ref),
        )
    ]
    if len({item["capture_id"] for item in references}) != 3:
        _deny("history_tail_distinct_captures_required")

    # Existing replay verifies raw bytes, each page query/cursor and the old
    # history-to-current account identity and recorded local checkpoint.
    joined = source_join.join_recorded_account_sources(
        history_chain=old_chain,
        history_reference=old_ref,
        current_chain=current_chain,
        current_reference=current_ref,
        scope=scope,
        validated_at=validated_at,
    )
    old_proof = history.verify_history_query_chain(
        old_chain, **observed._pins(old_ref, scope)
    )
    next_proof = history.verify_history_query_chain(
        next_chain, **observed._pins(next_ref, scope)
    )
    old_packet, current_packet, next_packet = (
        _packet(chain, reference)
        for chain, reference in (
            (old_chain, old_ref),
            (current_chain, current_ref),
            (next_chain, next_ref),
        )
    )
    if (
        type(old_packet.plan) is not capture.AllProductDemoAccountCapturePlan
        or type(current_packet.plan) is not capture.CurrentDemoAccountCapturePlanV6
        or type(next_packet.plan) is not capture.AllProductDemoAccountCapturePlan
    ):
        _deny("history_tail_plan_version_mismatch")
    old, current, newer = old_packet.plan, current_packet.plan, next_packet.plan
    for name in (
        "environment",
        "expected_uid",
        "expected_main_uid",
        "session_binding_id",
        "settlement_currency",
        "registration_region",
        "origin",
        "registration_evidence_sha256",
    ):
        if getattr(old, name) != getattr(current, name) or getattr(
            old, name
        ) != getattr(newer, name):
            _deny("history_tail_identity_or_region_changed")
    if (
        old.registration_region != "global"
        or source_join._checkpoint(next_chain) != source_join._checkpoint(old_chain)
        or source_join._config(old_packet, "config_after")
        != source_join._config(next_packet, "config_after")
    ):
        _deny("history_tail_scope_or_local_revision_changed")
    current_finished = current_packet.completed_at
    newer_started = next_packet.observations[0].request_started_at
    at = capture._utc(validated_at)
    if not current_finished < newer_started <= next_packet.completed_at <= at:
        _deny("history_tail_capture_chronology_invalid")
    if (
        newer.history_start > old.history_end
        or newer.history_end < current_finished
        or newer.history_end < old.history_end
    ):
        _deny("history_tail_requested_overlap_missing")

    comparison_bytes = history.compare_history_query_chains(
        previous_chain=old_chain,
        previous_pins=observed._pins(old_ref, scope),
        current_chain=next_chain,
        current_pins=observed._pins(next_ref, scope),
    )
    comparison = json.loads(comparison_bytes)
    old_value, next_value, join_value = (
        json.loads(item.receipt_json) for item in (old_proof, next_proof, joined)
    )
    blockers = set(join_value["blocking_reasons"])
    if any(
        row["requested_generation_window_covered"] is not True
        for proof in (old_value, next_value)
        for row in proof["coverage"]
    ):
        blockers.add("history_requested_generation_coverage_incomplete")
    if comparison["dependent_current_claims_require_reconciliation"]:
        blockers.add("history_overlap_discovery_requires_reconciliation")
    blockers.update(
        {
            "post_continuation_current_capture_required",
            "exchange_history_finality_unproven",
            "historical_native_source_unverified",
        }
    )
    receipt = journal.canonical(
        {
            "schema_version": "ctcc.demo_history_tail_prerequisite.v1",
            "policy_sha256": POLICY_SHA256,
            "scope_sha256": join_value["scope_sha256"],
            "source_references": references,
            "recorded_join_receipt_sha256": joined.receipt_sha256,
            "previous_history_query_receipt_sha256": old_proof.receipt_sha256,
            "continuation_history_query_receipt_sha256": next_proof.receipt_sha256,
            "history_comparison_sha256": journal.digest(comparison_bytes),
            "previous_history_requested_end": old.history_end.isoformat(),
            "current_capture_completed_at": current_finished.isoformat(),
            "continuation_history_requested_start": newer.history_start.isoformat(),
            "continuation_history_requested_end": newer.history_end.isoformat(),
            "continuation_capture_started_at": newer_started.isoformat(),
            "continuation_capture_completed_at": next_packet.completed_at.isoformat(),
            "validated_at": at.isoformat(),
            "history_comparison_findings": comparison["findings"],
            "uncompared_query_domains": comparison["uncompared_query_domains"],
            "blocking_reasons": sorted(blockers),
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
    return HistoryTailPrerequisite(receipt)


def verify_history_tail_prerequisite_receipt(
    receipt: HistoryTailPrerequisite, **original_inputs
) -> HistoryTailPrerequisite:
    """A saved diagnostic is usable only after exact original-chain replay."""
    if type(receipt) is not HistoryTailPrerequisite:
        _deny("history_tail_exact_receipt_required")
    checked = HistoryTailPrerequisite(receipt.receipt_json)
    replayed = verify_history_tail_prerequisites(**original_inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("history_tail_receipt_changed")
    return replayed
