"""Opt-in failed-G2 HighVol diagnostic over this invocation's G1 source.

This wrapper returns the unchanged G1--G7 prefix. Its sidecar is a replayable
observation, never a replacement G2, candidate, G5 event or execution permit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from app.domain.market import MarketSnapshot
from app.strategies.regime import RouteDecision
from app.trade_qualification.high_vol_momentum_observation import (
    HighVolMomentumObservation,
    HighVolOriginalSourcePin,
    observe_high_vol_momentum,
)
from app.trade_qualification.models import (
    MarketRegime,
    QualificationGate,
    QualificationModel,
)
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    QualificationPrefixRun,
    evaluate_qualification_prefix,
)

SCHEMA = "ctcc.high_vol_failed_g2_sidecar.v1"


def _digest(value) -> str:
    if isinstance(value, QualificationModel):
        value = value.model_dump(mode="json", round_trip=True)
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _rejected_high_vol(prefix: QualificationPrefixRun) -> bool:
    gates = prefix.result.gates
    if not prefix.data_result.passed or len(gates) != 2:
        return False
    g2 = gates[1]
    return (
        g2.gate == QualificationGate.REGIME
        and not g2.passed
        and g2.code == "regime_strategy_not_allowed"
        and g2.measured_values.get("regime") == MarketRegime.HIGH_VOLATILITY.value
        and g2.measured_values.get("route_decision") == RouteDecision.NO_TRADE.value
        and g2.measured_values.get("allowed_strategies") == "none"
        and prefix.result.market_regime == MarketRegime.HIGH_VOLATILITY
        and prefix.result.raw_score == prefix.result.effective_score == 0
        and prefix.detection is prefix.timing is prefix.location is None
    )


class HighVolFailedG2Sidecar(QualificationModel):
    schema_version: Literal["ctcc.high_vol_failed_g2_sidecar.v1"] = SCHEMA
    prefix_evaluation_sha256: str
    g1_source_sha256: str
    g2_gate_sha256: str
    original_pin: HighVolOriginalSourcePin
    observation: HighVolMomentumObservation
    admission: Literal["DENY"] = "DENY"
    source_authenticity_verified: Literal[False] = False
    qualification_performed: Literal[False] = False
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def lineage(self):
        original, observed = self.original_pin, self.observation
        if (
            observed.report_id != original.report_id
            or observed.instrument_id != original.instrument_id
            or observed.strategy != original.strategy
            or observed.direction != original.direction
            or observed.original_source_sha256 != original.source_sha256
            or observed.original_event_key != original.event_key
            or observed.original_timing_policy_sha256 != original.timing_policy_sha256
            or observed.original_trigger_expires_at
            != original.original_trigger_expires_at
            or observed.original_candidate_expires_at
            != original.original_candidate_expires_at
            or observed.effective_expires_at
            > min(
                original.original_trigger_expires_at,
                original.original_candidate_expires_at,
            )
            or observed.recomputed_source_sha256 not in (None, self.g1_source_sha256)
        ):
            raise ValueError("failed-G2 sidecar source/event/expiry lineage mismatch")
        for value in (
            self.prefix_evaluation_sha256,
            self.g1_source_sha256,
            self.g2_gate_sha256,
        ):
            if len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError("failed-G2 sidecar digest invalid")
        return self

    @property
    def receipt_sha256(self) -> str:
        """Consistency digest only; it does not authenticate raw acquisition."""
        return _digest(self)


class HighVolFailedG2Evaluation(QualificationModel):
    prefix: QualificationPrefixRun
    sidecar: HighVolFailedG2Sidecar | None = None
    execution_authority: Literal[False] = False

    @model_validator(mode="after")
    def only_rejected_g2_can_have_sidecar(self):
        if self.sidecar is not None:
            g2 = (
                self.prefix.result.gates[1] if _rejected_high_vol(self.prefix) else None
            )
            if (
                g2 is None
                or self.sidecar.prefix_evaluation_sha256
                != self.prefix.evaluation_sha256
                or self.sidecar.g1_source_sha256
                != self.prefix.data_result.source_sha256
                or self.sidecar.g2_gate_sha256 != _digest(g2)
                or self.sidecar.original_pin.report_id != self.prefix.intent.report_id
                or self.sidecar.original_pin.instrument_id
                != self.prefix.intent.instrument_id
                or self.sidecar.original_pin.strategy != self.prefix.intent.strategy
                or self.sidecar.original_pin.direction != self.prefix.intent.direction
                or self.sidecar.observation.observed_at
                != self.prefix.result.evaluated_at
                or self.sidecar.observation.analysis_version
                != self.prefix.policy.data.analysis_version
                or self.sidecar.observation.route_sha256
                not in (None, g2.measured_values.get("analysis_sha256"))
            ):
                raise ValueError("sidecar differs from failed G2 or G1 source")
        return self


def evaluate_high_vol_failed_g2(
    market: MarketSnapshot,
    *,
    intent: QualificationIntent,
    quote,
    reference,
    policy: QualificationPrefixPolicy,
    consumed_event_keys: frozenset[str],
    evaluated_at: datetime,
    original_pin: HighVolOriginalSourcePin | None = None,
    enabled: bool = False,
) -> HighVolFailedG2Evaluation:
    """Run the unmodified prefix, then optionally replay a failed-G2 sidecar.

    A saved prefix or caller-provided G1 record cannot enter this function. The
    only source read by the observer is the G1 source_json built in this call.
    """
    if type(enabled) is not bool:
        raise ValueError("HighVol diagnostic switch requires an exact boolean")
    prefix = evaluate_qualification_prefix(
        market,
        intent=intent,
        quote=quote,
        reference=reference,
        policy=policy,
        consumed_event_keys=consumed_event_keys,
        evaluated_at=evaluated_at,
    )
    if not enabled or not _rejected_high_vol(prefix):
        return HighVolFailedG2Evaluation(prefix=prefix)
    if type(original_pin) is not HighVolOriginalSourcePin:
        raise ValueError("an exact original source pin is required for the sidecar")
    original = HighVolOriginalSourcePin.model_validate(
        original_pin.model_dump(round_trip=True), strict=True
    )
    if (
        original.report_id != prefix.intent.report_id
        or original.instrument_id != prefix.intent.instrument_id
        or original.strategy != prefix.intent.strategy
        or original.direction != prefix.intent.direction
    ):
        raise ValueError("original source pin differs from failed G2 identity")
    source = json.loads(prefix.data_result.source_json)
    rebuilt = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    observation = observe_high_vol_momentum(
        rebuilt,
        original=original,
        observed_at=prefix.result.evaluated_at,
        analysis_version=prefix.policy.data.analysis_version,
        enabled=True,
    )
    sidecar = HighVolFailedG2Sidecar(
        prefix_evaluation_sha256=prefix.evaluation_sha256,
        g1_source_sha256=prefix.data_result.source_sha256,
        g2_gate_sha256=_digest(prefix.result.gates[1]),
        original_pin=original,
        observation=observation,
    )
    return HighVolFailedG2Evaluation(prefix=prefix, sidecar=sidecar)


def verify_high_vol_failed_g2(
    saved: HighVolFailedG2Evaluation, market: MarketSnapshot, **inputs
) -> HighVolFailedG2Evaluation:
    """Replay original inputs; a copied sidecar or matching hash cannot pass."""
    if type(saved) is not HighVolFailedG2Evaluation:
        raise ValueError("exact failed-G2 evaluation required")
    checked = HighVolFailedG2Evaluation.model_validate(
        saved.model_dump(round_trip=True), strict=True
    )
    replayed = evaluate_high_vol_failed_g2(market, **inputs)
    if replayed != checked:
        raise ValueError("failed-G2 sidecar replay mismatch")
    return replayed
