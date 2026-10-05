"""Offline inventory of unresolved Demo account proofs.

This is a diagnostic projection of a pinned, replayed capture packet and its
materialization. It never authenticates an exchange session, removes a gap,
issues a portfolio snapshot, or grants execution authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_materializer as materializer

MAX_INVENTORY_BYTES = 65536


class AccountGapInventoryError(ValueError):
    """Stable diagnostic error; never includes supplied private-account data."""


# Every unconditional capture gap must have a specific source-bound proof class.
# Other capture/materializer reasons remain visible with an unclassified proof
# requirement until they receive a reviewed mapping here.
_PROOF_BY_REASON = {
    "advanced_product_scope_unverified": "complete_advanced_product_exposure_scope",
    "cross_source_atomicity_unverified": "same_account_cross_source_revision_and_reconciliation",
    "history_ingestion_watermark_unverified": "durable_per_stream_history_ingestion_watermark",
    "history_retention_unverified": "source_bound_history_retention_and_archive_coverage",
    "history_seed_missing": "source_bound_loss_streak_and_realized_outcome_seed",
    "instrument_and_correlation_mapping_missing": "versioned_instrument_and_correlation_coverage",
    "local_uncertain_ledger_missing": "durable_local_inflight_uncertain_and_reservation_readback",
    "non_swap_history_not_requested": "non_swap_history_or_proven_account_product_exclusion",
    "peak_window_evidence_missing": "measured_peak_equity_and_continuous_window",
    "protection_and_cost_mapping_missing": "active_protection_and_all_execution_cost_readback",
    "source_authenticity_unverified": "owned_authenticated_session_and_exact_uid_provenance",
    "all_product_metadata_coverage_unverified": "all_product_metadata_and_leverage_coverage",
    "separate_history_source_join_required": "same_account_current_and_history_source_join",
    "source_fields_missing": "required_exchange_fields_in_raw_response",
    "source_clock_coverage_incomplete": "causal_exchange_source_timestamps",
    "account_mode_mapping_unsupported": "supported_account_and_position_mode_readback",
    "account_instrument_source_missing": "same_capture_instrument_specification_readback",
    "account_instrument_source_conflict": "conflict_free_instrument_specification_reconciliation",
    "account_leverage_coverage_incomplete": "same_capture_cross_and_isolated_leverage_readback",
    "account_metadata_instrument_coverage_incomplete": "complete_instrument_metadata_coverage",
    "balance_source_time_missing": "settlement_balance_source_update_time",
    "single_currency_balance_required": "exact_settlement_currency_equity_readback",
    "settlement_equity_or_margin_missing": "exact_settlement_equity_and_available_margin",
    "balance_liability_scope_unsupported": "liabilities_and_borrowed_balance_mapping",
    "position_anchor_inconsistent": "same_account_position_anchor_reconciliation",
    "local_ledger_missing": "durable_local_ledger_source_readback",
    "local_ledger_revision_missing": "durable_local_ledger_revision_readback",
    "local_ledger_source_time_invalid": "causal_local_ledger_source_time",
    "local_reservation_mapping_incomplete": "complete_local_reservation_projection",
    "local_exchange_binding_unresolved": "exchange_order_to_local_intent_binding",
    "partial_fill_local_union_conservative": "partial_fill_and_local_hold_reconciliation",
    "history_grouping_and_seed_missing": "source_bound_outcome_grouping_and_seed",
    "history_recorded_window_invalid": "valid_pinned_history_window",
    "history_recorded_window_incomplete": "complete_pinned_history_window",
    "history_overlapping_source_conflict": "conflict_free_recent_and_archive_history_join",
    "history_product_mapping_unsupported": "supported_history_product_mapping",
    "history_fill_inventory_unmapped": "all_fill_rows_mapped_to_outcomes",
    "history_cashflow_inventory_unmapped": "all_cashflow_rows_mapped_to_outcomes",
    "closed_outcome_mapping_incomplete": "complete_realized_outcome_mapping",
    "outcome_source_missing": "source_bound_realized_outcome_receipts",
    "outcome_fill_time_missing": "exchange_fill_event_time",
    "funding_accrual_provenance_missing": "holding_bound_funding_accrual_time",
    "peak_samples_missing": "source_bound_peak_equity_samples",
    "peak_sample_mapping_incomplete": "complete_peak_sample_mapping",
    "sampled_peak_below_current_equity": "peak_not_below_current_equity_reconciliation",
    "continuous_peak_window_unverified": "continuous_peak_equity_window",
    "instrument_mapping_incomplete": "complete_contract_and_correlation_projection",
    "cost_source_time_invalid": "causal_execution_cost_source_time",
}


@dataclass(frozen=True, slots=True, repr=False)
class AccountGapInventory:
    """Replayable diagnostic bytes; this type has no portfolio or order API."""

    payload: bytes
    sha256: str

    @property
    def account_complete(self) -> Literal[False]:
        return False

    @property
    def execution_authority(self) -> Literal[False]:
        return False


def _fail(code: str) -> None:
    raise AccountGapInventoryError(code)


def inventory_demo_account_gaps(
    packet_payload: bytes,
    *,
    expected_packet_sha256: str,
    expected_plan_sha256: str,
    inputs: materializer.AccountMaterializationInputs,
    expected_inputs_sha256: str,
) -> AccountGapInventory:
    """Replay exact source pins, then enumerate absent proofs without promotion."""

    if set(capture._BASE_GAPS) - set(_PROOF_BY_REASON):
        _fail("account_gap_proof_catalog_outdated")
    if (
        type(expected_inputs_sha256) is not str
        or re.fullmatch(r"[a-f0-9]{64}", expected_inputs_sha256) is None
    ):
        _fail("account_gap_inputs_pin_invalid")
    packet = capture.verify_demo_account_packet(
        packet_payload,
        expected_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
    )
    mapped = materializer.materialize_demo_portfolio_snapshot(
        packet,
        expected_plan_sha256=expected_plan_sha256,
        expected_packet_sha256=expected_packet_sha256,
        inputs=inputs,
        expected_inputs_sha256=expected_inputs_sha256,
    )
    capture_reasons = set(packet.incomplete_reasons)
    mapping_reasons = set(mapped.incomplete_reasons)
    if (
        not capture_reasons
        or not capture_reasons <= mapping_reasons
        or not mapping_reasons
        or mapped.snapshot is not None
        or packet.account_complete
        or packet.execution_authority
        or packet.source_authenticity_verified
        or mapped.account_complete
        or mapped.execution_authority
        or mapped.source_authenticity_verified
        or mapped.packet_sha256 != expected_packet_sha256
        or mapped.inputs_sha256 != expected_inputs_sha256
    ):
        _fail("account_gap_diagnostic_invariant_invalid")

    findings = [
        {
            "reason": reason,
            "recorded_in": "capture"
            if reason in capture_reasons
            else "materialization",
            "missing_proof": _PROOF_BY_REASON.get(
                reason, "unclassified_source_bound_proof_requires_review"
            ),
        }
        for reason in sorted(mapping_reasons)
    ]
    report = {
        "schema_version": "ctcc.demo_account_gap_inventory.v1",
        "state": "diagnostic_incomplete",
        "plan_sha256": expected_plan_sha256,
        "packet_sha256": expected_packet_sha256,
        "inputs_sha256": expected_inputs_sha256,
        "materialization_sha256": mapped.evaluation_sha256,
        "capture_gap_count": len(capture_reasons),
        "materialization_gap_count": len(mapping_reasons),
        "findings": findings,
        "formal_portfolio_snapshot_issued": False,
        "account_complete": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(payload) > MAX_INVENTORY_BYTES:
        _fail("account_gap_inventory_size_limit")
    return AccountGapInventory(
        payload=payload, sha256=hashlib.sha256(payload).hexdigest()
    )


def verify_demo_account_gap_inventory(
    inventory: AccountGapInventory,
    packet_payload: bytes,
    *,
    expected_packet_sha256: str,
    expected_plan_sha256: str,
    inputs: materializer.AccountMaterializationInputs,
    expected_inputs_sha256: str,
) -> AccountGapInventory:
    """Require exact diagnostic readback and a fresh replay of every source pin."""

    if (
        type(inventory) is not AccountGapInventory
        or type(inventory.payload) is not bytes
        or not 1 <= len(inventory.payload) <= MAX_INVENTORY_BYTES
        or type(inventory.sha256) is not str
        or hashlib.sha256(inventory.payload).hexdigest() != inventory.sha256
    ):
        _fail("account_gap_inventory_readback_invalid")
    replay = inventory_demo_account_gaps(
        packet_payload,
        expected_packet_sha256=expected_packet_sha256,
        expected_plan_sha256=expected_plan_sha256,
        inputs=inputs,
        expected_inputs_sha256=expected_inputs_sha256,
    )
    if inventory != replay:
        _fail("account_gap_inventory_replay_mismatch")
    return replay
