"""Replayable G1--G11 computation, never evidence completion or order authority.

Account/source stamps are supplied claims, not authenticated reconciliation.
The joint structural selector must produce one valid bracket before G8 can
pass; no independent stop is invented when every bracket fails. G10 then costs
that unchanged bracket using the separately captured quote. No re-selection,
reservation, account IO, rendering, clock lookup or execution occurs here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.structural_protection import select_structural_protection
from app.trade_qualification.data import WSReferenceObservation
from app.trade_qualification.economics import (
    EconomicsPolicy,
    EconomicsResult,
    evaluate_economics,
)
from app.trade_qualification.event_models import Digest
from app.trade_qualification.models import (
    EntryQualificationResult,
    GateAssessment,
    Price,
    QualificationGate,
    QualificationModel,
    Text,
    require_aware,
)
from app.trade_qualification.portfolio import (
    ContractRiskSpec,
    DemoRiskAuthority,
    EvidenceStamp,
    PendingReservation,
    PortfolioRiskPolicy,
    PortfolioRiskResult,
    PortfolioRiskSnapshot,
    PositionExposure,
    RealizedOutcome,
    evaluate_portfolio,
)
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    QualificationPrefixRun,
    _plain,
    evaluate_qualification_prefix,
)
from app.trade_qualification.service import (
    _bounded as _prefix_bounded,
)

D = Decimal
_MAX_BYTES = 8 * 1024 * 1024
_ORDER = tuple(QualificationGate)[:11]
_NONNEGATIVE = Annotated[
    Decimal, Field(ge=0, lt=10000, max_digits=40, decimal_places=20)
]
_POSITIVE = Annotated[Decimal, Field(gt=0, lt=10000, max_digits=40, decimal_places=20)]


class ProtectionPolicy(QualificationModel):
    policy_id: Text
    expected_slippage_bps: _NONNEGATIVE
    cost_bps: _NONNEGATIVE
    min_net_rr: _POSITIVE
    min_stop_distance_atr: _POSITIVE
    atr_buffer_multiplier: _POSITIVE
    minimum_buffer_bps: _POSITIVE


class PreEvidencePolicy(QualificationModel):
    policy_id: Text
    prefix: QualificationPrefixPolicy
    protection: ProtectionPolicy
    economics: EconomicsPolicy | None
    portfolio: PortfolioRiskPolicy | None


class PortfolioInputs(QualificationModel):
    requested_contracts: Price
    requested_leverage: int = Field(ge=1, le=125)
    instrument: ContractRiskSpec | None
    account: PortfolioRiskSnapshot | None
    authority: DemoRiskAuthority | None


def _preflight(value, depth=0, budget=None):
    """Exact declared models and bounded raw fields, before any serializer."""
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise ValueError("pre-evidence input exceeds traversal bounds")
    if isinstance(value, BaseModel):
        if type(value) not in {
            ProtectionPolicy,
            PreEvidencePolicy,
            PortfolioInputs,
            PreEvidenceRun,
            EconomicsPolicy,
            EconomicsResult,
            PortfolioRiskPolicy,
            PortfolioRiskResult,
            ContractRiskSpec,
            PortfolioRiskSnapshot,
            DemoRiskAuthority,
            EvidenceStamp,
            PositionExposure,
            PendingReservation,
            RealizedOutcome,
        }:
            _prefix_bounded(value)
            return
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("pre-evidence input has hidden or missing fields")
        for item in value.__dict__.values():
            _preflight(item, depth + 1, budget)
    elif type(value) in (dict, MappingProxyType):
        if len(value) > 64:
            raise ValueError("pre-evidence mapping exceeds bounds")
        for key, item in value.items():
            _preflight(key, depth + 1, budget)
            _preflight(item, depth + 1, budget)
    elif type(value) in (tuple, list):
        if len(value) > 2048:
            raise ValueError("pre-evidence ledger exceeds bounds")
        for item in value:
            _preflight(item, depth + 1, budget)
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 200
        ):
            raise ValueError("pre-evidence decimal exceeds bounds")
    elif type(value) is str:
        if len(value) > _MAX_BYTES:
            raise ValueError("pre-evidence text exceeds bounds")
    elif type(value) is datetime:
        require_aware(value)
    elif isinstance(value, Enum):
        _preflight(value.value, depth + 1, budget)
    elif (
        value is None
        or type(value) is bool
        or type(value) is int
        and abs(value) <= 10**40
    ):
        pass
    else:
        raise ValueError("unsupported pre-evidence input type")


def _copy(value, expected):
    if type(value) is not expected:
        raise ValueError(f"an exact {expected.__name__} is required")
    _preflight(value)
    return expected.model_validate(_plain(value), strict=True)


def _hash(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    if len(raw) > 3 * _MAX_BYTES:
        raise ValueError("pre-evidence record exceeds byte bounds")
    return hashlib.sha256(raw).hexdigest()


def _audit(raw):
    if type(raw) is not str or not 0 < len(raw) <= _MAX_BYTES:
        raise ValueError("invalid bounded structural audit")
    try:
        value = json.loads(raw)
        if (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            != raw
        ):
            raise ValueError("structural audit must be canonical")
        if (
            type(value) is not dict
            or value.get("schema") != "ctcc_structural_selection_v1"
        ):
            raise ValueError("unknown structural audit")
        if (
            value.get("record_kind") != "audit_not_authority"
            or value.get("execution_authority") is not False
            or value.get("economics_validated") is not False
            or value.get("policy_calibrated") is not False
        ):
            raise ValueError("structural audit cannot grant authority")
        if value.get("protection_valid") is True:
            selected = value.get("selected")
            if (
                type(selected) is not dict
                or type(selected.get("stop")) is not dict
                or type(selected.get("target")) is not dict
                or type(value.get("reference_entry")) is not str
                or type(selected["stop"].get("final_stop")) is not str
                or type(selected["target"].get("final_target")) is not str
            ):
                raise ValueError("selected structural audit has incomplete prices")
            for price in (
                value["reference_entry"],
                selected["stop"]["final_stop"],
                selected["target"]["final_target"],
            ):
                if len(price) > 128 or not D(price).is_finite():
                    raise ValueError("selected structural audit has invalid prices")
        return value
    except (TypeError, RecursionError, OverflowError, DecimalException):
        raise ValueError("invalid structural audit") from None


class PreEvidenceRun(QualificationModel):
    prefix: QualificationPrefixRun
    policy: PreEvidencePolicy
    risk_inputs: PortfolioInputs
    prefix_sha256: Digest
    intent_sha256: Digest
    policy_sha256: Digest
    risk_inputs_sha256: Digest
    result: EntryQualificationResult
    protection_audit_json: Annotated[str, Field(max_length=_MAX_BYTES)] | None = None
    protection_sha256: Digest | None = None
    economics: EconomicsResult | None = None
    portfolio: PortfolioRiskResult | None = None
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "account_evidence_authenticated",
        "atomic_risk_reserved",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("pre-evidence calculation cannot grant authority")
        return value

    @model_validator(mode="after")
    def consistent_run(self):
        prefix, result = self.prefix, self.result
        count = len(result.gates)
        if (
            not 1 <= count <= 11
            or tuple(g.gate for g in result.gates) != _ORDER[:count]
        ):
            raise ValueError("pre-evidence gates must stop at G11")
        if (
            self.policy.prefix != prefix.policy
            or self.prefix_sha256 != prefix.evaluation_sha256
        ):
            raise ValueError("prefix or prefix policy mismatch")
        if (
            self.intent_sha256 != _hash(prefix.intent)
            or self.policy_sha256 != _hash(self.policy)
            or self.risk_inputs_sha256 != _hash(self.risk_inputs)
        ):
            raise ValueError("pre-evidence input pin mismatch")
        unchanged = _plain(prefix.result)
        for field in ("gates", "stop_loss", "take_profit", "gross_rr", "net_rr"):
            unchanged.pop(field)
        actual = _plain(result)
        if any(actual[name] != value for name, value in unchanged.items()):
            raise ValueError("pre-evidence changed original entry or prefix state")
        if result.gates[: len(prefix.result.gates)] != prefix.result.gates:
            raise ValueError("a later gate changed or replaced a prefix gate")
        if not prefix.prefix_complete and (
            count != len(prefix.result.gates) or result != prefix.result
        ):
            raise ValueError("evaluation continued after a prefix failure")
        if (
            (self.protection_audit_json is not None) != (count >= 8)
            or (self.economics is not None) != (count >= 10)
            or (self.portfolio is not None) != (count >= 11)
        ):
            raise ValueError("sub-results must match the visited gates")
        if self.protection_audit_json is None:
            if self.protection_sha256 is not None:
                raise ValueError("structural digest has no audit")
        else:
            raw = self.protection_audit_json
            audit = _audit(raw)
            if hashlib.sha256(raw.encode()).hexdigest() != self.protection_sha256:
                raise ValueError("structural audit digest mismatch")
            if result.gates[7].passed:
                selected = audit.get("selected")
                if (
                    not selected
                    or audit.get("protection_valid") is not True
                    or audit.get("fail_codes") != []
                    or selected.get("rejection_codes") != []
                    or selected["stop"].get("rejection_codes") != []
                    or selected["target"].get("rejection_codes") != []
                    or audit.get("source_sha256") != prefix.data_result.source_sha256
                    or audit.get("report_id") != result.report_id
                    or audit.get("instrument_id") != prefix.intent.instrument_id
                    or audit.get("strategy") != result.strategy
                    or audit.get("direction") != result.direction
                    or D(audit["reference_entry"]) != result.candidate_entry
                    or D(selected["stop"]["final_stop"]) != result.stop_loss
                ):
                    raise ValueError(
                        "selected structural bracket is not bound to the entry"
                    )
                if (
                    count >= 9
                    and result.gates[8].passed
                    and D(selected["target"]["final_target"]) != result.take_profit
                ):
                    raise ValueError("selected target changed")
        if self.economics is not None:
            costs = self.economics
            if (
                (
                    costs.report_id,
                    costs.instrument_id,
                    costs.direction,
                    costs.evaluated_at,
                )
                != (
                    result.report_id,
                    prefix.intent.instrument_id,
                    result.direction,
                    result.evaluated_at,
                )
                or costs.passed != result.gates[9].passed
                or costs.code != result.gates[9].code
                or costs.policy != self.policy.economics
            ):
                raise ValueError("economics sub-result identity or policy mismatch")
            if costs.candidate_entry is not None and (
                costs.candidate_entry,
                costs.stop_loss,
                costs.take_profit,
            ) != (result.candidate_entry, result.stop_loss, result.take_profit):
                raise ValueError("economics changed the selected price geometry")
            if (
                costs.quote_sha256 is not None
                and costs.quote_sha256 != prefix.location.quote_sha256
            ):
                raise ValueError("economics quote differs from the prefix quote")
            with localcontext(Context(prec=100)):
                reported = (
                    tuple(
                        value.quantize(D("1e-30"), rounding=ROUND_HALF_EVEN)
                        for value in (costs.gross_rr, costs.net_rr)
                    )
                    if costs.passed
                    else (None, None)
                )
            if (result.gross_rr, result.net_rr) != reported:
                raise ValueError("reported RR differs from the full economics result")
        if self.portfolio is not None:
            risk = self.portfolio
            if (
                (risk.report_id, risk.instrument_id, risk.direction)
                != (result.report_id, prefix.intent.instrument_id, result.direction)
                or risk.passed != result.gates[10].passed
                or risk.code != result.gates[10].code
                or risk.requested_contracts != self.risk_inputs.requested_contracts
                or risk.requested_leverage != self.risk_inputs.requested_leverage
            ):
                raise ValueError("portfolio sub-result identity mismatch")
        return self

    @computed_field
    @property
    def pre_evidence_complete(self) -> bool:
        return len(self.result.gates) == 11 and all(g.passed for g in self.result.gates)

    @property
    def evaluation_sha256(self):
        return _hash(_copy(self, PreEvidenceRun))


def evaluate_pre_evidence(
    market: MarketSnapshot,
    *,
    intent: QualificationIntent,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    policy: PreEvidencePolicy,
    risk_inputs: PortfolioInputs,
    consumed_event_keys: frozenset[str],
    evaluated_at: datetime,
) -> PreEvidenceRun:
    """Re-run original G1--G7 inputs, then stop at the first failed G8--G11 gate."""
    policy = _copy(policy, PreEvidencePolicy)
    risk_inputs = _copy(risk_inputs, PortfolioInputs)
    prefix = evaluate_qualification_prefix(
        market,
        intent=intent,
        quote=quote,
        reference=reference,
        policy=policy.prefix,
        consumed_event_keys=consumed_event_keys,
        evaluated_at=evaluated_at,
    )
    values = _plain(prefix.result)
    gates = list(prefix.result.gates)
    audit_json = audit_sha = costs = risk = None

    def finish():
        values["gates"] = tuple(gates)
        return PreEvidenceRun.model_validate(
            _plain(
                {
                    "prefix": prefix,
                    "policy": policy,
                    "risk_inputs": risk_inputs,
                    "prefix_sha256": prefix.evaluation_sha256,
                    "intent_sha256": _hash(prefix.intent),
                    "policy_sha256": _hash(policy),
                    "risk_inputs_sha256": _hash(risk_inputs),
                    "result": values,
                    "protection_audit_json": audit_json,
                    "protection_sha256": audit_sha,
                    "economics": costs,
                    "portfolio": risk,
                }
            ),
            strict=True,
        )

    def gate(kind, code, reason, measured):
        gates.append(
            GateAssessment(
                report_id=prefix.intent.report_id,
                gate=kind,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            )
        )
        return code == "passed"

    if not prefix.prefix_complete:
        return finish()
    source = json.loads(prefix.data_result.source_json)
    rebuilt = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    collected = validate_collected_quote(quote)
    if collected.bundle_sha256 != prefix.data_result.quote_bundle_sha256:
        raise ValueError("quote changed during pre-evidence evaluation")
    entry, now = prefix.intent.candidate_entry, prefix.result.evaluated_at
    with localcontext(Context(prec=100)):
        selected = select_structural_protection(
            prefix.detection,
            rebuilt,
            analysis,
            observed_at=now,
            entry=entry,
            tick_size=policy.prefix.tick_size,
            **{
                key: value
                for key, value in _plain(policy.protection).items()
                if key != "policy_id"
            },
        )
        audit_json = selected.to_audit_json()
        audit_sha = hashlib.sha256(audit_json.encode()).hexdigest()
        bracket = selected.selected
        if selected.protection_valid:
            values["stop_loss"] = bracket.stop.final_stop
        if not gate(
            QualificationGate.STOP,
            "passed"
            if selected.protection_valid
            else selected.fail_codes[0]
            if selected.fail_codes
            else "no_valid_structural_bracket",
            "G8 requires the stop of a complete source-selected bracket; no stop is invented.",
            {
                "source_sha256": prefix.data_result.source_sha256,
                "protection_sha256": audit_sha,
                "selection_failures": ",".join(selected.fail_codes) or "none",
                "alternative_count": len(selected.alternatives),
                "candidate_entry": entry,
                "stop_loss": values["stop_loss"],
                "joint_bracket_required": True,
            },
        ):
            return finish()
        values["take_profit"] = bracket.target.final_target
        target_ok = bracket.valid and not bracket.target.rejection_codes
        if not gate(
            QualificationGate.TARGET,
            "passed" if target_ok else "structural_target_invalid",
            "G9 retains the same bracket's source target, including the nearer-barrier checks.",
            {
                "source_sha256": prefix.data_result.source_sha256,
                "protection_sha256": audit_sha,
                "target_anchor_id": bracket.target.anchor.anchor_id,
                "take_profit": values["take_profit"],
                "candidate_entry": entry,
                "stop_loss": values["stop_loss"],
            },
        ):
            return finish()
        costs = evaluate_economics(
            report_id=prefix.intent.report_id,
            instrument_id=prefix.intent.instrument_id,
            direction=prefix.intent.direction,
            candidate_entry=entry,
            stop_loss=values["stop_loss"],
            take_profit=values["take_profit"],
            quote=collected.quote,
            policy=policy.economics,
            current_time=now,
        )
        if costs.passed:
            values["gross_rr"] = costs.gross_rr.quantize(
                D("1e-30"), rounding=ROUND_HALF_EVEN
            )
            values["net_rr"] = costs.net_rr.quantize(
                D("1e-30"), rounding=ROUND_HALF_EVEN
            )
        if not gate(
            QualificationGate.ECONOMICS,
            costs.code,
            costs.reason,
            {
                "quote_sha256": costs.quote_sha256,
                "cost_policy_sha256": costs.policy_sha256,
                "total_cost_bps": costs.total_cost_bps,
                "cost_per_base": costs.cost_per_base,
                "gross_rr": costs.gross_rr,
                "net_rr": costs.net_rr,
                "entry_unchanged": True,
                "stop_unchanged": True,
            },
        ):
            return finish()
        risk = evaluate_portfolio(
            report_id=prefix.intent.report_id,
            instrument_id=prefix.intent.instrument_id,
            direction=prefix.intent.direction,
            candidate_entry=entry,
            stop_loss=values["stop_loss"],
            round_trip_cost_per_base=costs.cost_per_base,
            requested_contracts=risk_inputs.requested_contracts,
            requested_leverage=risk_inputs.requested_leverage,
            instrument=risk_inputs.instrument,
            account=risk_inputs.account,
            authority=risk_inputs.authority,
            policy=policy.portfolio,
            current_time=now,
        )
        gate(
            QualificationGate.RISK,
            risk.code,
            risk.reason,
            {
                "risk_inputs_sha256": _hash(risk_inputs),
                "risk_evidence_sha256": risk.evidence_sha256,
                "risk_cause_count": len(risk.causes),
                "risk_first_cause": risk.causes[0] if risk.causes else "none",
                "risk_result_sha256": _hash(risk),
                "requested_contracts": risk_inputs.requested_contracts,
                "requested_leverage": risk_inputs.requested_leverage,
                "max_loss_amount": risk.max_loss_amount,
                "risk_pct": risk.risk_pct,
                "atomic_risk_reserved": False,
                "account_evidence_authenticated": False,
            },
        )
        return finish()


def verify_pre_evidence(run, market, **original_inputs) -> PreEvidenceRun:
    """Re-execute all visited gates; self-consistent hashes alone are insufficient."""
    checked = _copy(run, PreEvidenceRun)
    replayed = evaluate_pre_evidence(market, **original_inputs)
    if checked != replayed:
        raise ValueError("pre_evidence_replay_mismatch")
    return replayed
