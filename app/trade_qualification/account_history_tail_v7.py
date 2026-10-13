"""Versioned, offline V5-history/V7-current four-chain tail diagnostic.

The two current captures must each replay all eight Algo order-type chains.
Four finite observations still cannot establish exchange history finality,
funding accrual, an historical high-water mark or trading authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.trade_qualification import account_capture as capture
from app.trade_qualification import account_capture_journal as journal
from app.trade_qualification import account_current_history_join as source_join
from app.trade_qualification import account_current_source_verifier as current
from app.trade_qualification import account_history_query_verifier as history
from app.trade_qualification import account_history_tail_prerequisite as legacy_tail
from app.trade_qualification import account_observation_index as observed
from app.trade_qualification.reservations import LedgerScope, checked_bootstrap

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
SCHEMA_VERSION = "ctcc.demo_history_tail_followup_current.v2"
HISTORY_COMPARISON_SCHEMA_VERSION = "ctcc.observed_history_query_comparison.v2"
POLICY_BYTES = journal.canonical(
    {
        "version": SCHEMA_VERSION,
        "history_plan": "ctcc.demo_account_plan.v5",
        "current_plan": "ctcc.demo_current_account_plan.v7",
        "history_query_policy_sha256": history.POLICY_SHA256,
        "history_comparison_schema": HISTORY_COMPARISON_SCHEMA_VERSION,
        "ordered_join_policy_sha256": source_join.V7_ORDERED_POLICY_SHA256,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "source": "four_distinct_original_B1_raw_page_chains",
        "chronology": "history_current_overlapping_history_followup_current",
        "identity": "exact_uid_mainuid_session_region_origin_mode_currency_checkpoint",
        "eight_algo_empty_terminal_chains_per_current_capture": True,
        "history_commit_before_current_request": False,
        "history_tail_closed": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
POLICY_SHA256 = journal.digest(POLICY_BYTES)
TYPED_SCHEMA_VERSION = "ctcc.demo_history_tail_followup_current.v3"
TYPED_HISTORY_COMPARISON_SCHEMA_VERSION = "ctcc.observed_history_query_comparison.v3"
TYPED_POLICY_BYTES = journal.canonical(
    {
        "version": TYPED_SCHEMA_VERSION,
        "prior_tail_policy_sha256": POLICY_SHA256,
        "history_query_policy_sha256": history.V2_POLICY_SHA256,
        "history_comparison_policy_sha256": history.V2_COMPARISON_POLICY_SHA256,
        "history_comparison_schema": TYPED_HISTORY_COMPARISON_SCHEMA_VERSION,
        "ordered_join_policy_sha256": source_join.V7_ORDERED_TYPED_POLICY_SHA256,
        "current_source_policy_sha256": current.V7_POLICY_SHA256,
        "source": "four_distinct_original_B1_raw_page_chains",
        "chronology": "history_current_overlapping_history_followup_current",
        "identity": "exact_uid_mainuid_session_region_origin_mode_currency_checkpoint",
        "eight_algo_empty_terminal_chains_per_current_capture": True,
        "history_commit_before_current_request": False,
        "history_tail_closed": False,
        "account_complete": False,
        "execution_authority": False,
    }
)
TYPED_POLICY_SHA256 = journal.digest(TYPED_POLICY_BYTES)
_HASH_KEYS = (
    "scope_sha256",
    "history_to_current_join_receipt_sha256",
    "continuation_to_followup_join_receipt_sha256",
    "previous_history_query_receipt_sha256",
    "continuation_history_query_receipt_sha256",
    "history_comparison_sha256",
    "recorded_local_checkpoint_sha256",
)
_TIME_KEYS = (
    "previous_history_requested_end",
    "current_capture_completed_at",
    "continuation_history_requested_start",
    "continuation_history_requested_end",
    "continuation_capture_started_at",
    "continuation_capture_completed_at",
    "followup_current_capture_started_at",
    "followup_current_capture_completed_at",
    "validated_at",
)
_FALSE_KEYS = (
    "native_pre_http_history_commit_verified",
    "history_tail_closed",
    "exchange_global_eof_verified",
    "funding_accrual_provenance_verified",
    "historical_native_hwm_verified",
    "future_late_arrival_finality_verified",
    "account_complete",
    "source_authenticity_verified",
    "flat_start_permission",
    "execution_authority",
)
_REQUIRED_BLOCKERS = frozenset(
    {
        "history_tail_not_atomically_closed",
        "exchange_history_finality_unproven",
        "historical_native_source_unverified",
        "native_pre_http_history_commit_proof_missing",
        "funding_accrual_provenance_missing",
        "historical_native_hwm_unknown",
        "current_local_revision_readback_required",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "policy_sha256",
        "source_references",
        "history_comparison_findings",
        "uncompared_query_domains",
        "blocking_reasons",
        "eight_algo_empty_terminal_chains_replayed",
        "history_terminal_db_timestamps_before_current_requests",
        "observed_current_flat",
        "observed_followup_flat",
        "snapshot",
        "admission",
        *_HASH_KEYS,
        *_TIME_KEYS,
        *_FALSE_KEYS,
    }
)
_TYPED_RECEIPT_KEYS = _RECEIPT_KEYS | frozenset(
    {
        "history_comparison_policy_sha256",
        "history_query_policy_sha256",
        "source_time_domains",
        "uncompared_source_ts_intervals",
    }
)


class V7HistoryTailError(ValueError):
    """Only fixed, private-data-safe denial codes."""


def _deny(code):
    raise V7HistoryTailError(code)


def _sha(value):
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _utc_time(value):
    if type(value) is not str:
        _deny("v7_history_tail_receipt_invalid")
    stamp = capture._utc(datetime.fromisoformat(value))
    if stamp.isoformat() != value:
        _deny("v7_history_tail_receipt_invalid")
    return stamp


def _validate_receipt(receipt_json, *, typed):
    if (
        type(receipt_json) is not bytes
        or not 1 <= len(receipt_json) <= MAX_RECEIPT_BYTES
    ):
        _deny("v7_history_tail_receipt_invalid")
    try:
        value = json.loads(receipt_json)
        if (
            type(value) is not dict
            or journal.canonical(value) != receipt_json
            or set(value) != (_TYPED_RECEIPT_KEYS if typed else _RECEIPT_KEYS)
            or value["schema_version"]
            != (TYPED_SCHEMA_VERSION if typed else SCHEMA_VERSION)
            or value["policy_sha256"]
            != (TYPED_POLICY_SHA256 if typed else POLICY_SHA256)
            or any(not _sha(value[key]) for key in _HASH_KEYS)
            or any(value[key] is not False for key in _FALSE_KEYS)
            or value["eight_algo_empty_terminal_chains_replayed"] is not True
            or value["history_terminal_db_timestamps_before_current_requests"]
            is not True
            or type(value["observed_current_flat"]) is not bool
            or type(value["observed_followup_flat"]) is not bool
            or value["snapshot"] is not None
            or value["admission"] != "DENY"
            or type(value["history_comparison_findings"]) is not list
            or type(value["uncompared_query_domains"]) is not list
            or type(value["blocking_reasons"]) is not list
            or any(type(item) is not str for item in value["blocking_reasons"])
            or sorted(set(value["blocking_reasons"])) != value["blocking_reasons"]
            or not _REQUIRED_BLOCKERS.issubset(value["blocking_reasons"])
        ):
            _deny("v7_history_tail_receipt_invalid")
        if typed and (
            value["history_comparison_policy_sha256"]
            != history.V2_COMPARISON_POLICY_SHA256
            or value["history_query_policy_sha256"] != history.V2_POLICY_SHA256
            or value["source_time_domains"] != history._SOURCE_TS_DOMAINS
            or type(value["uncompared_source_ts_intervals"]) is not list
        ):
            _deny("v7_history_tail_receipt_invalid")
        refs = value["source_references"]
        if (
            type(refs) is not list
            or len(refs) != 4
            or any(
                type(ref) is not dict
                or observed.reference_document(observed.CaptureReference(**ref)) != ref
                for ref in refs
            )
            or len({ref["capture_id"] for ref in refs}) != 4
        ):
            _deny("v7_history_tail_receipt_invalid")
        times = [_utc_time(value[key]) for key in _TIME_KEYS]
        (
            old_end,
            current_end,
            next_start,
            next_end,
            next_capture_start,
            next_capture_end,
            followup_start,
            followup_end,
            validated,
        ) = times
        if not (
            next_start
            <= old_end
            <= current_end
            < next_capture_start
            <= next_capture_end
            < followup_start
            <= followup_end
            <= validated
            and next_end >= current_end
            and next_end >= old_end
        ):
            _deny("v7_history_tail_receipt_invalid")
    except V7HistoryTailError:
        raise
    except Exception:  # noqa: BLE001 -- saved bytes never grant authority
        _deny("v7_history_tail_receipt_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class V7HistoryTailDiagnostic:
    receipt_json: bytes

    def __post_init__(self):
        _validate_receipt(self.receipt_json, typed=False)

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


@dataclass(frozen=True, slots=True, repr=False)
class TypedV7HistoryTailDiagnostic:
    receipt_json: bytes

    def __post_init__(self):
        _validate_receipt(self.receipt_json, typed=True)

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


def verify_v7_history_tail_diagnostic(
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
    """Replay the four pinned originals and return an authority-free receipt."""
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
            typed=False,
        )
    except V7HistoryTailError:
        raise
    except Exception:  # noqa: BLE001 -- redact private source/parser errors
        raise V7HistoryTailError("v7_history_tail_source_invalid") from None


def verify_v7_history_tail_diagnostic_v2(
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
    expected_policy_sha256=TYPED_POLICY_SHA256,
):
    """New typed-source-time four-chain diagnostic; never execution authority."""
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
            typed=True,
        )
    except V7HistoryTailError:
        raise
    except Exception:  # noqa: BLE001 -- redact private source/parser errors
        raise V7HistoryTailError("v7_history_tail_source_invalid") from None


def _verify(
    old_chain,
    old_ref,
    current_chain,
    current_ref,
    next_chain,
    next_ref,
    followup_chain,
    followup_ref,
    scope,
    validated_at,
    policy_pin,
    *,
    typed,
):
    checked_bootstrap(scope, LedgerScope)
    chains = (old_chain, current_chain, next_chain, followup_chain)
    refs = (old_ref, current_ref, next_ref, followup_ref)
    if (
        type(policy_pin) is not str
        or policy_pin != (TYPED_POLICY_SHA256 if typed else POLICY_SHA256)
        or type(validated_at) is not datetime
        or any(
            type(chain) is not tuple
            or not 1 <= len(chain) <= journal.MAX_EVENTS
            or any(type(item) is not journal.JournalReadback for item in chain)
            for chain in chains
        )
        or sum(
            len(item.event.event_json)
            + len(item.event.raw_body or b"")
            + len(item.event.packet_payload or b"")
            for chain in chains
            for item in chain
        )
        > MAX_SOURCE_BYTES
    ):
        _deny("v7_history_tail_inputs_invalid")
    references = [
        legacy_tail._exact_reference(chain, ref)
        for chain, ref in zip(chains, refs, strict=True)
    ]
    if len({ref["capture_id"] for ref in references}) != 4:
        _deny("v7_history_tail_distinct_captures_required")

    join_sources = (
        source_join.join_recorded_account_sources_v2
        if typed
        else source_join.join_recorded_account_sources
    )
    join_policy = (
        source_join.V7_ORDERED_TYPED_POLICY_SHA256
        if typed
        else source_join.V7_ORDERED_POLICY_SHA256
    )
    joined = [
        join_sources(
            history_chain=history_source,
            history_reference=history_pin,
            current_chain=current_source,
            current_reference=current_pin,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=join_policy,
        )
        for history_source, history_pin, current_source, current_pin in (
            (old_chain, old_ref, current_chain, current_ref),
            (next_chain, next_ref, followup_chain, followup_ref),
        )
    ]
    current_proofs = [
        current.verify_current_account_sources(
            chain,
            reference=ref,
            scope=scope,
            validated_at=validated_at,
            expected_policy_sha256=current.V7_POLICY_SHA256,
        )
        for chain, ref in ((current_chain, current_ref), (followup_chain, followup_ref))
    ]
    join_values = [json.loads(item.receipt_json) for item in joined]
    current_values = [json.loads(item.receipt_json) for item in current_proofs]
    if any(
        join_value["current_source_receipt_sha256"] != proof.receipt_sha256
        or current_value["algo_type_coverage"]["all_eight_terminal_chains_replayed"]
        is not True
        for join_value, proof, current_value in zip(
            join_values, current_proofs, current_values, strict=True
        )
    ):
        _deny("v7_history_tail_algo_terminal_proof_missing")

    packets = [
        legacy_tail._packet(chain, ref) for chain, ref in zip(chains, refs, strict=True)
    ]
    if [type(packet.plan) for packet in packets] != [
        capture.AllProductDemoAccountCapturePlan,
        capture.CurrentDemoAccountCapturePlanV7,
        capture.AllProductDemoAccountCapturePlan,
        capture.CurrentDemoAccountCapturePlanV7,
    ]:
        _deny("v7_history_tail_plan_version_mismatch")
    plans = [packet.plan for packet in packets]
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
        if len({getattr(plan, name) for plan in plans}) != 1:
            _deny("v7_history_tail_identity_or_region_changed")
    if plans[0].registration_region != "global":
        _deny("v7_history_tail_scope_changed")
    checkpoints = [source_join._checkpoint(chain) for chain in chains]
    configs = [source_join._config(packet, "config_after") for packet in packets]
    if len(set(checkpoints)) != 1 or any(config != configs[0] for config in configs):
        _deny("v7_history_tail_account_or_checkpoint_changed")
    at = capture._utc(validated_at)
    old_end = packets[0].plan.history_end
    current_end = packets[1].completed_at
    next_start = packets[2].observations[0].request_started_at
    next_end = packets[2].plan.history_end
    followup_start = packets[3].observations[0].request_started_at
    if not (
        current_end < next_start <= packets[2].completed_at < followup_start
        and packets[3].completed_at <= at
        and packets[2].plan.history_start <= old_end
        and next_end >= current_end
        and next_end >= old_end
    ):
        _deny("v7_history_tail_chronology_or_overlap_invalid")

    verify_history = (
        history.verify_history_query_chain_v2
        if typed
        else history.verify_history_query_chain
    )
    old_proof = verify_history(old_chain, **observed._pins(old_ref, scope))
    next_proof = verify_history(next_chain, **observed._pins(next_ref, scope))
    compare_history = (
        history.compare_history_query_chains_v2
        if typed
        else history.compare_history_query_chains
    )
    comparison_bytes = compare_history(
        previous_chain=old_chain,
        previous_pins=observed._pins(old_ref, scope),
        current_chain=next_chain,
        current_pins=observed._pins(next_ref, scope),
    )
    comparison = json.loads(comparison_bytes)
    if comparison.get("schema_version") != (
        TYPED_HISTORY_COMPARISON_SCHEMA_VERSION
        if typed
        else HISTORY_COMPARISON_SCHEMA_VERSION
    ):
        _deny("v7_history_tail_comparison_schema_invalid")
    if typed and (
        comparison.get("policy_sha256") != history.V2_COMPARISON_POLICY_SHA256
        or comparison.get("source_query_policy_sha256") != history.V2_POLICY_SHA256
        or comparison.get("previous_query_receipt_sha256") != old_proof.receipt_sha256
        or comparison.get("current_query_receipt_sha256") != next_proof.receipt_sha256
        or comparison.get("source_time_domains") != history._SOURCE_TS_DOMAINS
        or any(
            value.get("schema_version")
            != "ctcc.recorded_account_current_history_join.v4"
            or value.get("policy_sha256") != join_policy
            or value.get("history_query_policy_sha256") != history.V2_POLICY_SHA256
            or value.get("history_query_receipt_sha256") != proof.receipt_sha256
            for value, proof in zip(join_values, (old_proof, next_proof), strict=True)
        )
    ):
        _deny("v7_history_tail_typed_source_mismatch")
    blockers = set(join_values[0]["blocking_reasons"])
    blockers.update(join_values[1]["blocking_reasons"])
    coverage_key = (
        "requested_source_ts_window_covered"
        if typed
        else "requested_generation_window_covered"
    )
    if any(
        row[coverage_key] is not True
        for proof in (old_proof, next_proof)
        for row in json.loads(proof.receipt_json)["coverage"]
    ):
        blockers.add(
            "history_requested_source_ts_coverage_incomplete"
            if typed
            else "history_requested_generation_coverage_incomplete"
        )
    if comparison["dependent_current_claims_require_reconciliation"]:
        blockers.add("history_overlap_discovery_requires_reconciliation")
    blockers.update(
        {
            "exchange_history_finality_unproven",
            "historical_native_source_unverified",
            "native_pre_http_history_commit_proof_missing",
            "post_continuation_current_native_proof_required",
            "post_continuation_locked_local_revision_readback_required",
        }
    )
    value = {
        "schema_version": TYPED_SCHEMA_VERSION if typed else SCHEMA_VERSION,
        "policy_sha256": TYPED_POLICY_SHA256 if typed else POLICY_SHA256,
        "scope_sha256": join_values[0]["scope_sha256"],
        "source_references": references,
        "history_to_current_join_receipt_sha256": joined[0].receipt_sha256,
        "continuation_to_followup_join_receipt_sha256": joined[1].receipt_sha256,
        "previous_history_query_receipt_sha256": old_proof.receipt_sha256,
        "continuation_history_query_receipt_sha256": next_proof.receipt_sha256,
        "history_comparison_sha256": journal.digest(comparison_bytes),
        "recorded_local_checkpoint_sha256": checkpoints[0],
        "history_comparison_findings": comparison["findings"],
        "uncompared_query_domains": comparison["uncompared_query_domains"],
        "blocking_reasons": sorted(blockers),
        "eight_algo_empty_terminal_chains_replayed": True,
        "history_terminal_db_timestamps_before_current_requests": True,
        "previous_history_requested_end": old_end.isoformat(),
        "current_capture_completed_at": current_end.isoformat(),
        "continuation_history_requested_start": packets[
            2
        ].plan.history_start.isoformat(),
        "continuation_history_requested_end": next_end.isoformat(),
        "continuation_capture_started_at": next_start.isoformat(),
        "continuation_capture_completed_at": packets[2].completed_at.isoformat(),
        "followup_current_capture_started_at": followup_start.isoformat(),
        "followup_current_capture_completed_at": packets[3].completed_at.isoformat(),
        "validated_at": at.isoformat(),
        "observed_current_flat": current_values[0]["observed_flat"],
        "observed_followup_flat": current_values[1]["observed_flat"],
        "native_pre_http_history_commit_verified": False,
        "history_tail_closed": False,
        "exchange_global_eof_verified": False,
        "funding_accrual_provenance_verified": False,
        "historical_native_hwm_verified": False,
        "future_late_arrival_finality_verified": False,
        "snapshot": None,
        "account_complete": False,
        "source_authenticity_verified": False,
        "flat_start_permission": False,
        "execution_authority": False,
        "admission": "DENY",
    }
    if typed:
        value.update(
            {
                "history_comparison_policy_sha256": history.V2_COMPARISON_POLICY_SHA256,
                "history_query_policy_sha256": history.V2_POLICY_SHA256,
                "source_time_domains": history._SOURCE_TS_DOMAINS,
                "uncompared_source_ts_intervals": comparison[
                    "uncompared_source_ts_intervals"
                ],
            }
        )
    receipt = journal.canonical(value)
    return (
        TypedV7HistoryTailDiagnostic(receipt)
        if typed
        else V7HistoryTailDiagnostic(receipt)
    )


def verify_v7_history_tail_receipt(
    receipt: V7HistoryTailDiagnostic, **original_inputs
) -> V7HistoryTailDiagnostic:
    """A saved diagnostic is valid only after exact four-chain replay."""
    if type(receipt) is not V7HistoryTailDiagnostic:
        _deny("v7_history_tail_exact_receipt_required")
    checked = V7HistoryTailDiagnostic(receipt.receipt_json)
    replayed = verify_v7_history_tail_diagnostic(**original_inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("v7_history_tail_receipt_changed")
    return replayed


def verify_v7_history_tail_receipt_v2(
    receipt: TypedV7HistoryTailDiagnostic, **original_inputs
) -> TypedV7HistoryTailDiagnostic:
    """Read back the typed receipt against the same four original B1 chains."""
    if type(receipt) is not TypedV7HistoryTailDiagnostic:
        _deny("v7_history_tail_exact_typed_receipt_required")
    checked = TypedV7HistoryTailDiagnostic(receipt.receipt_json)
    replayed = verify_v7_history_tail_diagnostic_v2(**original_inputs)
    if checked.receipt_json != replayed.receipt_json:
        _deny("v7_history_tail_typed_receipt_changed")
    return replayed
