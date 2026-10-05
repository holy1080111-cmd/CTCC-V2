"""Post-publication base-strategy G2--G4 over verified full-public V2 G1.

This reuses the existing current-condition route/HTF/setup arithmetic. It has
no event, continuation, protection, account, reservation or order authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.source_primitives import canonical, decode, sha
from app.trade_qualification import data, data_v2
from app.trade_qualification import public_market_collector_v2 as public_v2
from app.trade_qualification.current_conditions import (
    _BASE_STRATEGIES,
    _evaluate_base_g2_g4,
)
from app.trade_qualification.data_v2 import DataQualificationResultV2
from app.trade_qualification.engine import PreEvidenceRun
from app.trade_qualification.models import (
    EntryQualificationResult,
    GateAssessment,
    QualificationGate,
)
from app.trade_qualification.recheck_models import copy_recheck_origin
from app.trade_qualification.service import QualificationPrefixPolicy

BASE_STRATEGIES = _BASE_STRATEGIES


@dataclass(frozen=True, slots=True)
class CurrentBaseConditionsDiagnosticV2:
    result: EntryQualificationResult = field(repr=False)
    origin_sha256: str
    original_candidate_sha256: str
    original_event_key: str
    original_policy_sha256: str
    public_bundle_sha256: str
    current_g1_sha256: str
    current_source_sha256: str
    evaluated_at: datetime
    record_kind: str = field(default="post_g12_base_current_conditions_v2", init=False)
    admission: Literal["DENY"] = field(default="DENY", init=False)
    original_event_survival_verified: Literal[False] = field(default=False, init=False)
    execution_recheck_performed: Literal[False] = field(default=False, init=False)
    account_complete: Literal[False] = field(default=False, init=False)
    atomic_risk_reserved: Literal[False] = field(default=False, init=False)
    execution_authority: Literal[False] = field(default=False, init=False)

    @property
    def gates(self):
        return self.result.gates

    @property
    def passed(self):
        return len(self.gates) == 4 and all(gate.passed for gate in self.gates)


def evaluate_current_base_conditions_v2(packet, *, origin, current_g1):
    """Replay new raw G1, then preserve one original base candidate through G4.

    The only input time is the G1 evaluation time. History strategies are not
    mapped onto base semantics. A stored diagnostic can never resume this call.
    """
    if type(current_g1) is not DataQualificationResultV2:
        raise ValueError("current_v2_exact_g1_required")
    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not PreEvidenceRun
        or type(pre.policy.prefix) is not QualificationPrefixPolicy
        or pre.prefix.intent.strategy not in BASE_STRATEGIES
    ):
        raise ValueError("current_v2_base_original_required")
    intent, policy = pre.prefix.intent, pre.policy.prefix
    checked, (_, quote, _, _, _, _) = public_v2._parts(packet)
    document = decode(checked.packet_json, public_v2.MAX_PACKET_BYTES)
    if (
        document["stage"] != "post_publication"
        or document["barrier_completed_at"]
        != original.publication_completed_at.isoformat()
        or document["report_id"] != intent.report_id
        or document["instrument_id"] != intent.instrument_id
    ):
        raise ValueError("current_v2_post_g12_scope_mismatch")
    if current_g1.evaluated_at >= original.deadline:
        raise ValueError("current_v2_original_expired")
    g1 = data_v2.verify_public_market_data_v2(
        current_g1,
        checked,
        expected_bundle_sha256=checked.bundle_sha256,
        policy=policy.data,
        evaluated_at=current_g1.evaluated_at,
    )
    if (
        not g1.passed
        or g1.report_id != intent.report_id
        or g1.instrument_id != intent.instrument_id
        or g1.evaluated_at <= original.publication_completed_at
        or g1.source_json is None
        or g1.source_sha256 is None
    ):
        raise ValueError("current_v2_g1_or_barrier_rejected")
    source = decode(g1.source_json.encode(), data.MAX_SOURCE_BYTES)
    market = MarketSnapshot.model_validate_json(
        json.dumps(source["market"], allow_nan=False), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"], allow_nan=False), strict=True
    )
    gates = [g1.gate]
    values = {
        "report_id": intent.report_id,
        "symbol": market.symbol,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": g1.evaluated_at,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }

    def gate(kind, code, reason, measured):
        item = GateAssessment(
            report_id=intent.report_id,
            gate=kind,
            passed=code == "passed",
            code=code,
            reason=reason,
            measured_values=measured,
        )
        gates.append(item)
        return item.passed

    _evaluate_base_g2_g4(
        market,
        analysis,
        intent=intent,
        policy=policy,
        source_sha256=g1.source_sha256,
        quote_bundle_sha256=g1.quote_bundle_sha256,
        bid=quote.ticker.bid,
        ask=quote.ticker.ask,
        funding_rate=quote.funding.forecast.rate,
        values=values,
        gate=gate,
    )
    result = EntryQualificationResult(**values, gates=tuple(gates))
    if (
        tuple(item.gate for item in result.gates)
        != tuple(QualificationGate)[: len(result.gates)]
        or result.candidate_entry != intent.candidate_entry
        or any(
            getattr(result, name) is not None
            for name in (
                "trigger",
                "entry_zone",
                "reference_price",
                "stop_loss",
                "take_profit",
                "gross_rr",
                "net_rr",
            )
        )
    ):
        raise ValueError("current_v2_candidate_geometry_changed")
    return CurrentBaseConditionsDiagnosticV2(
        result=result,
        origin_sha256=original.evaluation_sha256,
        original_candidate_sha256=sha(
            canonical(original.candidate.model_dump(mode="json", round_trip=True))
        ),
        original_event_key=original.original_event_key,
        original_policy_sha256=original.original_policy_sha256,
        public_bundle_sha256=checked.bundle_sha256,
        current_g1_sha256=g1.evaluation_sha256,
        current_source_sha256=g1.source_sha256,
        evaluated_at=g1.evaluated_at,
    )
