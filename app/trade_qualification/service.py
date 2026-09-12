"""Ordered, replayable G1--G7 evaluation; deliberately no execution authority.

This is a prefix, not the twelve-gate engine. Sources, instrument metadata and
the consumed-event set are explicit adapter inputs, not authenticated facts.
Never feed a caller-created result directly to an execution or evidence gate:
verify it against its original inputs first. Even a verified prefix cannot trade.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Context, Decimal, localcontext
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.data import (
    DataQualificationPolicy,
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.event_models import Digest, StrategyName, TriggerDetection
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.location import (
    LocationResult,
    build_entry_zone,
    evaluate_location,
)
from app.trade_qualification.models import (
    EntryQualificationResult,
    EntryTrigger,
    EntryZone,
    GateAssessment,
    Price,
    QualificationGate,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    TimingPolicy,
    TimingResult,
    evaluate_timing,
)

D = Decimal
_PREFIX = tuple(QualificationGate)[:7]
_MAX_BYTES = 8 * 1024 * 1024


def _time(value):
    if type(value) is not datetime:
        raise ValueError("an exact aware datetime is required")
    return require_aware(value)


class QualificationIntent(QualificationModel):
    """Immutable requested identity/entry; no supplied score or gate booleans."""

    report_id: ReportId
    instrument_id: Text
    strategy: StrategyName
    direction: Literal["long", "short"]
    candidate_entry: Price
    created_at: datetime
    expires_at: datetime

    _times = field_validator("created_at", "expires_at")(_time)

    @model_validator(mode="after")
    def lifetime(self):
        if self.expires_at <= self.created_at:
            raise ValueError("candidate expiry must follow creation")
        return self


class QualificationPrefixPolicy(QualificationModel):
    policy_id: Text
    data: DataQualificationPolicy
    minimum_score: int = Field(ge=0, le=100)
    tick_size: Price
    max_allowed_drift_bps: Decimal = Field(
        ge=0, le=10000, max_digits=30, decimal_places=20
    )
    # Preserve the existing strategy-wide limits, using independently observed
    # REST fields. These policies may tighten, but not waive, those limits.
    maximum_strategy_spread_bps: Decimal = Field(
        default=D(8), ge=0, le=8, max_digits=30, decimal_places=20
    )
    maximum_adverse_funding_bps: Decimal = Field(
        default=D(15), ge=0, le=15, max_digits=30, decimal_places=20
    )


def _bounded(value, depth=0, budget=None):
    """Reject bypass/hidden fields before serializers can erase or traverse them."""
    if budget is None:
        budget = [50000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ValueError("qualification record exceeds the traversal bound")
    if isinstance(value, BaseModel):
        if type(value) not in {
            QualificationIntent,
            QualificationPrefixPolicy,
            QualificationPrefixRun,
            DataQualificationPolicy,
            DataQualificationResult,
            EntryQualificationResult,
            GateAssessment,
            EntryTrigger,
            EntryZone,
            TriggerDetection,
            TimingPolicy,
            TimingResult,
            LocationResult,
        }:
            raise ValueError("an exact declared qualification model type is required")
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("qualification record has hidden or missing fields")
        for item in value.__dict__.values():
            _bounded(item, depth + 1, budget)
    elif type(value) in (dict, MappingProxyType):
        if len(value) > 64:
            raise ValueError("qualification mapping exceeds its bound")
        for key, item in value.items():
            _bounded(key, depth + 1, budget)
            _bounded(item, depth + 1, budget)
    elif type(value) in (tuple, list):
        if len(value) > 64:
            raise ValueError("qualification sequence exceeds its bound")
        for item in value:
            _bounded(item, depth + 1, budget)
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 128
            or abs(value.as_tuple().exponent) > 200
        ):
            raise ValueError("qualification decimal exceeds its bound")
    elif type(value) is str:
        if len(value) > _MAX_BYTES:
            raise ValueError("qualification text exceeds its bound")
    elif type(value) is datetime:
        _time(value)
    elif isinstance(value, Enum):
        _bounded(value.value, depth + 1, budget)
    elif (
        value is None
        or type(value) is bool
        or type(value) is int
        and abs(value) <= 10**40
    ):
        pass
    else:
        raise ValueError("unsupported qualification value")


def _copy(value, expected):
    if type(value) is not expected:
        raise ValueError(f"an exact {expected.__name__} is required")
    _bounded(value)
    # Validate raw fields before serialization. This also prevents a serializer
    # warning/coercion from obscuring model_copy-injected wrong scalar types.
    return expected.model_validate(_plain(value), strict=True)


def _plain(value):
    """Copy preflighted raw fields without model serializers or subclass erasure.

    MappingProxyType is an output-only immutable view; restore its actual dict
    for strict Pydantic input validation. Preserve tuple/scalar types otherwise.
    """
    if isinstance(value, BaseModel):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if type(value) in (dict, MappingProxyType):
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is tuple:
        return tuple(_plain(item) for item in value)
    if type(value) is list:
        return [_plain(item) for item in value]
    return value


def _digest(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _event_keys(value):
    if type(value) is not frozenset or len(value) > 10000:
        raise ValueError("an explicit bounded frozen consumed-event set is required")
    if any(
        type(key) is not str
        or len(key) != 64
        or any(char not in "0123456789abcdef" for char in key)
        for key in value
    ):
        raise ValueError("consumed-event identities must be SHA-256 digests")
    return value


class QualificationPrefixRun(QualificationModel):
    intent: QualificationIntent
    policy: QualificationPrefixPolicy
    timing_policy: TimingPolicy
    policy_sha256: Digest
    consumed_event_keys_sha256: Digest
    data_result: DataQualificationResult
    result: EntryQualificationResult
    detection: TriggerDetection | None = None
    timing: TimingResult | None = None
    location: LocationResult | None = None
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    event_ledger_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "event_ledger_verified",
        "execution_recheck_performed",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("a qualification prefix cannot grant authority")
        return value

    @model_validator(mode="after")
    def consistent_prefix(self):
        intent, result = self.intent, self.result
        if (
            not 1 <= len(result.gates) <= 7
            or tuple(item.gate for item in result.gates) != _PREFIX[: len(result.gates)]
            or result.gates[0] != self.data_result.gate
            or self.data_result.report_id != intent.report_id
            or self.data_result.instrument_id != intent.instrument_id
            or self.data_result.evaluated_at != result.evaluated_at
            or result.report_id != intent.report_id
            or result.strategy != intent.strategy
            or result.direction != intent.direction
            or result.candidate_entry != intent.candidate_entry
            or self.policy_sha256 != _policy_digest(self.policy, self.timing_policy)
            or self.timing_policy != TIMING_POLICIES[intent.strategy]
        ):
            raise ValueError("qualification prefix identity or gate mismatch")
        count = len(result.gates)
        if (self.detection is not None) != (count >= 5) or (
            self.timing is not None
        ) != (count >= 6):
            raise ValueError("event/timing records must match the visited gates")
        for item in (self.detection, self.timing, self.location):
            if item is not None and item.report_id != intent.report_id:
                raise ValueError("sub-result report mismatch")
        if self.detection is not None and (
            self.detection.source_sha256 != self.data_result.source_sha256
            or self.detection.instrument_id != intent.instrument_id
            or self.detection.strategy != intent.strategy
            or self.detection.direction != intent.direction
            or self.detection.observed_at != result.evaluated_at
            or self.detection.trigger != result.trigger
        ):
            raise ValueError("event source or candidate identity mismatch")
        if self.location is not None and count != 7:
            raise ValueError("location was evaluated after an earlier failure")
        if any(
            value is not None
            for value in (
                result.stop_loss,
                result.take_profit,
                result.gross_rr,
                result.net_rr,
            )
        ):
            raise ValueError("G1--G7 cannot claim protection or economics")
        return self

    @computed_field
    @property
    def prefix_complete(self) -> bool:
        return len(self.result.gates) == 7 and all(g.passed for g in self.result.gates)

    @property
    def evaluation_sha256(self):
        """Consistency pin, not evidence that this function ran or can execute."""
        return _digest(_copy(self, QualificationPrefixRun))


def _policy_digest(policy, timing_policy):
    return _digest(
        {
            "prefix": policy.model_dump(mode="json", round_trip=True),
            "timing": timing_policy.model_dump(mode="json", round_trip=True),
        }
    )


def evaluate_qualification_prefix(
    market: MarketSnapshot,
    *,
    intent: QualificationIntent,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    policy: QualificationPrefixPolicy,
    consumed_event_keys: frozenset[str],
    evaluated_at: datetime,
) -> QualificationPrefixRun:
    """Execute the real ordered prefix at one explicit clock and source pin.

    Malformed intent/policy/ledger/time raises before a run can be emitted.
    Invalid market evidence is a failing G1. No caller analysis, score, gate,
    event, timing, zone, or prior passing result is accepted as an input.
    """
    intent = _copy(intent, QualificationIntent)
    policy = _copy(policy, QualificationPrefixPolicy)
    consumed_event_keys = _event_keys(consumed_event_keys)
    now = _time(evaluated_at)
    timing_policy = _copy(TIMING_POLICIES[intent.strategy], TimingPolicy)
    data = evaluate_data(
        market,
        report_id=intent.report_id,
        instrument_id=intent.instrument_id,
        quote=quote,
        reference=reference,
        policy=policy.data,
        evaluated_at=now,
    )
    gates = [data.gate]
    detection = timing = location = None
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
        return QualificationPrefixRun(
            intent=intent,
            policy=policy,
            timing_policy=timing_policy,
            policy_sha256=_policy_digest(policy, timing_policy),
            consumed_event_keys_sha256=_digest(sorted(consumed_event_keys)),
            data_result=data,
            result=EntryQualificationResult(**values, gates=tuple(gates)),
            detection=detection,
            timing=timing,
            location=location,
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
    # Only deserialize the source produced by G1 in THIS invocation. There is no
    # result/hash-only admission path. G1 ignored/rebuilt caller quality already.
    source = json.loads(data.source_json)
    rebuilt = MarketSnapshot.model_validate_json(
        json.dumps(source["market"]), strict=True
    )
    analysis = MultiTimeframeAnalysis.model_validate_json(
        json.dumps(source["analysis"]), strict=True
    )
    collected = validate_collected_quote(quote)
    if collected.bundle_sha256 != data.quote_bundle_sha256:
        raise ValueError("quote changed during qualification")
    executable = collected.quote
    values["symbol"] = rebuilt.symbol
    with localcontext(Context(prec=100)):
        route = route_regime(analysis)
        allowed = (
            route.decision == RouteDecision.ALLOW_SCORING
            and intent.strategy in route.allowed_strategies
        )
        values["market_regime"] = route.regime
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
            },
        ):
            return finish()
        # minimum_risk_reward belongs to the legacy context but none of the
        # shared predicates uses it. No candidate or synthetic RR is built here.
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
        if not gate(
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
        ):
            return finish()
        detection = extract_trigger(
            rebuilt,
            analysis,
            report_id=intent.report_id,
            strategy=intent.strategy,
            direction=intent.direction,
            observed_at=now,
            trigger_ttl_seconds=timing_policy.trigger_ttl_seconds,
        )
        trigger = detection.trigger
        values["trigger"] = trigger
        code = (
            "trigger_source_mismatch"
            if detection.source_sha256 != data.source_sha256
            else detection.fail_codes[0]
            if detection.fail_codes
            else "trigger_missing"
            if trigger is None
            else "trigger_invalidated"
            if trigger.invalidation_reason
            else "trigger_expired"
            if now >= trigger.expires_at
            else "passed"
        )
        if not gate(
            QualificationGate.TRIGGER,
            code,
            "A current setup state is not a new closed-candle trigger event.",
            {
                "source_sha256": detection.source_sha256,
                "setup_time": detection.setup_time.isoformat()
                if detection.setup_time
                else None,
                "trigger_time": trigger.trigger_time.isoformat() if trigger else None,
                "trigger_type": trigger.trigger_type if trigger else None,
                "trigger_expires_at": trigger.expires_at.isoformat()
                if trigger
                else None,
            },
        ):
            return finish()
        reference_price = (
            executable.ask if intent.direction == "long" else executable.bid
        )
        values["reference_price"] = reference_price
        timing = evaluate_timing(
            detection,
            current_time=now,
            candidate_created_at=intent.created_at,
            candidate_expires_at=intent.expires_at,
            reference_price=reference_price,
            consumed_event_keys=consumed_event_keys,
            policy=timing_policy,
        )
        values["entry_timing_state"] = (
            "valid"
            if timing.timing_valid
            else ("wait" if timing.action == "WAIT" else "cancel")
        )
        if not gate(
            QualificationGate.TIMING,
            timing.code,
            timing.reason,
            {
                "policy_id": timing_policy.policy_id,
                "action": timing.action,
                "event_key": timing.event_key,
                "seconds_since_trigger": timing.seconds_since_trigger,
                "candles_since_trigger": timing.candles_since_trigger,
                "deadline": timing.latest_valid_entry_time.isoformat()
                if timing.latest_valid_entry_time
                else None,
                "consumed_event_ledger_authenticated": False,
            },
        ):
            return finish()
        zone, zone_code = build_entry_zone(
            detection,
            tick_size=policy.tick_size,
            max_allowed_drift_bps=policy.max_allowed_drift_bps,
            expires_at=timing.latest_valid_entry_time,
        )
        values["entry_zone"] = zone
        if zone is None:
            gate(
                QualificationGate.LOCATION,
                zone_code,
                "A source-derived executable entry zone could not be built.",
                {"source_sha256": data.source_sha256, "tick_size": policy.tick_size},
            )
            return finish()
        location = evaluate_location(
            report_id=intent.report_id,
            instrument_id=intent.instrument_id,
            direction=intent.direction,
            zone=zone,
            candidate_entry=intent.candidate_entry,
            quote=executable,
            current_time=now,
            max_quote_age_seconds=policy.data.maximum_quote_age_seconds,
        )
        gate(
            QualificationGate.LOCATION,
            location.code,
            location.reason,
            {
                "candidate_entry": intent.candidate_entry,
                "reference_price": location.reference_price,
                "drift_bps": location.drift_bps,
                "quote_sha256": location.quote_sha256,
                "zone_low": zone.zone_low,
                "zone_high": zone.zone_high,
                "invalidation_price": zone.invalidation_price,
            },
        )
        return finish()


def verify_qualification_prefix(run, market, **inputs) -> QualificationPrefixRun:
    """Re-execute from original sources/policy/intent/ledger/time, then compare all.

    A self-signed source, a renamed report, or a structurally valid passing result
    is not sufficient. Verification itself grants no IO/authenticity/permission.
    """
    checked = _copy(run, QualificationPrefixRun)
    replayed = evaluate_qualification_prefix(market, **inputs)
    if checked != replayed:
        raise ValueError("qualification_prefix_replay_mismatch")
    return replayed
