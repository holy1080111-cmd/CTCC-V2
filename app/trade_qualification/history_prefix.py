"""Opt-in versioned G1--G7 history-route qualification, never order authority.

The original service and its policy hashes are unchanged. This module uses the
same individual gate evaluators but owns distinct record/policy types. V1 stays
rejected downstream; V2 expansion has explicit evidence and recheck dispatch.
Only structure reversal and volatility expansion gain a replayed history G2;
sweep's unresolved HTF rule stays blocked. No route, score or PASS is input.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext
from fractions import Fraction
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pydantic_core import TzInfo

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import RouteDecision, route_regime
from app.trade_qualification.data import (
    DataQualificationPolicy,
    DataQualificationResult,
    WSReferenceObservation,
    evaluate_data,
)
from app.trade_qualification.event_models import Digest, TriggerDetection
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.location import (
    RANGE_ANCHOR_POLICY,
    LocationResult,
    build_entry_zone,
    build_original_range_anchor_zone,
    evaluate_location,
)
from app.trade_qualification.models import (
    EntryQualificationResult,
    EntryTrigger,
    EntryZone,
    GateAssessment,
    MarketRegime,
    Price,
    QualificationGate,
    QualificationModel,
    Text,
    require_aware,
)
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.range_policy import (
    RANGE_PROTECTION_POLICY,
    range_current_permission,
)
from app.trade_qualification.regime_admission import (
    POLICY_ID as HISTORY_POLICY_ID,
)
from app.trade_qualification.regime_admission import (
    RegimeAdmissionResult,
    evaluate_regime_admission,
)
from app.trade_qualification.reversal_policy import (
    REVERSAL_POLICY,
    reversal_current_permission,
)
from app.trade_qualification.service import QualificationIntent
from app.trade_qualification.timing import (
    TIMING_POLICIES,
    TimingPolicy,
    TimingResult,
    evaluate_timing,
)

CONTRACT_VERSION = "ctcc-history-qualification-prefix-v1"
CONTRACT_VERSION_V2 = "ctcc-history-qualification-prefix-v2"
EXPANSION_HTF_POLICY = "ctcc-expansion-htf-permission-v1"
_HISTORY_STRATEGIES = frozenset(
    ("structure_reversal", "volatility_expansion", "liquidity_sweep_reversal")
)
_ENUM_TYPES = frozenset((QualificationGate, MarketRegime))
D = Decimal
_PREFIX = tuple(QualificationGate)[:7]
_MAX_BYTES = 8 * 1024 * 1024


def _guard_source(value):
    # Late import keeps source guarding independent of evidence's version dispatch.
    from app.trade_qualification.one_shot import _guard_original

    _guard_original(value)


def _time(value):
    if type(value) is not datetime or not any(
        type(value.tzinfo) is allowed for allowed in (timezone, ZoneInfo, TzInfo)
    ):
        raise ValueError("an exact aware datetime with supported timezone is required")
    return require_aware(value)


class HistoryEntryQualificationResult(EntryQualificationResult):
    """New result type; history verification never relabels the old snapshot."""

    contract_version: Literal["ctcc-history-qualification-result-v1"] = (
        "ctcc-history-qualification-result-v1"
    )
    market_regime: MarketRegime | Literal["History Verified Reversal"] = (
        MarketRegime.UNKNOWN
    )
    history_admission_sha256: Digest | None = None
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=11)

    @model_validator(mode="after")
    def history_classification(self):
        history_expected = len(self.gates) >= 2 and self.strategy in _HISTORY_STRATEGIES
        if (self.history_admission_sha256 is not None) != history_expected:
            raise ValueError("history result requires its versioned G2 observation")
        if history_expected and self.history_admission_sha256 != self.gates[
            1
        ].measured_values.get("history_evaluation_sha256"):
            raise ValueError("history classification digest differs from G2")
        if self.market_regime == "History Verified Reversal" and (
            self.strategy != "structure_reversal"
            or not self._passed(QualificationGate.REGIME)
            or self.history_admission_sha256 is None
            or self.gates[1].measured_values.get("regime") != MarketRegime.UNKNOWN.value
        ):
            raise ValueError("history-only reversal classification lacks its G2 basis")
        return self


class HistoryQualificationPrefixPolicy(QualificationModel):
    contract_version: Literal["ctcc-history-qualification-prefix-v1"] = CONTRACT_VERSION
    history_policy_id: Literal["ctcc-history-regime-admission-v1"] = HISTORY_POLICY_ID
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


class HistoryEntryQualificationResultV2(HistoryEntryQualificationResult):
    # Required at every serialized union boundary: a legacy dictionary must
    # never acquire a new contract merely because a subtype supplies defaults.
    contract_version: Literal["ctcc-history-qualification-result-v2"]
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=12)


class HistoryQualificationPrefixPolicyV2(HistoryQualificationPrefixPolicy):
    contract_version: Literal["ctcc-history-qualification-prefix-v2"] = (
        CONTRACT_VERSION_V2
    )
    expansion_htf_policy: Literal["ctcc-expansion-htf-permission-v1"] = (
        EXPANSION_HTF_POLICY
    )


class HistoryEntryQualificationResultV3(HistoryEntryQualificationResult):
    contract_version: Literal["ctcc-history-qualification-result-v3"]
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=12)


class HistoryQualificationPrefixPolicyV3(HistoryQualificationPrefixPolicy):
    contract_version: Literal["ctcc-history-qualification-prefix-v3"]
    reversal_policy: Literal["ctcc-reversal-history-protection-v1"] = REVERSAL_POLICY


class HistoryEntryQualificationResultV4(HistoryEntryQualificationResult):
    contract_version: Literal["ctcc-history-qualification-result-v4"]
    range_anchor_policy: Literal["ctcc-original-range-anchor-v1"]
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def range_only(self):
        if self.strategy != "range_reversal":
            raise ValueError("history_v4_range_only")
        return self


class HistoryQualificationPrefixPolicyV4(HistoryQualificationPrefixPolicy):
    contract_version: Literal["ctcc-history-qualification-prefix-v4"]
    range_anchor_policy: Literal["ctcc-original-range-anchor-v1"]


class HistoryEntryQualificationResultV5(HistoryEntryQualificationResult):
    contract_version: Literal["ctcc-history-qualification-result-v5"]
    range_protection_policy: Literal["ctcc-neutral-range-protection-v1"]
    range_anchor_policy: Literal["ctcc-original-range-anchor-v1"]
    gates: tuple[GateAssessment, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def range_only(self):
        if self.strategy != "range_reversal":
            raise ValueError("history_v5_range_only")
        return self


class HistoryQualificationPrefixPolicyV5(HistoryQualificationPrefixPolicy):
    contract_version: Literal["ctcc-history-qualification-prefix-v5"]
    range_protection_policy: Literal["ctcc-neutral-range-protection-v1"]
    range_anchor_policy: Literal["ctcc-original-range-anchor-v1"]


def expansion_htf_permission(analysis, route, direction):
    """Deterministic existing Expansion route operands, never event detection.

    Callers must separately replay original compression history. This helper
    only evaluates present HTF permission; it neither repairs nor creates history.
    """
    h4, h1, m15 = (analysis.timeframe_analyses[tf] for tf in ("4H", "1H", "15m"))
    opposite = "short" if direction == "long" else "long"
    expected_bos = "up" if direction == "long" else "down"
    math = mathematical_confirmation(analysis, direction)
    return (
        direction in ("long", "short")
        and route.regime == MarketRegime.EXPANSION
        and "breakout_continuation" in route.allowed_strategies
        and h4.directional_bias == direction
        and h1.directional_bias in (direction, "neutral")
        and h1.directional_bias != opposite
        and m15.structure.bos == expected_bos
        and m15.volatility == "high"
        and not analysis.blockers
        and math.status not in ("unstable", "opposed")
        and math.risk_grade != "blocked"
    )


def _bounded(value, depth=0, budget=None):
    """Reject bypass/hidden fields before serializers can erase or traverse them."""
    if budget is None:
        budget = [50000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ValueError("qualification record exceeds the traversal bound")
    if any(
        type(value) is allowed
        for allowed in (
            QualificationIntent,
            RegimeAdmissionResult,
            HistoryQualificationPrefixPolicy,
            HistoryQualificationPrefixRun,
            HistoryQualificationPrefixPolicyV2,
            HistoryQualificationPrefixRunV2,
            HistoryQualificationPrefixPolicyV3,
            HistoryQualificationPrefixRunV3,
            HistoryQualificationPrefixPolicyV4,
            HistoryQualificationPrefixPolicyV5,
            HistoryQualificationPrefixRunV4,
            HistoryQualificationPrefixRunV5,
            DataQualificationPolicy,
            DataQualificationResult,
            HistoryEntryQualificationResult,
            HistoryEntryQualificationResultV2,
            HistoryEntryQualificationResultV3,
            HistoryEntryQualificationResultV4,
            HistoryEntryQualificationResultV5,
            GateAssessment,
            EntryTrigger,
            EntryZone,
            TriggerDetection,
            TimingPolicy,
            TimingResult,
            LocationResult,
        )
    ):
        fields = object.__getattribute__(value, "__dict__")
        extras = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        expected = set(type(value).model_fields)
        if (
            type(fields) is not dict
            or any(type(key) is not str for key in fields)
            or set(fields) != expected
            or extras is not None
            or private is not None
            or type(fields_set) is not set
            or any(type(key) is not str for key in fields_set)
            or not fields_set <= expected
        ):
            raise ValueError("qualification record has hidden or missing fields")
        for item in value.__dict__.values():
            _bounded(item, depth + 1, budget)
    elif any(type(value) is allowed for allowed in (dict, MappingProxyType)):
        if len(value) > 64:
            raise ValueError("qualification mapping exceeds its bound")
        for key, item in value.items():
            _bounded(key, depth + 1, budget)
            _bounded(item, depth + 1, budget)
    elif any(type(value) is allowed for allowed in (tuple, list)):
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
    elif any(type(value) is allowed for allowed in _ENUM_TYPES):
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
    if any(type(value) is allowed for allowed in (dict, MappingProxyType)):
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


class HistoryQualificationPrefixRun(QualificationModel):
    contract_version: Literal["ctcc-history-qualification-prefix-v1"] = CONTRACT_VERSION
    history_admission: RegimeAdmissionResult | None = None
    intent: QualificationIntent
    policy: HistoryQualificationPrefixPolicy
    timing_policy: TimingPolicy
    policy_sha256: Digest
    consumed_event_keys_sha256: Digest
    data_result: DataQualificationResult
    result: HistoryEntryQualificationResult
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
        expected_history = count >= 2 and intent.strategy in _HISTORY_STRATEGIES
        if (self.history_admission is not None) != expected_history:
            raise ValueError("history record must match the visited versioned G2")
        admission = self.history_admission
        if admission is not None:
            if (
                admission.report_id != intent.report_id
                or (
                    admission.instrument_id != intent.instrument_id
                    and not (
                        admission.instrument_id == "unknown"
                        and admission.source_sha256 is None
                        and admission.code
                        in {
                            "confirmed_tail_missing",
                            "insufficient_history",
                            "source_invalid",
                        }
                    )
                )
                or admission.strategy != intent.strategy
                or admission.direction != intent.direction
                or admission.observed_at != result.evaluated_at
                or admission.analysis_version != self.policy.data.analysis_version
                or admission.policy_id != self.policy.history_policy_id
                or admission.source_sha256 not in (None, self.data_result.source_sha256)
                or result.gates[1].passed != admission.admitted
                or result.gates[1].code
                != ("passed" if admission.admitted else admission.code)
                or result.gates[1].measured_values.get("history_evaluation_sha256")
                != admission.evaluation_sha256
                or (
                    admission.admitted
                    and admission.source_sha256 != self.data_result.source_sha256
                )
            ):
                raise ValueError("history G2 identity, source or admission mismatch")
            if self.detection is not None and self.detection != admission.detection:
                raise ValueError("G5 detection differs from replayed history G2")
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
        return _digest(_copy(self, type(self)))


class HistoryQualificationPrefixRunV2(HistoryQualificationPrefixRun):
    contract_version: Literal["ctcc-history-qualification-prefix-v2"] = (
        CONTRACT_VERSION_V2
    )
    policy: HistoryQualificationPrefixPolicyV2
    result: HistoryEntryQualificationResultV2

    @model_validator(mode="after")
    def expansion_policy_bound(self):
        if (
            self.intent.strategy == "volatility_expansion"
            and len(self.result.gates) >= 3
        ):
            measured = self.result.gates[2].measured_values
            if (
                measured.get("expansion_htf_policy") != self.policy.expansion_htf_policy
                or measured.get("history_evaluation_sha256")
                != self.history_admission.evaluation_sha256
                or measured.get("source_sha256") != self.data_result.source_sha256
                or measured.get("analysis_sha256")
                != self.result.gates[1].measured_values.get("analysis_sha256")
            ):
                raise ValueError("history_expansion_htf_source_binding_mismatch")
        return self


class HistoryQualificationPrefixRunV3(HistoryQualificationPrefixRun):
    contract_version: Literal["ctcc-history-qualification-prefix-v3"]
    policy: HistoryQualificationPrefixPolicyV3
    result: HistoryEntryQualificationResultV3

    @model_validator(mode="after")
    def reversal_policy_bound(self):
        if self.intent.strategy != "structure_reversal":
            raise ValueError("history_v3_reversal_only")
        if len(self.result.gates) >= 3:
            measured = self.result.gates[2].measured_values
            if (
                measured.get("reversal_policy") != self.policy.reversal_policy
                or measured.get("history_evaluation_sha256")
                != self.history_admission.evaluation_sha256
                or measured.get("source_sha256") != self.data_result.source_sha256
                or measured.get("analysis_sha256")
                != self.result.gates[1].measured_values.get("analysis_sha256")
            ):
                raise ValueError("history_reversal_htf_source_binding_mismatch")
        return self


class HistoryQualificationPrefixRunV4(HistoryQualificationPrefixRun):
    contract_version: Literal["ctcc-history-qualification-prefix-v4"]
    policy: HistoryQualificationPrefixPolicyV4
    result: HistoryEntryQualificationResultV4

    @model_validator(mode="after")
    def range_policy_bound(self):
        if self.intent.strategy != "range_reversal":
            raise ValueError("history_v4_range_only")
        if self.result.range_anchor_policy != self.policy.range_anchor_policy:
            raise ValueError("range_anchor_policy_mismatch")
        if len(self.result.gates) == 7:
            zone, code = build_original_range_anchor_zone(
                self.detection,
                tick_size=self.policy.tick_size,
                max_allowed_drift_bps=self.policy.max_allowed_drift_bps,
                expires_at=self.timing.latest_valid_entry_time,
            )
            measured = self.result.gates[-1].measured_values
            if (
                zone != self.result.entry_zone
                or measured.get("range_anchor_policy")
                != self.policy.range_anchor_policy
                or measured.get("original_event_key") != self.timing.event_key
                or measured.get("source_sha256") != self.data_result.source_sha256
                or measured.get("zone_sha256") != (_digest(zone) if zone else None)
                or (zone is None and self.result.gates[-1].code != code)
            ):
                raise ValueError("range_anchor_source_zone_binding_mismatch")
        return self


class HistoryQualificationPrefixRunV5(HistoryQualificationPrefixRun):
    contract_version: Literal["ctcc-history-qualification-prefix-v5"]
    policy: HistoryQualificationPrefixPolicyV5
    result: HistoryEntryQualificationResultV5

    @model_validator(mode="after")
    def range_policy_bound(self):
        if self.intent.strategy != "range_reversal":
            raise ValueError("history_v5_range_only")
        if self.result.range_protection_policy != self.policy.range_protection_policy:
            raise ValueError("range_protection_policy_mismatch")
        if (
            len(self.result.gates) >= 3
            and self.result.gates[2].measured_values.get("range_protection_policy")
            != self.policy.range_protection_policy
        ):
            raise ValueError("range_protection_source_binding_mismatch")
        if self.result.range_anchor_policy != self.policy.range_anchor_policy:
            raise ValueError("range_anchor_policy_mismatch")
        if len(self.result.gates) == 7:
            zone, code = build_original_range_anchor_zone(
                self.detection,
                tick_size=self.policy.tick_size,
                max_allowed_drift_bps=self.policy.max_allowed_drift_bps,
                expires_at=self.timing.latest_valid_entry_time,
            )
            measured = self.result.gates[-1].measured_values
            if (
                zone != self.result.entry_zone
                or measured.get("range_anchor_policy")
                != self.policy.range_anchor_policy
                or measured.get("original_event_key") != self.timing.event_key
                or measured.get("source_sha256") != self.data_result.source_sha256
                or measured.get("zone_sha256") != (_digest(zone) if zone else None)
                or (zone is None and self.result.gates[-1].code != code)
            ):
                raise ValueError("range_anchor_source_zone_binding_mismatch")
        return self


def _policy_digest(policy, timing_policy):
    return _digest(
        {
            "prefix": policy.model_dump(mode="json", round_trip=True),
            "timing": timing_policy.model_dump(mode="json", round_trip=True),
        }
    )


def evaluate_history_qualification_prefix(
    market: MarketSnapshot,
    *,
    intent: QualificationIntent,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    policy: HistoryQualificationPrefixPolicy,
    consumed_event_keys: frozenset[str],
    evaluated_at: datetime,
) -> HistoryQualificationPrefixRun:
    if type(policy) is not HistoryQualificationPrefixPolicy:
        raise ValueError("exact v1 history policy required")
    return _evaluate_history_prefix(
        market,
        intent=intent,
        quote=quote,
        reference=reference,
        policy=policy,
        consumed_event_keys=consumed_event_keys,
        evaluated_at=evaluated_at,
    )


def evaluate_history_qualification_prefix_v2(market, *, policy, **inputs):
    if type(policy) is not HistoryQualificationPrefixPolicyV2:
        raise ValueError("exact v2 history policy required")
    return _evaluate_history_prefix(market, policy=policy, **inputs)


def evaluate_history_qualification_prefix_v3(market, *, policy, **inputs):
    if type(policy) is not HistoryQualificationPrefixPolicyV3:
        raise ValueError("exact v3 history policy required")
    return _evaluate_history_prefix(market, policy=policy, **inputs)


def evaluate_history_qualification_prefix_v4(market, *, policy, **inputs):
    if type(policy) is not HistoryQualificationPrefixPolicyV4:
        raise ValueError("exact v4 history policy required")
    return _evaluate_history_prefix(market, policy=policy, **inputs)


def evaluate_history_qualification_prefix_v5(market, *, policy, **inputs):
    if type(policy) is not HistoryQualificationPrefixPolicyV5:
        raise ValueError("exact v4 history policy required")
    return _evaluate_history_prefix(market, policy=policy, **inputs)


def _evaluate_history_prefix(
    market,
    *,
    intent,
    quote,
    reference,
    policy,
    consumed_event_keys,
    evaluated_at,
):
    """Execute the real ordered prefix at one explicit clock and source pin.

    Malformed intent/policy/ledger/time raises before a run can be emitted.
    Invalid market evidence is a failing G1. No caller analysis, score, gate,
    event, timing, zone, or prior passing result is accepted as an input.
    """
    intent = _copy(intent, QualificationIntent)
    version2 = type(policy) is HistoryQualificationPrefixPolicyV2
    version3 = type(policy) is HistoryQualificationPrefixPolicyV3
    version5 = type(policy) is HistoryQualificationPrefixPolicyV5
    version4 = type(policy) is HistoryQualificationPrefixPolicyV4
    if (version4 or version5) and intent.strategy != "range_reversal":
        raise ValueError("history_v4_range_only")
    if version2 and intent.strategy != "volatility_expansion":
        raise ValueError("history_v2_expansion_only")
    if version3 and intent.strategy != "structure_reversal":
        raise ValueError("history_v3_reversal_only")
    policy = _copy(
        policy,
        HistoryQualificationPrefixPolicyV5
        if version5
        else HistoryQualificationPrefixPolicyV4
        if version4
        else HistoryQualificationPrefixPolicyV3
        if version3
        else HistoryQualificationPrefixPolicyV2
        if version2
        else HistoryQualificationPrefixPolicy,
    )
    run_type = (
        HistoryQualificationPrefixRunV5
        if version5
        else HistoryQualificationPrefixRunV4
        if version4
        else HistoryQualificationPrefixRunV3
        if version3
        else HistoryQualificationPrefixRunV2
        if version2
        else HistoryQualificationPrefixRun
    )
    result_type = (
        HistoryEntryQualificationResultV5
        if version5
        else HistoryEntryQualificationResultV4
        if version4
        else HistoryEntryQualificationResultV3
        if version3
        else HistoryEntryQualificationResultV2
        if version2
        else HistoryEntryQualificationResult
    )
    consumed_event_keys = _event_keys(consumed_event_keys)
    now = _time(evaluated_at)
    for source_input in (market, quote, reference):
        _guard_source(source_input)
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
    detection = timing = location = history = None
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
    if version2:
        values["contract_version"] = "ctcc-history-qualification-result-v2"
    if version3:
        values["contract_version"] = "ctcc-history-qualification-result-v3"

    if version5:
        values.update(
            contract_version="ctcc-history-qualification-result-v5",
            range_anchor_policy=RANGE_ANCHOR_POLICY,
            range_protection_policy=RANGE_PROTECTION_POLICY,
        )
    if version4:
        values.update(
            contract_version="ctcc-history-qualification-result-v4",
            range_anchor_policy=RANGE_ANCHOR_POLICY,
        )

    def finish():
        values["history_admission_sha256"] = (
            history.evaluation_sha256 if history is not None else None
        )
        return run_type(
            **(
                {"contract_version": "ctcc-history-qualification-prefix-v5"}
                if version5
                else {"contract_version": "ctcc-history-qualification-prefix-v4"}
                if version4
                else {"contract_version": "ctcc-history-qualification-prefix-v3"}
                if version3
                else {}
            ),
            history_admission=history,
            intent=intent,
            policy=policy,
            timing_policy=timing_policy,
            policy_sha256=_policy_digest(policy, timing_policy),
            consumed_event_keys_sha256=_digest(sorted(consumed_event_keys)),
            data_result=data,
            result=result_type(**values, gates=tuple(gates)),
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
        legacy_allowed = (
            route.decision == RouteDecision.ALLOW_SCORING
            and intent.strategy in route.allowed_strategies
        )
        values["market_regime"] = route.regime
        if intent.strategy in _HISTORY_STRATEGIES:
            history = evaluate_regime_admission(
                rebuilt,
                report_id=intent.report_id,
                strategy=intent.strategy,
                direction=intent.direction,
                observed_at=now,
                analysis_version=policy.data.analysis_version,
            )
            if history.source_sha256 not in (None, data.source_sha256) or (
                history.admitted and history.source_sha256 != data.source_sha256
            ):
                raise ValueError("history source changed during qualification")
            code = "passed" if history.admitted else history.code
            if history.admitted and route.regime == MarketRegime.UNKNOWN:
                if intent.strategy != "structure_reversal":
                    raise ValueError(
                        "no versioned history classification for this strategy"
                    )
                values["market_regime"] = "History Verified Reversal"
        else:
            code = "passed" if legacy_allowed else "regime_strategy_not_allowed"
        if not gate(
            QualificationGate.REGIME,
            code,
            "Versioned history admission or unchanged legacy family route is required.",
            {
                "contract_version": policy.contract_version,
                "regime": route.regime.value,
                "route_decision": route.decision.value,
                "allowed_strategies": ",".join(route.allowed_strategies) or "none",
                "route_diagnostics": ",".join(route.fail_codes) or "none",
                "analysis_sha256": route.snapshot_sha256,
                "source_sha256": data.source_sha256,
                "route_method": "replayed_history" if history else "legacy_snapshot",
                "history_policy_id": history.policy_id if history else None,
                "history_evaluation_sha256": (
                    history.evaluation_sha256 if history else None
                ),
                "legacy_route_allowed": legacy_allowed,
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
        expansion_values = {}
        if version2 and intent.strategy == "volatility_expansion":
            htf_ok = (
                direction_matches
                and history is not None
                and history.admitted
                and expansion_htf_permission(analysis, route, intent.direction)
            )
            expansion_values = {
                "expansion_htf_policy": policy.expansion_htf_policy,
                "history_evaluation_sha256": history.evaluation_sha256,
                "source_sha256": data.source_sha256,
                "analysis_sha256": route.snapshot_sha256,
            }
        if version3:
            htf_ok = (
                direction_matches
                and history is not None
                and history.admitted
                and reversal_current_permission(
                    analysis, rebuilt, route, intent.direction
                )
            )
            expansion_values = {
                "reversal_policy": policy.reversal_policy,
                "history_evaluation_sha256": history.evaluation_sha256,
                "source_sha256": data.source_sha256,
                "analysis_sha256": route.snapshot_sha256,
                "retained_analysis_blockers": ",".join(analysis.blockers) or "none",
            }
        if version5:
            htf_ok = direction_matches and range_current_permission(
                analysis, rebuilt, route, intent.direction
            )
            expansion_values = {
                "range_protection_policy": policy.range_protection_policy,
                "source_sha256": data.source_sha256,
                "analysis_sha256": route.snapshot_sha256,
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
        if history is not None and detection != history.detection:
            raise ValueError("history G2 and G5 event replay mismatch")
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
        zone_builder = (
            build_original_range_anchor_zone
            if version4 or version5
            else build_entry_zone
        )
        zone, zone_code = zone_builder(
            detection,
            tick_size=policy.tick_size,
            max_allowed_drift_bps=policy.max_allowed_drift_bps,
            expires_at=timing.latest_valid_entry_time,
        )
        values["entry_zone"] = zone
        range_values = (
            {
                "range_anchor_policy": policy.range_anchor_policy,
                "original_event_key": timing.event_key,
                "source_sha256": data.source_sha256,
                "zone_sha256": _digest(zone) if zone else None,
            }
            if version4 or version5
            else {}
        )
        if zone is None:
            gate(
                QualificationGate.LOCATION,
                zone_code,
                "A source-derived executable entry zone could not be built.",
                {
                    "source_sha256": data.source_sha256,
                    "tick_size": policy.tick_size,
                    **range_values,
                },
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
                **range_values,
            },
        )
        return finish()


def verify_history_qualification_prefix(
    run, market, **inputs
) -> HistoryQualificationPrefixRun:
    """Re-execute from original sources/policy/intent/ledger/time, then compare all.

    A self-signed source, a renamed report, or a structurally valid passing result
    is not sufficient. Verification itself grants no IO/authenticity/permission.
    """
    checked = _copy(run, HistoryQualificationPrefixRun)
    replayed = evaluate_history_qualification_prefix(market, **inputs)
    if checked != replayed:
        raise ValueError("history_qualification_prefix_replay_mismatch")
    return replayed


def verify_history_qualification_prefix_v2(run, market, **inputs):
    checked = _copy(run, HistoryQualificationPrefixRunV2)
    replayed = evaluate_history_qualification_prefix_v2(market, **inputs)
    if checked != replayed:
        raise ValueError("history_qualification_prefix_replay_mismatch")
    return replayed


def verify_history_qualification_prefix_v3(run, market, **inputs):
    checked = _copy(run, HistoryQualificationPrefixRunV3)
    replayed = evaluate_history_qualification_prefix_v3(market, **inputs)
    if checked != replayed:
        raise ValueError("history_qualification_prefix_replay_mismatch")
    return replayed


def verify_history_qualification_prefix_v4(run, market, **inputs):
    checked = _copy(run, HistoryQualificationPrefixRunV4)
    replayed = evaluate_history_qualification_prefix_v4(market, **inputs)
    if checked != replayed:
        raise ValueError("history_qualification_prefix_replay_mismatch")
    return replayed
