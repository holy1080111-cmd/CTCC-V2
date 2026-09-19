"""Versioned cost decomposition replay; authentic trade collection is upstream.

Preserves v1 results/hashes. Actual-fill PnL already includes price friction;
spread/slippage are decomposed once against source-bound pre-fill quote claims,
never subtracted a second time. Missing/stale references leave those metrics
unknown. This module performs no IO and grants no source or execution authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.trade_evidence import forensics as core
from app.trade_evidence.post_submit import _exact

MAX_BYTES = 4 * 1024 * 1024
VERSION = "ctcc.execution_forensics.v1"


class ExecutionReference(core._BoundEvidence):
    """Explicit quote/source claim; a hash does not authenticate its source."""

    fill_id: core.Identifier
    request_started_at: datetime
    source_at: datetime
    received_at: datetime
    bid: core.Positive
    ask: core.Positive

    _times = field_validator("request_started_at", "source_at", "received_at")(
        core._utc
    )

    @model_validator(mode="after")
    def causal_quote(self):
        if not (
            self.request_started_at
            <= self.source_at
            <= self.received_at
            <= self.recorded_at
            and self.bid <= self.ask
        ):
            raise core.ForensicsError("execution_reference_causality_or_book_invalid")
        return self


class ExecutionForensicsPolicy(core._Model):
    schema_version: Literal["ctcc.execution_forensics_policy.v1"] = (
        "ctcc.execution_forensics_policy.v1"
    )
    policy_id: core.Identifier
    maximum_reference_age_ms: int = Field(gt=0, le=60000)


@dataclass(frozen=True, slots=True)
class ExecutionForensicsReplay:
    canonical_payload: bytes = field(repr=False)
    sha256: str
    metrics: tuple[tuple[str, core.ExactMetric], ...]
    source_authenticity_verified: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)


def _copy(value, kind):
    raw = _exact(value, kind)
    for name, part in raw.items():
        if kind is ExecutionForensicsPolicy and name == "maximum_reference_age_ms":
            if type(part) is not int or not 0 < part <= 60000:
                raise core.ForensicsError("execution_reference_age_invalid")
        else:
            core._guard(part)
    return kind.model_validate(raw, strict=True)


def _wire(value):
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode()
    if len(raw) > MAX_BYTES:
        raise core.ForensicsError("execution_forensics_bytes_bound")
    return raw


def _fraction(metric):
    return (
        None
        if metric.value is None
        else Fraction(int(metric.numerator), int(metric.denominator))
    )


def _micros(delta):
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def analyze_execution_forensics(
    result: core.TradeForensicsResult,
    *,
    expected_candidate_sha256: str,
    policy: ExecutionForensicsPolicy,
    references: tuple[ExecutionReference, ...] = (),
) -> ExecutionForensicsReplay:
    """Replay v1 and add exact metrics, retaining all unknowns and source pins."""
    result = core.validate_forensics(
        result, expected_candidate_sha256=expected_candidate_sha256
    )
    base_bytes = core.freeze_forensics(
        result, expected_candidate_sha256=expected_candidate_sha256
    )
    policy = _copy(policy, ExecutionForensicsPolicy)
    if type(references) is not tuple or len(references) > 4096:
        raise core.ForensicsError("bounded_execution_references_required")
    references = tuple(_copy(ref, ExecutionReference) for ref in references)
    packet = result.inputs
    candidate = packet.candidate
    currency = candidate.settlement_currency
    fills = {fill.evidence_id: fill for fill in packet.fills}
    by_fill = {}
    evidence_ids = {
        item.evidence_id
        for group in (
            packet.fills,
            packet.cashflows,
            packet.path,
            packet.coverage,
            packet.facts,
        )
        for item in group
    }
    for reference in references:
        if (
            reference.fill_id not in fills
            or reference.fill_id in by_fill
            or reference.evidence_id in evidence_ids
        ):
            raise core.ForensicsError("duplicate_or_unbound_execution_reference")
        if (
            any(
                getattr(reference, name) != getattr(candidate, name)
                for name in ("report_id", "instrument_id", "account_id", "environment")
            )
            or reference.candidate_sha256 != expected_candidate_sha256
        ):
            raise core.ForensicsError("execution_reference_scope_mismatch")
        if (
            not candidate.recorded_at <= reference.request_started_at
            or reference.recorded_at > packet.observed_at
        ):
            raise core.ForensicsError("execution_reference_observation_window_invalid")
        by_fill[reference.fill_id] = reference
        evidence_ids.add(reference.evidence_id)

    metrics = {}
    fill_known = result.entry_contracts.value is not None
    entries = tuple(fill for fill in packet.fills if fill.role == "entry")
    exits = tuple(fill for fill in packet.fills if fill.role == "exit")
    value = Fraction(candidate.contract_value_base)
    sign = 1 if candidate.direction == "long" else -1
    risk = _fraction(result.original_risk)
    pnl = _fraction(result.pnl)
    metrics["net_r_multiple"] = core._metric(
        None if pnl is None else pnl / risk, "original_R", "closed_net_pnl_unknown"
    )
    duration = None
    if result.position_status == "flat" and entries and exits:
        duration = Fraction(
            _micros(
                max(f.occurred_at for f in exits) - min(f.occurred_at for f in entries)
            ),
            1000000,
        )
    metrics["holding_duration"] = core._metric(
        duration, "seconds", "closed_holding_interval_unknown"
    )
    fees_known = result.fees.value is not None
    fees = tuple(flow for flow in packet.cashflows if flow.kind == "fee")
    metrics["fees_paid"] = core._metric(
        sum((-Fraction(flow.amount) for flow in fees if flow.amount < 0), Fraction(0))
        if fees_known
        else None,
        currency,
        "fee_coverage_or_currency_unknown",
    )
    metrics["rebates_received"] = core._metric(
        sum((Fraction(flow.amount) for flow in fees if flow.amount > 0), Fraction(0))
        if fees_known
        else None,
        currency,
        "fee_coverage_or_currency_unknown",
    )
    metrics["entry_plan_deviation_cash"] = core._metric(
        sum(
            (
                sign
                * (Fraction(f.price) - Fraction(candidate.entry))
                * Fraction(f.contracts)
                * value
                for f in entries
            ),
            Fraction(0),
        )
        if fill_known and entries
        else None,
        currency + "_adverse_positive",
        "entry_fill_coverage_unknown_or_unfilled",
    )
    metrics["exit_reference_deviation_cash"] = core._metric(
        sum(
            (
                -sign
                * (Fraction(f.price) - Fraction(f.reference_price))
                * Fraction(f.contracts)
                * value
                for f in exits
            ),
            Fraction(0),
        )
        if fill_known and exits and all(f.reference_price is not None for f in exits)
        else None,
        currency + "_adverse_positive",
        "exit_fill_or_reference_unknown",
    )
    metrics["structural_stop_distance"] = core._metric(
        abs(Fraction(candidate.entry) - Fraction(candidate.stop_loss)),
        currency + "_per_base",
    )
    metrics["structural_target_distance"] = core._metric(
        abs(Fraction(candidate.take_profit) - Fraction(candidate.entry)),
        currency + "_per_base",
    )

    friction_values = {}
    for role, group in (("entry", entries), ("exit", exits)):
        reason = None
        if not fill_known:
            reason = "fill_coverage_incomplete"
        elif not group:
            reason = "no_" + role + "_fills"
        elif any(f.evidence_id not in by_fill for f in group):
            reason = "execution_reference_missing"
        else:
            for fill in group:
                reference = by_fill[fill.evidence_id]
                age = _micros(fill.occurred_at - reference.source_at)
                if reference.received_at > fill.occurred_at:
                    reason = "execution_reference_received_after_fill"
                    break
                if age > policy.maximum_reference_age_ms * 1000:
                    reason = "execution_reference_stale"
                    break
        spread = residual = total = None
        if reason is None:
            spread = residual = total = Fraction(0)
            for fill in group:
                reference = by_fill[fill.evidence_id]
                mid = (Fraction(reference.bid) + Fraction(reference.ask)) / 2
                touch = Fraction(reference.ask if fill.side == "buy" else reference.bid)
                side = 1 if fill.side == "buy" else -1
                quantity = Fraction(fill.contracts) * value
                spread += side * (touch - mid) * quantity
                residual += side * (Fraction(fill.price) - touch) * quantity
                total += side * (Fraction(fill.price) - mid) * quantity
            if total != spread + residual:
                raise core.ForensicsError("execution_cost_identity_mismatch")
        for name, amount in (
            ("spread_cost", spread),
            ("residual_slippage", residual),
            ("total_price_friction", total),
        ):
            metrics[role + "_" + name] = core._metric(
                amount, currency + "_adverse_positive", reason
            )
        friction_values[role] = total

    reference_gross = reference_net = None
    if result.position_status == "flat" and all(
        v is not None for v in friction_values.values()
    ):
        total = sum(friction_values.values(), Fraction(0))
        reference_gross = _fraction(result.gross_realized_pnl) + total
        if pnl is not None:
            reference_net = (
                reference_gross
                - total
                + _fraction(result.fees)
                + _fraction(result.funding)
            )
            if reference_net != pnl:
                raise core.ForensicsError("execution_pnl_double_count_or_mismatch")
    metrics["quote_reference_gross_pnl"] = core._metric(
        reference_gross, currency, "closed_quote_reference_pnl_unknown"
    )
    metrics["net_pnl_from_reference_decomposition"] = core._metric(
        reference_net, currency, "closed_pnl_or_cost_decomposition_unknown"
    )
    body = {
        "schema_version": VERSION,
        "base_payload_sha256": hashlib.sha256(base_bytes).hexdigest(),
        "base_result_sha256": result.result_sha256,
        "candidate_sha256": expected_candidate_sha256,
        "policy": policy.model_dump(mode="json"),
        "references": [ref.model_dump(mode="json") for ref in references],
        "metrics": {
            key: metric.model_dump(mode="json") for key, metric in metrics.items()
        },
        "cost_identity": "quote_reference_gross-minus-spread-minus-residual_slippage-plus-signed_fees-plus-signed_funding",
        "actual_fill_pnl_already_contains_price_friction": True,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }
    raw = _wire(body)
    return ExecutionForensicsReplay(
        raw, hashlib.sha256(raw).hexdigest(), tuple(metrics.items())
    )


def verify_execution_forensics(
    payload: bytes,
    *,
    expected_sha256: str,
    base_payload: bytes,
    expected_base_sha256: str,
    expected_candidate_sha256: str,
) -> ExecutionForensicsReplay:
    """Read back exact bytes and recompute; forged metrics/claims cannot survive."""
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        raise core.ForensicsError("bounded_execution_forensics_bytes_required")
    if hashlib.sha256(payload).hexdigest() != core._pin(expected_sha256):
        raise core.ForensicsError("execution_forensics_hash_mismatch")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise core.ForensicsError("duplicate_execution_forensics_key")
            result[key] = value
        return result

    try:
        body = json.loads(payload, object_pairs_hook=unique)
        if (
            _wire(body) != payload
            or type(body) is not dict
            or body["schema_version"] != VERSION
        ):
            raise core.ForensicsError("execution_forensics_noncanonical_or_version")
        if type(body["references"]) is not list or len(body["references"]) > 4096:
            raise core.ForensicsError("bounded_execution_references_required")
        policy = ExecutionForensicsPolicy.model_validate_json(
            _wire(body["policy"]), strict=True
        )
        references = tuple(
            ExecutionReference.model_validate_json(_wire(ref), strict=True)
            for ref in body["references"]
        )
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise core.ForensicsError("invalid_execution_forensics_json") from None
    base = core.verify_forensics(
        base_payload,
        expected_base_sha256,
        expected_candidate_sha256=expected_candidate_sha256,
    )
    replayed = analyze_execution_forensics(
        base,
        expected_candidate_sha256=expected_candidate_sha256,
        policy=policy,
        references=references,
    )
    if replayed.canonical_payload != payload:
        raise core.ForensicsError("execution_forensics_replay_mismatch")
    return replayed
