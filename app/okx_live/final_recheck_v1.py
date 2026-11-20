"""Additional final-clock fixed-candidate math, never replacement evidence.

The owned coordinator authenticates its source and committed intent. This
module only computes DATA; it cannot move an Arm, reserve risk or submit.
"""

from datetime import timedelta
from decimal import Decimal

from app.domain.source_primitives import decode
from app.okx_live import qualification_math_v1 as math
from app.trade_qualification import native_live_public_source_v1 as public


def _need(value):
    if value is not True:
        raise ValueError("native_live_final_current_recheck_refused") from None


def next_cash_window_change_v1(facts, *, evaluated_at):
    """Bound synchronous final math by the next known UTC/window boundary."""
    midnight = evaluated_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    # Cash inclusion is closed on the lower bound. One microsecond is the
    # exact datetime precision, rather than an enlarged freshness tolerance.
    changes = tuple(
        item.at + timedelta(days=7, microseconds=1)
        for item in facts.cashflows
        if item.at + timedelta(days=7, microseconds=1) > evaluated_at
    )
    return min(midnight, *changes) if changes else midnight


def same_live_public_bank_v1(original, current):
    """Observation times stay original; evaluated_at is a NEW clock sample."""
    _need(type(original) is type(current) is public.LivePublicEvaluationV1)
    _need(current.evaluated_at >= original.evaluated_at)
    return (
        current.invocation is original.invocation
        and all(
            getattr(current, name) == getattr(original, name)
            for name in (
                "report_id",
                "instrument_id",
                "stage",
                "candidate_sha256",
                "publication_receipt_sha256",
                "publication_barrier",
                "quote",
                "reference",
                "raw_files",
                "receipt_json",
                "plan_sha256",
                "journal_sha256",
                "raw_inventory_sha256",
                "expires_at",
                "environment",
                "source_environment",
            )
        )
        and current.packet.packet_json == original.packet.packet_json
        and current.packet.bundle_sha256 == original.packet.bundle_sha256
    )


def final_current_recheck_data_v1(
    *,
    initial,
    committed,
    current_source,
    risk_inputs,
    consumed_event_keys,
    publication_candidate_sha256,
    committed_risk,
    committed_margin,
    committed_notional,
    deadline,
):
    """Leave both earlier receipts immutable; compute an EXTRA current check."""
    _need(
        type(initial) is type(committed) is math.LiveQualificationMathV1
        and initial.pre_evidence_complete
        and committed.pre_evidence_complete
        and same_live_public_bank_v1(committed.source, current_source)
        and current_source.evaluated_at < deadline <= committed.intent.expires_at
        and all(
            type(v) is Decimal and v.is_finite() and v > 0
            for v in (committed_risk, committed_margin, committed_notional)
        )
    )
    checked = math.recheck_live_qualification_math_v1(
        current_source,
        original=initial,
        consumed_event_keys=consumed_event_keys,
        publication_candidate_sha256=publication_candidate_sha256,
        risk_inputs=risk_inputs,
    )
    _need(
        checked.pre_evidence_complete
        and checked.candidate_geometry_sha256 == initial.candidate_geometry_sha256
        and checked.policy == committed.policy
        and checked.risk_inputs.requested_contracts
        == committed.risk_inputs.requested_contracts
        and checked.risk_inputs.requested_leverage
        == committed.risk_inputs.requested_leverage
        and (
            checked.intent.candidate_entry,
            checked.result.stop_loss,
            checked.result.take_profit,
        )
        == (
            committed.intent.candidate_entry,
            committed.result.stop_loss,
            committed.result.take_profit,
        )
    )
    operands = decode(checked.risk_operands_json, maximum=16384)
    samples = tuple(
        math._sample_risk(
            risk_inputs,
            intent=checked.intent,
            policy=checked.policy,
            entry=Decimal(operands[name]),
            stop=checked.result.stop_loss,
            cost=Decimal(operands["round_trip_cost_per_base"]),
            now=current_source.evaluated_at,
        )
        for name in ("candidate_entry", "executable_reference")
    )
    _need(
        all(
            s.passed
            and s.max_loss_amount <= committed_risk
            and s.required_margin <= committed_margin
            and s.notional <= committed_notional
            for s in samples
        )
    )
    return checked
