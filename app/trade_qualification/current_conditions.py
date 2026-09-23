"""Current G1--G4 only: preserve the intent, never find or replace its event.

Raw quality/analysis is rebuilt by this invocation's G1. G2--G4 deliberately
match the existing prefix's strategy-specific route/HTF/required/veto/score
semantics, including fresh executable spread and adverse funding. A current
boolean momentum state is not a new trigger or an original-event survival proof.
The fixed candidate's lifetime, original event/zone/bracket and consumed-event
ledger belong to the enclosing recheck, not this four-gate calculation.

This pure module has no adapter, execution, source-authentication or G13 authority.
Copying/fingerprinting a record validates consistency only; replay original raw
inputs with ``verify_current_conditions`` before relying on a saved calculation.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.data import (
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.event_models import Digest
from app.trade_qualification.history_prefix import (
    HistoryEntryQualificationResultV3,
    HistoryEntryQualificationResultV4,
    HistoryQualificationPrefixPolicyV2,
    HistoryQualificationPrefixPolicyV3,
    HistoryQualificationPrefixPolicyV4,
    expansion_htf_permission,
)
from app.trade_qualification.history_prefix import (
    _bounded as _history_bounded,
)
from app.trade_qualification.history_prefix import (
    _copy as _history_copy,
)
from app.trade_qualification.models import (
    EntryQualificationResult,
    GateAssessment,
    MarketRegime,
    QualificationGate,
    QualificationModel,
)
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.reversal_policy import (
    reversal_current_permission,
    reversal_safety_permission,
)
from app.trade_qualification.service import (
    QualificationIntent,
    QualificationPrefixPolicy,
    _bounded,
    _copy,
    _digest,
    _plain,
    _time,
)

D = Decimal
_ORDER = tuple(QualificationGate)[:4]


def _raw_record(value):
    if isinstance(value, BaseModel):
        if (
            type(value)
            not in (
                CurrentConditionsResult,
                HistoryCurrentConditionsResultV2,
                HistoryCurrentConditionsResultV3,
                HistoryCurrentConditionsResultV4,
            )
            or set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("exact current-conditions record required")
        values = value.__dict__
    elif type(value) is dict and len(value) <= 64:
        values = value
    else:
        raise ValueError("bounded current-conditions fields required")
    for key, item in values.items():
        if type(key) is not str or len(key) > 128:
            raise ValueError("invalid current-conditions field")
        if type(item) in (
            HistoryQualificationPrefixPolicyV2,
            HistoryQualificationPrefixPolicyV3,
            HistoryQualificationPrefixPolicyV4,
            HistoryEntryQualificationResultV3,
            HistoryEntryQualificationResultV4,
        ):
            _history_bounded(item)
        else:
            _bounded(item)
    return value


class CurrentConditionsResult(QualificationModel):
    intent: QualificationIntent
    policy: QualificationPrefixPolicy
    policy_sha256: Digest
    data_result: DataQualificationResult
    result: EntryQualificationResult
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    event_continuation_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @model_validator(mode="before")
    @classmethod
    def bounded_input(cls, value, info):
        value = _raw_record(value)
        if info.mode == "json":
            # A model-level before validator receives Python dictionaries. Give
            # fixed nested records their strict JSON boundary explicitly, so
            # Decimal/datetime/tuple JSON encodings retain their documented
            # meaning without permitting string coercion in Python inputs.
            value = dict(value)
            for field, model in (
                ("intent", QualificationIntent),
                (
                    "policy",
                    HistoryQualificationPrefixPolicyV4
                    if value.get("contract_version")
                    == "ctcc-history-current-conditions-v4"
                    else HistoryQualificationPrefixPolicyV3
                    if value.get("contract_version")
                    == "ctcc-history-current-conditions-v3"
                    else HistoryQualificationPrefixPolicyV2
                    if value.get("contract_version")
                    == "ctcc-history-current-conditions-v2"
                    else QualificationPrefixPolicy,
                ),
                ("data_result", DataQualificationResult),
                (
                    "result",
                    HistoryEntryQualificationResultV4
                    if value.get("contract_version")
                    == "ctcc-history-current-conditions-v4"
                    else HistoryEntryQualificationResultV3
                    if value.get("contract_version")
                    == "ctcc-history-current-conditions-v3"
                    else EntryQualificationResult,
                ),
            ):
                if field in value:
                    value[field] = _plain(
                        model.model_validate_json(
                            json.dumps(value[field], allow_nan=False), strict=True
                        )
                    )
        return value

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "event_continuation_verified",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("current conditions cannot grant recheck authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        intent, result, data = self.intent, self.result, self.data_result
        gates = result.gates
        if (
            not 1 <= len(gates) <= 4
            or tuple(item.gate for item in gates) != _ORDER[: len(gates)]
            or (len(gates) < 4 and gates[-1].passed)
            or gates[0] != data.gate
            or data.report_id != intent.report_id
            or data.instrument_id != intent.instrument_id
            or data.evaluated_at != result.evaluated_at
            or data.policy != self.policy.data
            or result.report_id != intent.report_id
            or result.strategy != intent.strategy
            or result.direction != intent.direction
            or result.candidate_entry != intent.candidate_entry
            or self.policy_sha256 != _digest(self.policy)
        ):
            raise ValueError("current-conditions identity, policy or gate mismatch")
        if result.entry_timing_state != "not_evaluated" or any(
            value is not None
            for value in (
                result.trigger,
                result.entry_zone,
                result.reference_price,
                result.stop_loss,
                result.take_profit,
                result.gross_rr,
                result.net_rr,
            )
        ):
            raise ValueError("current conditions cannot replace event or geometry")
        return self

    @property
    def gates(self):
        return self.result.gates

    @property
    def passed(self):
        return len(self.gates) == 4 and all(item.passed for item in self.gates)

    @property
    def code(self):
        return self.gates[-1].code

    @property
    def evaluation_sha256(self):
        return _digest(copy_current_conditions(self))


class HistoryCurrentConditionsResultV2(CurrentConditionsResult):
    contract_version: Literal["ctcc-history-current-conditions-v2"]
    policy: HistoryQualificationPrefixPolicyV2
    origin_sha256: Digest
    original_event_key: Digest
    history_admission_sha256: Digest

    @model_validator(mode="after")
    def expansion_only(self):
        if self.intent.strategy != "volatility_expansion":
            raise ValueError("history_current_conditions_expansion_only")
        return self


class HistoryCurrentConditionsResultV3(CurrentConditionsResult):
    contract_version: Literal["ctcc-history-current-conditions-v3"]
    policy: HistoryQualificationPrefixPolicyV3
    result: HistoryEntryQualificationResultV3
    origin_sha256: Digest
    original_event_key: Digest
    history_admission_sha256: Digest

    @model_validator(mode="after")
    def reversal_only(self):
        if self.intent.strategy != "structure_reversal":
            raise ValueError("history_current_conditions_reversal_only")
        if (
            len(self.gates) >= 2
            and self.result.history_admission_sha256 != self.history_admission_sha256
        ):
            raise ValueError("history_reversal_current_original_pin_mismatch")
        if len(self.gates) >= 3:
            measured = self.gates[2].measured_values
            if (
                measured.get("reversal_policy") != self.policy.reversal_policy
                or measured.get("history_admission_sha256")
                != self.history_admission_sha256
                or measured.get("original_event_key") != self.original_event_key
                or measured.get("current_source_sha256")
                != self.data_result.source_sha256
            ):
                raise ValueError("history_reversal_current_binding_mismatch")
        return self


class HistoryCurrentConditionsResultV4(CurrentConditionsResult):
    contract_version: Literal["ctcc-history-current-conditions-v4"]
    policy: HistoryQualificationPrefixPolicyV4
    result: HistoryEntryQualificationResultV4
    origin_sha256: Digest
    original_event_key: Digest

    @model_validator(mode="after")
    def range_only(self):
        if (
            self.intent.strategy != "range_reversal"
            or self.result.range_anchor_policy != self.policy.range_anchor_policy
        ):
            raise ValueError("history_current_conditions_range_policy_required")
        return self


def copy_current_conditions(result) -> CurrentConditionsResult:
    """Strict, bounded consistency copy; this does not authenticate a PASS."""
    if type(result) not in (
        CurrentConditionsResult,
        HistoryCurrentConditionsResultV2,
        HistoryCurrentConditionsResultV3,
        HistoryCurrentConditionsResultV4,
    ):
        raise ValueError("exact current-conditions result required")
    _raw_record(result)
    return type(result).model_validate(_plain(result), strict=True)


def evaluate_current_conditions(
    current_market: MarketSnapshot,
    *,
    intent: QualificationIntent,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    policy: QualificationPrefixPolicy,
    observed_at: datetime,
) -> CurrentConditionsResult:
    if type(policy) is not QualificationPrefixPolicy:
        raise ValueError("exact legacy current-conditions policy required")
    return _evaluate_current_conditions(
        current_market,
        intent=intent,
        quote=quote,
        reference=reference,
        policy=policy,
        observed_at=observed_at,
    )


def evaluate_history_current_conditions_v2(
    current_market, *, origin, quote, reference, observed_at
):
    from app.trade_qualification.history_engine import HistoryPreEvidenceRunV2
    from app.trade_qualification.recheck_models import copy_recheck_origin

    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not HistoryPreEvidenceRunV2
        or pre.result.strategy != "volatility_expansion"
    ):
        raise ValueError("history_current_conditions_origin_version_required")
    return _evaluate_current_conditions(
        current_market,
        intent=pre.prefix.intent,
        policy=pre.policy.prefix,
        quote=quote,
        reference=reference,
        observed_at=observed_at,
        history_pins={
            "contract_version": "ctcc-history-current-conditions-v2",
            "origin_sha256": original.evaluation_sha256,
            "original_event_key": original.original_event_key,
            "history_admission_sha256": pre.prefix.history_admission.evaluation_sha256,
        },
    )


def evaluate_history_current_conditions_v3(
    current_market, *, origin, quote, reference, observed_at
):
    from app.trade_qualification.history_engine import HistoryPreEvidenceRunV3
    from app.trade_qualification.recheck_models import copy_recheck_origin

    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not HistoryPreEvidenceRunV3
        or pre.result.strategy != "structure_reversal"
    ):
        raise ValueError("history_current_conditions_origin_version_required")
    return _evaluate_current_conditions(
        current_market,
        intent=pre.prefix.intent,
        policy=pre.policy.prefix,
        quote=quote,
        reference=reference,
        observed_at=observed_at,
        history_pins={
            "contract_version": "ctcc-history-current-conditions-v3",
            "origin_sha256": original.evaluation_sha256,
            "original_event_key": original.original_event_key,
            "history_admission_sha256": pre.prefix.history_admission.evaluation_sha256,
        },
    )


def evaluate_history_current_conditions_v4(
    current_market, *, origin, quote, reference, observed_at
):
    from app.trade_qualification.history_engine import HistoryPreEvidenceRunV4
    from app.trade_qualification.recheck_models import copy_recheck_origin

    original = copy_recheck_origin(origin)
    pre = original.evidence.pre_evidence
    if (
        type(pre) is not HistoryPreEvidenceRunV4
        or pre.result.strategy != "range_reversal"
    ):
        raise ValueError("history_current_conditions_origin_version_required")
    return _evaluate_current_conditions(
        current_market,
        intent=pre.prefix.intent,
        policy=pre.policy.prefix,
        quote=quote,
        reference=reference,
        observed_at=observed_at,
        history_pins={
            "contract_version": "ctcc-history-current-conditions-v4",
            "origin_sha256": original.evaluation_sha256,
            "original_event_key": original.original_event_key,
        },
    )


def _evaluate_current_conditions(
    current_market, *, intent, quote, reference, policy, observed_at, history_pins=None
):
    """Rebuild raw current G1, then stop at the first failure through G4.

    No caller analysis, route, score, event or prior passing G1 is accepted.
    Intent/policy/time contract errors raise; missing or malformed market/quote
    evidence is rejected by the real G1 before route or conditions are evaluated.
    """
    intent = _copy(intent, QualificationIntent)
    version2 = type(policy) is HistoryQualificationPrefixPolicyV2
    version3 = type(policy) is HistoryQualificationPrefixPolicyV3
    version4 = type(policy) is HistoryQualificationPrefixPolicyV4
    policy = (
        _history_copy(policy, HistoryQualificationPrefixPolicyV4)
        if version4
        else _history_copy(policy, HistoryQualificationPrefixPolicyV3)
        if version3
        else _history_copy(policy, HistoryQualificationPrefixPolicyV2)
        if version2
        else _copy(policy, QualificationPrefixPolicy)
    )
    result_type = (
        HistoryCurrentConditionsResultV4
        if version4
        else HistoryCurrentConditionsResultV3
        if version3
        else HistoryCurrentConditionsResultV2
        if version2
        else CurrentConditionsResult
    )
    now = _time(observed_at)
    data = evaluate_data(
        current_market,
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        quote=quote,
        reference=reference,
        policy=policy.data,
        evaluated_at=now,
    )
    gates = [data.gate]
    values = {
        "report_id": intent.report_id,
        "symbol": intent.instrument_id,
        "strategy": intent.strategy,
        "direction": intent.direction,
        "evaluated_at": now,
        "candidate_entry": intent.candidate_entry,
        "raw_score": 0,
        "effective_score": 0,
    }

    def finish():
        if version4:
            values.update(
                contract_version="ctcc-history-qualification-result-v4",
                range_anchor_policy=policy.range_anchor_policy,
            )
        if version3:
            values.update(
                contract_version="ctcc-history-qualification-result-v3",
                history_admission_sha256=history_pins["history_admission_sha256"]
                if len(gates) >= 2
                else None,
            )
        qualification_type = (
            HistoryEntryQualificationResultV4
            if version4
            else HistoryEntryQualificationResultV3
            if version3
            else EntryQualificationResult
        )
        return result_type(
            intent=intent,
            policy=policy,
            policy_sha256=_digest(policy),
            data_result=data,
            result=qualification_type(**values, gates=tuple(gates)),
            **(history_pins or {}),
        )

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

    if not data.passed:
        return finish()
    source = json.loads(data.source_json)
    rebuilt = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    collected = validate_collected_quote(quote)
    if collected.bundle_sha256 != data.quote_bundle_sha256:
        raise ValueError("quote changed during current-conditions calculation")
    executable = collected.quote
    values["symbol"] = rebuilt.symbol
    with localcontext(Context(prec=100)):
        route = route_regime(analysis)
        allowed = (
            route.decision == RouteDecision.ALLOW_SCORING
            and intent.strategy in route.allowed_strategies
        )
        if version2:
            allowed = (
                intent.strategy == "volatility_expansion"
                and route.regime == MarketRegime.EXPANSION
                and "breakout_continuation" in route.allowed_strategies
            )
        if version3:
            allowed = (
                intent.strategy == "structure_reversal"
                and reversal_safety_permission(analysis, route, intent.direction)
            )
        values["market_regime"] = route.regime
        if version3 and allowed and route.regime == MarketRegime.UNKNOWN:
            values["market_regime"] = "History Verified Reversal"
        if not gate(
            QualificationGate.REGIME,
            "passed" if allowed else "regime_strategy_not_allowed",
            "Only the source-derived regime's admitted families may continue.",
            {
                "regime": route.regime.value,
                "route_decision": route.decision.value,
                "allowed_strategies": ",".join(route.allowed_strategies) or "none",
                "route_diagnostics": ",".join(route.fail_codes) or "none",
                "analysis_sha256": route.snapshot_sha256,
                "source_sha256": data.source_sha256,
                **(
                    {
                        "history_evaluation_sha256": history_pins[
                            "history_admission_sha256"
                        ],
                        "route_method": "original_history_current_safety",
                    }
                    if version3
                    else {}
                ),
            },
        ):
            return finish()
        # This unused legacy context operand does not create an RR/candidate.
        ctx = StrategyContext(analysis, rebuilt, policy.minimum_score, D(1))
        assessment = assess_conditions(ctx, intent.strategy)
        values["raw_score"] = assessment.score
        htf = assessment.for_group("htf")
        htf_failures = tuple(
            c.code for c in htf if (c.required or c.veto) and not c.passed
        )
        h4, h1 = (analysis.timeframe_analyses[tf] for tf in ("4H", "1H"))
        range_htf = intent.strategy == "range_reversal" and all(
            view.structure.trend == "neutral" and view.directional_bias == "neutral"
            for view in (h4, h1)
        )
        direction_matches = assessment.direction == intent.direction
        htf_ok = direction_matches and not htf_failures and (bool(htf) or range_htf)
        expansion_values = {}
        if version2:
            htf_ok = direction_matches and expansion_htf_permission(
                analysis, route, intent.direction
            )
            expansion_values = {
                "expansion_htf_policy": policy.expansion_htf_policy,
                "history_admission_sha256": history_pins["history_admission_sha256"],
                "original_event_key": history_pins["original_event_key"],
                "current_source_sha256": data.source_sha256,
                "current_analysis_sha256": route.snapshot_sha256,
            }
        if version3:
            htf_ok = direction_matches and reversal_current_permission(
                analysis, rebuilt, route, intent.direction
            )
            expansion_values = {
                "reversal_policy": policy.reversal_policy,
                "history_admission_sha256": history_pins["history_admission_sha256"],
                "original_event_key": history_pins["original_event_key"],
                "current_source_sha256": data.source_sha256,
                "current_analysis_sha256": route.snapshot_sha256,
                "retained_analysis_blockers": ",".join(analysis.blockers) or "none",
            }
        if htf_ok:
            values["htf_bias"] = "neutral" if range_htf else assessment.direction
        if not gate(
            QualificationGate.HTF,
            "passed" if htf_ok else "htf_strategy_permission_denied",
            "HTF permission is strategy-specific; neutral range is not trend alignment.",
            {
                "strategy_direction": assessment.direction,
                "requested_direction": intent.direction,
                "4h_bias": h4.directional_bias,
                "1h_bias": h1.directional_bias,
                "4h_structure": h4.structure.trend,
                "1h_structure": h1.structure.trend,
                "neutral_range_permission": range_htf,
                "failed_htf_conditions": ",".join(htf_failures) or "none",
                **{c.code: c.passed for c in htf},
                **expansion_values,
            },
        ):
            return finish()
        spread = (
            (Fraction(executable.ask) - Fraction(executable.bid))
            / ((Fraction(executable.ask) + Fraction(executable.bid)) / 2)
            * 10000
        )
        funding = Fraction(executable.funding_rate) * (
            10000 if intent.direction == "long" else -10000
        )
        spread_ok = spread <= Fraction(policy.maximum_strategy_spread_bps)
        funding_ok = funding <= Fraction(policy.maximum_adverse_funding_bps)
        code = (
            "required_setup_missing"
            if assessment.required_failures
            else "strategy_veto"
            if assessment.veto_failures
            else "strategy_spread_exceeded"
            if not spread_ok
            else "strategy_adverse_funding_exceeded"
            if not funding_ok
            else "strategy_score_below_minimum"
            if assessment.score < policy.minimum_score
            else "passed"
        )
        if code == "passed":
            values.update(setup_state="valid", effective_score=assessment.score)
        else:
            values["setup_state"] = "invalid"
        gate(
            QualificationGate.SETUP,
            code,
            "Required conditions and vetoes cannot be repaired by a high score.",
            {
                "raw_score": assessment.score,
                "minimum_score": policy.minimum_score,
                "required_failures": ",".join(assessment.required_failures) or "none",
                "veto_failures": ",".join(assessment.veto_failures) or "none",
                "fresh_spread_bps": D(spread.numerator) / D(spread.denominator),
                "fresh_signed_funding_cost_bps": D(funding.numerator)
                / D(funding.denominator),
                "spread_within_strategy_limit": spread_ok,
                "funding_within_strategy_limit": funding_ok,
                "quote_bundle_sha256": data.quote_bundle_sha256,
                **{c.code: c.passed for c in assessment.items},
            },
        )
        return finish()


def verify_current_conditions(run, current_market, **inputs) -> CurrentConditionsResult:
    """Replay the actual raw inputs; a consistent self-signed record is not proof."""
    checked = copy_current_conditions(run)
    replayed = evaluate_current_conditions(current_market, **inputs)
    if checked != replayed:
        raise ValueError("current_conditions_replay_mismatch")
    return replayed
