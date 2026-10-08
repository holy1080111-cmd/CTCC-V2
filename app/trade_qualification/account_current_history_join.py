"""Pure join of original history and fresh current Demo account source chains.

The join checks identity and recorded local checkpoint continuity. It cannot
authenticate DB readback, prove the exchange's atomic account state, or mint a
PortfolioRiskSnapshot. Both original chains must be independently replayed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.recorded_account_current_history_join.v1",
        "current_policy_sha256": current.V6_POLICY_SHA256,
        "history_policy_sha256": history.POLICY_SHA256,
        "history_capture_plan": "ctcc.demo_account_plan.v5",
        "current_capture_plan": "ctcc.demo_current_account_plan.v6",
        "scope": "same_exact_environment_uid_main_uid_session_region_mode_currency",
        "local_revision": "same_recorded_B1_checkpoint_hash_not_DB_current_revision",
        "maximum_requested_history_tail_gap_seconds": 120,
        "complete_history_tail": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
V7_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.recorded_account_current_history_join.v2",
        "prior_join_policy_sha256": POLICY_SHA256,
        "current_policy_sha256": current.V7_POLICY_SHA256,
        "history_policy_sha256": history.POLICY_SHA256,
        "history_capture_plan": "ctcc.demo_account_plan.v5",
        "current_capture_plan": "ctcc.demo_current_account_plan.v7",
        "scope": "same_exact_environment_uid_main_uid_session_region_mode_currency",
        "local_revision": "same_recorded_B1_checkpoint_hash_not_DB_current_revision",
        "maximum_requested_history_tail_gap_seconds": 120,
        "complete_history_tail": False,
        "flat_start_permission": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
V7_POLICY_SHA256 = journal.digest(V7_POLICY_BYTES)
V7_ORDERED_POLICY_BYTES = journal.canonical(
    {
        "version": "ctcc.recorded_account_current_history_join.v3",
        "prior_join_policy_sha256": V7_POLICY_SHA256,
        "current_policy_sha256": current.V7_POLICY_SHA256,
        "history_policy_sha256": history.POLICY_SHA256,
        "history_capture_plan": "ctcc.demo_account_plan.v5",
        "current_capture_plan": "ctcc.demo_current_account_plan.v7",
        "history_terminal_db_timestamp_before_current_request": True,
        "history_commit_before_current_request": False,
        "same_recorded_local_checkpoint_required": True,
        "complete_history_tail": False,
        "flat_start_permission": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
V7_ORDERED_POLICY_SHA256 = journal.digest(V7_ORDERED_POLICY_BYTES)


class AccountSourceJoinError(ValueError):
    """Fixed private-data-safe denial codes."""


@dataclass(frozen=True, slots=True, repr=False)
class RecordedAccountSourceJoin:
    receipt_json: bytes

    @property
    def receipt_sha256(self):
        return journal.digest(self.receipt_json)

    @property
    def snapshot(self):
        return None

    @property
    def account_complete(self):
        return False

    @property
    def execution_authority(self):
        return False


def _deny(code):
    raise AccountSourceJoinError(code)


def _packet(chain, reference):
    payloads = [
        item.event.packet_payload for item in chain if item.event.packet_payload
    ]
    if len(payloads) != 1:
        _deny("account_join_original_packet_required")
    return capture.verify_demo_account_packet(
        payloads[0],
        expected_sha256=reference.packet_sha256,
        expected_plan_sha256=reference.plan_sha256,
    )


def _config(packet, stream):
    pages = [page for page in packet.observations if page.request.stream == stream]
    if len(pages) != 1 or len(pages[0].rows) != 1:
        _deny("account_join_original_config_required")
    return json.loads(pages[0].rows[0].canonical_json)


def _checkpoint(chain):
    first, saved = (
        journal.checked_event(chain[0].event),
        journal.checked_event(chain[-2].event),
    )
    if first["kind"] != "capture_start" or saved["kind"] != "packet_recorded":
        _deny("account_join_recorded_checkpoint_missing")
    checkpoint = first["data"].get("local_checkpoint_sha256")
    journal._sha(checkpoint)
    if saved["data"].get("local_checkpoint_sha256") != checkpoint:
        _deny("account_join_recorded_checkpoint_changed")
    return checkpoint


def _recorded_db_chain_sha256(chain):
    """Bind every original event and its DB timestamp across locked reads.

    Both callers first replay the exact B1 raw chain. This digest is a local
    continuity check, not a proof that the original transaction committed
    before a particular network request.
    """
    return journal.digest(
        journal.canonical(
            [
                [
                    journal.digest(item.event.event_json),
                    capture._utc(item.db_recorded_at).isoformat(),
                ]
                for item in chain
            ]
        )
    )


def join_recorded_account_sources(
    *,
    history_chain,
    history_reference,
    current_chain,
    current_reference,
    scope,
    validated_at,
    expected_policy_sha256=POLICY_SHA256,
):
    """Recompute both original chains; absence of a complete tail stays unknown.

    The current chain starts after its own publication barrier. A future owned
    coordinator must bind the resulting checkpoint hash to a fresh DB readback
    under the account lock and prove history/funding/HWM before risk admission.
    """
    try:
        return _join(
            history_chain,
            history_reference,
            current_chain,
            current_reference,
            scope,
            validated_at,
            expected_policy_sha256,
        )
    except AccountSourceJoinError:
        raise
    except Exception:  # noqa: BLE001 -- never expose private account/source data
        raise AccountSourceJoinError("account_source_join_invalid") from None


def _join(
    history_chain,
    history_reference,
    current_chain,
    current_reference,
    scope,
    validated_at,
    policy_pin,
):
    checked_bootstrap(scope, LedgerScope)
    if (
        type(policy_pin) is not str
        or policy_pin not in {POLICY_SHA256, V7_POLICY_SHA256, V7_ORDERED_POLICY_SHA256}
        or type(validated_at) is not datetime
        or type(history_chain) is not tuple
        or type(current_chain) is not tuple
        or not history_chain
        or not current_chain
    ):
        _deny("account_join_inputs_invalid")
    observed.reference_document(history_reference)
    observed.reference_document(current_reference)
    if (
        observed.reference_document(observed.source_reference(history_chain))
        != observed.reference_document(history_reference)
        or observed.reference_document(observed.source_reference(current_chain))
        != observed.reference_document(current_reference)
        or history_reference.capture_id == current_reference.capture_id
    ):
        _deny("account_join_source_reference_mismatch")
    ordered_v7 = policy_pin == V7_ORDERED_POLICY_SHA256
    v7 = policy_pin in {V7_POLICY_SHA256, V7_ORDERED_POLICY_SHA256}
    current_policy = current.V7_POLICY_SHA256 if v7 else current.V6_POLICY_SHA256
    current_plan = (
        capture.CurrentDemoAccountCapturePlanV7
        if v7
        else capture.CurrentDemoAccountCapturePlanV6
    )
    history_proof = history.verify_history_query_chain(
        history_chain, **observed._pins(history_reference, scope)
    )
    current_proof = current.verify_current_account_sources(
        current_chain,
        reference=current_reference,
        scope=scope,
        validated_at=validated_at,
        expected_policy_sha256=current_policy,
    )
    history_packet = _packet(history_chain, history_reference)
    current_packet = _packet(current_chain, current_reference)
    if (
        type(history_packet.plan) is not capture.AllProductDemoAccountCapturePlan
        or type(current_packet.plan) is not current_plan
    ):
        _deny("account_join_plan_version_mismatch")
    old, new = history_packet.plan, current_packet.plan
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
        if getattr(old, name) != getattr(new, name):
            _deny("account_join_identity_or_session_changed")
    if (
        scope.environment != "demo"
        or scope.account_id != new.expected_uid
        or scope.settlement_currency != new.settlement_currency
    ):
        _deny("account_join_scope_mismatch")
    old_config = _config(history_packet, "config_after")
    new_config = _config(current_packet, "config_after")
    if any(
        old_config.get(name) != new_config.get(name)
        for name in ("uid", "mainUid", "acctLv", "posMode")
    ):
        _deny("account_join_account_mode_changed")
    old_checkpoint, new_checkpoint = (
        _checkpoint(history_chain),
        _checkpoint(current_chain),
    )
    if old_checkpoint != new_checkpoint:
        _deny("account_join_recorded_local_revision_changed")
    current_start = current_packet.observations[0].request_started_at
    if not old.history_end <= history_packet.completed_at < current_start:
        _deny("account_join_chronology_invalid")
    if ordered_v7:
        # Recorded timestamps alone cannot prove when the transaction
        # committed. Native V7 separately reads the committed history chain
        # before allowing the first HTTP request to start.
        terminal = history_chain[-1]
        if (
            journal.checked_event(terminal.event)["kind"] != "terminal"
            or not terminal.db_recorded_at < current_start
        ):
            _deny("account_join_history_terminal_db_timestamp_not_before_current")
    at = capture._utc(validated_at)
    if at < current_packet.completed_at:
        _deny("account_join_validation_precedes_source")
    history_data, current_data = (
        json.loads(history_proof.receipt_json),
        json.loads(current_proof.receipt_json),
    )
    tail_gap = current_start - old.history_end
    blockers = set(current_data["blocking_reasons"])
    if tail_gap > timedelta(seconds=120):
        blockers.add("history_requested_tail_gap_exceeds_policy")
    if any(
        item["requested_generation_window_covered"] is not True
        for item in history_data["coverage"]
    ):
        blockers.add("history_requested_generation_coverage_incomplete")
    blockers.update(
        {
            "history_tail_not_atomically_closed",
            "history_late_arrival_finality_unproven",
            "funding_accrual_provenance_missing",
            "complete_net_loss_window_unproven",
            "loss_streak_seed_unknown",
            "historical_native_hwm_unknown",
            "current_local_revision_readback_required",
            "current_native_owner_unverified",
        }
    )
    output = {
        "schema_version": (
            "ctcc.recorded_account_current_history_join.v3"
            if ordered_v7
            else "ctcc.recorded_account_current_history_join.v2"
            if v7
            else "ctcc.recorded_account_current_history_join.v1"
        ),
        "policy_sha256": policy_pin,
        "scope_sha256": journal.digest(
            journal.canonical(
                [scope.environment, scope.account_id, scope.settlement_currency]
            )
        ),
        "session_binding_sha256": current_reference.session_binding_sha256,
        "history_source_reference": observed.reference_document(history_reference),
        "current_source_reference": observed.reference_document(current_reference),
        "history_query_receipt_sha256": history_proof.receipt_sha256,
        "current_source_receipt_sha256": current_proof.receipt_sha256,
        "recorded_local_checkpoint_sha256": new_checkpoint,
        "recorded_local_checkpoint_equal": True,
        "account_revision_verified": False,
        "history_requested_end": old.history_end.isoformat(),
        "history_capture_completed_at": history_packet.completed_at.isoformat(),
        "current_capture_started_at": current_start.isoformat(),
        "current_capture_completed_at": current_packet.completed_at.isoformat(),
        "validated_at": at.isoformat(),
        "history_tail_gap_seconds": str(tail_gap.total_seconds()),
        "history_requested_generation_coverage": history_data["coverage"],
        "current_observed_flat": current_data["observed_flat"],
        "blocking_reasons": sorted(blockers),
        "snapshot": None,
        "account_complete": False,
        "account_revision_published": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    if v7:
        output["current_source_policy_sha256"] = current_policy
        output["flat_start_permission"] = False
    if ordered_v7:
        output["history_journal_terminal_db_recorded_at"] = capture._utc(
            history_chain[-1].db_recorded_at
        ).isoformat()
        output["history_original_db_chain_sha256"] = _recorded_db_chain_sha256(
            history_chain
        )
        output["history_terminal_db_timestamp_before_current_request"] = True
        output["history_commit_before_current_request"] = False
    return RecordedAccountSourceJoin(journal.canonical(output))
