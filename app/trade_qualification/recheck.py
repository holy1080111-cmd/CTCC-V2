"""Replayable offline R1--R4 checks, never a resumed execution permission.

The publication is a recorded fact, the WS/account envelopes are supplied
claims, and merged market receipt does not prove per-timeframe capture. Even
all computational checks passing leaves intrabar coverage unknown and R5--R7
(trusted collection, atomic reservation and runtime execution) unperformed.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Context, DecimalException, localcontext
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.trade_evidence.gates import _digest
from app.trade_evidence.gates import _guard as _evidence_guard
from app.trade_qualification.continuation import (
    ContinuationResult,
    evaluate_continuation,
)
from app.trade_qualification.continuation import (
    _copy_result as _copy_continuation,
)
from app.trade_qualification.contract_dispatch import record_family, record_versions
from app.trade_qualification.current_conditions import (
    CurrentConditionsResult,
    HistoryCurrentConditionsResultV2,
    HistoryCurrentConditionsResultV3,
    HistoryCurrentConditionsResultV4,
    HistoryCurrentConditionsResultV5,
    HistoryCurrentConditionsResultV6,
    copy_current_conditions,
    evaluate_current_conditions,
    evaluate_history_current_conditions_v2,
    evaluate_history_current_conditions_v3,
    evaluate_history_current_conditions_v4,
    evaluate_history_current_conditions_v5,
    evaluate_history_current_conditions_v6,
)
from app.trade_qualification.current_risk import (
    CurrentRiskResult,
    copy_current_risk,
    evaluate_current_risk,
)
from app.trade_qualification.data import (
    WSReferenceObservation,
    _bounded_scalars,
    _market_copy,
)
from app.trade_qualification.engine import PortfolioInputs
from app.trade_qualification.engine import _copy as _copy_risk
from app.trade_qualification.event_models import Digest
from app.trade_qualification.fixed_protection import (
    FixedProtectionResult,
    FixedProtectionResultV3,
    FixedProtectionResultV5,
    FixedProtectionResultV6,
    evaluate_fixed_protection,
    evaluate_fixed_protection_v3,
    evaluate_fixed_protection_v5,
    evaluate_fixed_protection_v6,
)
from app.trade_qualification.fixed_protection import (
    _copy as _copy_protection,
)
from app.trade_qualification.history_engine import (
    HistoryPreEvidenceRunV2,
    HistoryPreEvidenceRunV3,
    HistoryPreEvidenceRunV4,
    HistoryPreEvidenceRunV5,
    HistoryPreEvidenceRunV6,
)
from app.trade_qualification.history_prefix import (
    HistoryEntryQualificationResultV6,
    HistoryQualificationPrefixRunV6,
)
from app.trade_qualification.location import (
    LocationResult,
    evaluate_location,
    quote_fingerprint,
)
from app.trade_qualification.models import QualificationModel, Text, require_aware
from app.trade_qualification.quote_collector import (
    CollectedQuote,
    validate_collected_quote,
)
from app.trade_qualification.recheck_models import (
    RecheckOrigin,
    copy_recheck_origin,
    replay_recheck_origin,
)
from app.trade_qualification.service import _bounded, _event_keys, _plain
from app.trade_qualification.timing import TimingPolicy, TimingResult, evaluate_timing

Step = Literal[
    "origin_replay",
    "capture_barrier",
    "current_conditions",
    "continuation",
    "timing",
    "location",
    "fixed_protection",
    "current_risk",
]
STEPS = (
    "origin_replay",
    "capture_barrier",
    "current_conditions",
    "continuation",
    "timing",
    "location",
    "fixed_protection",
    "current_risk",
)
_ORIGINAL_INPUTS = frozenset(
    (
        "intent",
        "quote",
        "reference",
        "policy",
        "risk_inputs",
        "consumed_event_keys",
        "evaluated_at",
    )
)
_FALSE_FLAGS = (
    "runtime_admissible",
    "execution_authority",
    "source_authenticity_verified",
    "account_evidence_authenticated",
    "atomic_risk_reserved",
    "publication_observed_here",
    "complete_path_verified",
    "execution_recheck_performed",
    "per_timeframe_capture_verified",
    "consumed_event_ledger_authenticated",
    "market_fill_guaranteed",
)
_INVALID = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    OverflowError,
    DecimalException,
)


class _Denied(ValueError):
    pass


def _conditions_family(value):
    return record_family(
        value,
        CurrentConditionsResult,
        HistoryCurrentConditionsResultV2,
        "ctcc-history-current-conditions-v2",
        (
            HistoryCurrentConditionsResultV3,
            "ctcc-history-current-conditions-v3",
            "history_v3",
        ),
        (
            HistoryCurrentConditionsResultV4,
            "ctcc-history-current-conditions-v4",
            "history_v4",
        ),
        (
            HistoryCurrentConditionsResultV5,
            "ctcc-history-current-conditions-v5",
            "history_v5",
        ),
        (
            HistoryCurrentConditionsResultV6,
            "ctcc-history-current-conditions-v6",
            "history_v6",
        ),
    )


RecheckConditions = Annotated[
    Annotated[CurrentConditionsResult, Tag("legacy")]
    | Annotated[HistoryCurrentConditionsResultV2, Tag("history_v2")]
    | Annotated[HistoryCurrentConditionsResultV3, Tag("history_v3")]
    | Annotated[HistoryCurrentConditionsResultV4, Tag("history_v4")]
    | Annotated[HistoryCurrentConditionsResultV5, Tag("history_v5")]
    | Annotated[HistoryCurrentConditionsResultV6, Tag("history_v6")],
    Discriminator(_conditions_family),
]


def _fixed_family(value):
    return record_versions(
        value,
        FixedProtectionResult,
        (
            (FixedProtectionResultV3, "ctcc-history-fixed-protection-v3", "history_v3"),
            (FixedProtectionResultV5, "ctcc-history-fixed-protection-v5", "history_v5"),
            (FixedProtectionResultV6, "ctcc-history-fixed-protection-v6", "history_v6"),
        ),
    )


RecheckProtection = Annotated[
    Annotated[FixedProtectionResult, Tag("legacy")]
    | Annotated[FixedProtectionResultV3, Tag("history_v3")]
    | Annotated[FixedProtectionResultV5, Tag("history_v5")]
    | Annotated[FixedProtectionResultV6, Tag("history_v6")],
    Discriminator(_fixed_family),
]


def _utc(value):
    if type(value) is not datetime:
        raise ValueError("exact_recorded_recheck_clock_required")
    return require_aware(value)


class RecheckCheck(QualificationModel):
    """An ordered calculation record, deliberately not a QualificationGate."""

    step: Step
    passed: bool
    code: Text
    subresult_sha256: Digest | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.passed != (self.code == "passed"):
            raise ValueError("recheck_check_status_mismatch")
        return self


def _guard(value):
    """Bound raw trees and reject undeclared nested types before serialization."""
    copiers = {
        RecheckOrigin: copy_recheck_origin,
        CurrentConditionsResult: copy_current_conditions,
        HistoryCurrentConditionsResultV2: copy_current_conditions,
        HistoryCurrentConditionsResultV3: copy_current_conditions,
        HistoryCurrentConditionsResultV4: copy_current_conditions,
        HistoryCurrentConditionsResultV5: copy_current_conditions,
        HistoryCurrentConditionsResultV6: copy_current_conditions,
        ContinuationResult: _copy_continuation,
        FixedProtectionResult: lambda v: _copy_protection(v, FixedProtectionResult),
        FixedProtectionResultV3: lambda v: _copy_protection(v, FixedProtectionResultV3),
        FixedProtectionResultV5: lambda v: _copy_protection(v, FixedProtectionResultV5),
        FixedProtectionResultV6: lambda v: _copy_protection(v, FixedProtectionResultV6),
        CurrentRiskResult: copy_current_risk,
    }
    if type(value) in copiers:
        copiers[type(value)](value)
    elif isinstance(value, BaseModel) and type(value) in {
        RecordedRecheckAssessment,
        RecheckCheck,
    }:
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("dirty_recorded_recheck")
        if type(value) is RecordedRecheckAssessment and (
            type(value.checks) is not tuple or not 1 <= len(value.checks) <= 8
        ):
            raise ValueError("bounded_recheck_checks_required")
        for item in value.__dict__.values():
            if type(item) is tuple:
                for check in item:
                    if type(check) is not RecheckCheck:
                        raise ValueError("exact_recheck_check_required")
                    _guard(check)
            else:
                _guard(item)
    else:
        # Known evidence/prefix models and bounded plain trees only. Unknown
        # model subclasses, generators, recursive containers and serializers fail.
        _evidence_guard(value)


def _checked_v6_json_output(value, expected):
    """Check declared output fields against strict reconstruction, never ignore.

    Only fixed V6 subcontracts and their timing records use this JSON path.
    Unknown fields stay in the strict input and fail extra-forbid. Comparison
    uses JSON bytes so false cannot be supplied as zero or a truthy string.
    """
    from app.trade_evidence.gates import HistoryEvidenceGateRunV6
    from app.trade_evidence.models import EvidenceSnapshot

    if (
        not any(
            expected is model
            for model in (
                HistoryEntryQualificationResultV6,
                HistoryQualificationPrefixRunV6,
                HistoryPreEvidenceRunV6,
                HistoryEvidenceGateRunV6,
                HistoryCurrentConditionsResultV6,
                TimingPolicy,
                TimingResult,
                EvidenceSnapshot,
                RecheckOrigin,
            )
        )
        or type(value) is not dict
    ):
        raise ValueError("exact_v6_json_output_contract_required")
    computed = expected.model_computed_fields
    supplied = {name: value[name] for name in computed if name in value}
    remaining = {name: item for name, item in value.items() if name not in computed}
    rebuilt = expected.model_validate_json(
        json.dumps(remaining, allow_nan=False), strict=True
    )
    actual = rebuilt.model_dump(mode="json")
    for name, item in supplied.items():
        if json.dumps(item, allow_nan=False, sort_keys=True) != json.dumps(
            actual[name], allow_nan=False, sort_keys=True
        ):
            raise ValueError("v6_json_computed_output_mismatch")
    return json.loads(rebuilt.model_dump_json(round_trip=True))


def _restore_v6_recheck_json_outputs(value):
    """Postorder reconstruction at fixed V6 paths; old/Python inputs untouched."""
    from app.trade_evidence.gates import HistoryEvidenceGateRunV6
    from app.trade_evidence.models import EvidenceSnapshot

    origin = value.get("origin")
    if type(origin) is not dict:
        return value
    evidence = origin.get("evidence")
    if (
        type(evidence) is not dict
        or evidence.get("contract_version") != "ctcc-history-evidence-v6"
    ):
        return value
    pre = evidence.get("pre_evidence")
    prefix = pre.get("prefix") if type(pre) is dict else None
    if (
        type(pre) is not dict
        or pre.get("contract_version") != "ctcc-history-pre-evidence-v6"
        or type(prefix) is not dict
        or prefix.get("contract_version") != "ctcc-history-qualification-prefix-v6"
    ):
        raise ValueError("v6_json_original_version_mismatch")
    value, origin, evidence, pre, prefix = (
        dict(item) for item in (value, origin, evidence, pre, prefix)
    )
    prefix["result"] = _checked_v6_json_output(
        prefix.get("result"), HistoryEntryQualificationResultV6
    )
    prefix["timing_policy"] = _checked_v6_json_output(
        prefix.get("timing_policy"), TimingPolicy
    )
    if prefix.get("timing") is not None:
        prefix["timing"] = _checked_v6_json_output(prefix["timing"], TimingResult)
    pre["prefix"] = _checked_v6_json_output(prefix, HistoryQualificationPrefixRunV6)
    pre["result"] = _checked_v6_json_output(
        pre.get("result"), HistoryEntryQualificationResultV6
    )
    evidence["pre_evidence"] = _checked_v6_json_output(pre, HistoryPreEvidenceRunV6)
    evidence["result"] = _checked_v6_json_output(
        evidence.get("result"), HistoryEntryQualificationResultV6
    )
    snapshot = evidence.get("snapshot")
    if snapshot is not None:
        if type(snapshot) is not dict:
            raise ValueError("exact_v6_json_snapshot_required")
        snapshot = dict(snapshot)
        snapshot["qualification"] = _checked_v6_json_output(
            snapshot.get("qualification"), HistoryEntryQualificationResultV6
        )
        evidence["snapshot"] = _checked_v6_json_output(snapshot, EvidenceSnapshot)
    origin["evidence"] = _checked_v6_json_output(evidence, HistoryEvidenceGateRunV6)
    value["origin"] = _checked_v6_json_output(origin, RecheckOrigin)
    current = value.get("current_conditions")
    if current is not None:
        if type(current) is not dict:
            raise ValueError("exact_v6_json_current_conditions_required")
        current = dict(current)
        current["result"] = _checked_v6_json_output(
            current.get("result"), HistoryEntryQualificationResultV6
        )
        value["current_conditions"] = _checked_v6_json_output(
            current, HistoryCurrentConditionsResultV6
        )
    if value.get("timing") is not None:
        value["timing"] = _checked_v6_json_output(value["timing"], TimingResult)
    return value


class RecordedRecheckAssessment(QualificationModel):
    origin: RecheckOrigin
    observed_at: datetime
    checks: Annotated[tuple[RecheckCheck, ...], Field(min_length=1, max_length=8)]
    computational_checks_passed: bool
    quote_bundle_sha256: Digest | None = None
    quote_sha256: Digest | None = None
    reference_sha256: Digest | None = None
    captured_market_sha256: Digest | None = None
    consumed_event_keys_sha256: Digest | None = None
    current_risk_inputs_sha256: Digest | None = None
    current_conditions: RecheckConditions | None = None
    continuation: ContinuationResult | None = None
    timing: TimingResult | None = None
    location: LocationResult | None = None
    fixed_protection: RecheckProtection | None = None
    current_risk: CurrentRiskResult | None = None
    record_kind: Literal["offline_recorded_recheck_not_runtime_permission"] = (
        "offline_recorded_recheck_not_runtime_permission"
    )
    intrabar_status: Literal["unknown"] = "unknown"
    runtime_admissible: Literal[False] = False
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    account_evidence_authenticated: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False
    publication_observed_here: Literal[False] = False
    complete_path_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    per_timeframe_capture_verified: Literal[False] = False
    consumed_event_ledger_authenticated: Literal[False] = False
    market_fill_guaranteed: Literal[False] = False

    _time = field_validator("observed_at")(_utc)

    @model_validator(mode="wrap")
    @classmethod
    def bounded_raw_input(cls, value, handler, info):
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("exact_recorded_recheck_required")
            _guard(value)
            return handler(value)
        if type(value) is not dict or len(value) > 64:
            raise ValueError("bounded_recorded_recheck_fields_required")
        checks = value.get("checks")
        expected_sequence = list if info.mode == "json" else tuple
        if type(checks) is not expected_sequence or not 1 <= len(checks) <= 8:
            raise ValueError("bounded_recheck_checks_required")
        for key, item in value.items():
            if type(key) is not str or len(key) > 128:
                raise ValueError("invalid_recorded_recheck_field")
            if key == "checks":
                for check in item:
                    _guard(check)
            else:
                _guard(item)
        if info.mode == "json":
            value = _restore_v6_recheck_json_outputs(value)
            # Strict JSON has its own decimal/datetime/tuple decoding rules.
            # Restore ONLY the declared subcontracts through their JSON APIs;
            # Python callers never receive this coercion path.
            value = dict(value)
            models = {
                "origin": RecheckOrigin,
                "current_conditions": HistoryCurrentConditionsResultV6
                if (value.get("current_conditions") or {}).get("contract_version")
                == "ctcc-history-current-conditions-v6"
                else HistoryCurrentConditionsResultV5
                if (value.get("current_conditions") or {}).get("contract_version")
                == "ctcc-history-current-conditions-v5"
                else HistoryCurrentConditionsResultV4
                if (value.get("current_conditions") or {}).get("contract_version")
                == "ctcc-history-current-conditions-v4"
                else HistoryCurrentConditionsResultV3
                if (value.get("current_conditions") or {}).get("contract_version")
                == "ctcc-history-current-conditions-v3"
                else HistoryCurrentConditionsResultV2
                if (value.get("current_conditions") or {}).get("contract_version")
                == "ctcc-history-current-conditions-v2"
                else CurrentConditionsResult,
                "continuation": ContinuationResult,
                "timing": TimingResult,
                "location": LocationResult,
                "fixed_protection": FixedProtectionResultV6
                if (value.get("fixed_protection") or {}).get("contract_version")
                == "ctcc-history-fixed-protection-v6"
                else FixedProtectionResultV5
                if (value.get("fixed_protection") or {}).get("contract_version")
                == "ctcc-history-fixed-protection-v5"
                else FixedProtectionResultV3
                if (value.get("fixed_protection") or {}).get("contract_version")
                == "ctcc-history-fixed-protection-v3"
                else FixedProtectionResult,
                "current_risk": CurrentRiskResult,
            }
            for name, expected in models.items():
                if value.get(name) is not None:
                    value[name] = _plain(
                        expected.model_validate_json(
                            json.dumps(value[name]),
                            strict=True,
                        )
                    )
            value["checks"] = tuple(
                _plain(
                    RecheckCheck.model_validate_json(
                        json.dumps(check),
                        strict=True,
                    )
                )
                for check in checks
            )
            value["observed_at"] = TypeAdapter(datetime).validate_json(
                json.dumps(value.get("observed_at")),
                strict=True,
            )
            # Re-enter a strict PYTHON boundary once every declared JSON
            # subcontract is typed; nested JSON validators must not decode
            # those restored Decimal/datetime values for a second time.
            return cls.model_validate(value, strict=True)
        return handler(value)

    @field_validator(*_FALSE_FLAGS, mode="before")
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("recorded_recheck_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        checks = self.checks
        if (
            tuple(c.step for c in checks) != STEPS[: len(checks)]
            or any(not c.passed for c in checks[:-1])
            or (len(checks) < 8 and checks[-1].passed)
            or self.computational_checks_passed
            != (len(checks) == 8 and checks[-1].passed)
        ):
            raise ValueError("recheck_must_stop_at_first_failure")
        if checks[0].subresult_sha256 != self.origin.evaluation_sha256:
            raise ValueError("recheck_origin_pin_mismatch")
        for index, name in enumerate(STEPS[2:], 2):
            sub = getattr(self, name)
            if index >= len(checks):
                if sub is not None:
                    raise ValueError("recheck_contains_unperformed_check")
                continue
            check = checks[index]
            if sub is None:
                if check.passed or check.subresult_sha256 is not None:
                    raise ValueError("recheck_passing_subresult_missing")
            elif (
                check.subresult_sha256 != _subhash(sub)
                or check.passed != _passed(sub)
                or check.code != sub.code
            ):
                raise ValueError("recheck_subresult_status_mismatch")
        pre = self.origin.evidence.pre_evidence
        intent = pre.prefix.intent
        if len(checks) > 2 and (
            not self.origin.publication_completed_at
            < self.observed_at
            < self.origin.deadline
            or any(
                pin is None
                for pin in (
                    self.quote_bundle_sha256,
                    self.quote_sha256,
                    self.reference_sha256,
                    self.captured_market_sha256,
                )
            )
        ):
            raise ValueError("recheck_capture_pins_missing")
        current = self.current_conditions
        expected_current = (
            HistoryCurrentConditionsResultV6
            if type(pre) is HistoryPreEvidenceRunV6
            else HistoryCurrentConditionsResultV5
            if type(pre) is HistoryPreEvidenceRunV5
            else HistoryCurrentConditionsResultV4
            if type(pre) is HistoryPreEvidenceRunV4
            else HistoryCurrentConditionsResultV3
            if type(pre) is HistoryPreEvidenceRunV3
            else HistoryCurrentConditionsResultV2
            if type(pre) is HistoryPreEvidenceRunV2
            else CurrentConditionsResult
        )
        if current is not None and type(current) is not expected_current:
            raise ValueError("recheck_current_contract_mismatch")
        if (
            current is not None
            and type(pre)
            in (
                HistoryPreEvidenceRunV2,
                HistoryPreEvidenceRunV3,
                HistoryPreEvidenceRunV6,
            )
            and (
                type(current) is not expected_current
                or current.origin_sha256 != self.origin.evaluation_sha256
                or current.original_event_key != self.origin.original_event_key
                or current.history_admission_sha256
                != pre.prefix.history_admission.evaluation_sha256
            )
        ):
            raise ValueError("recheck_history_current_origin_mismatch")
        if (
            current is not None
            and type(pre) in (HistoryPreEvidenceRunV4, HistoryPreEvidenceRunV5)
            and (
                current.origin_sha256 != self.origin.evaluation_sha256
                or current.original_event_key != self.origin.original_event_key
            )
        ):
            raise ValueError("recheck_range_current_origin_mismatch")
        if current is not None and (
            current.intent != intent
            or current.policy != pre.policy.prefix
            or current.result.evaluated_at != self.observed_at
            or (
                current.data_result.passed
                and (
                    current.data_result.quote_bundle_sha256 != self.quote_bundle_sha256
                    or current.data_result.reference_sha256 != self.reference_sha256
                )
            )
        ):
            raise ValueError("recheck_changed_current_conditions_inputs")
        continuation = self.continuation
        if continuation is not None and (
            continuation.report_id != intent.report_id
            or continuation.instrument_id != intent.instrument_id
            or continuation.direction != intent.direction
            or continuation.observed_at != self.observed_at
            or (
                continuation.passed
                and (
                    continuation.original_source_sha256
                    != self.origin.original_source_sha256
                    or continuation.original_event_key != self.origin.original_event_key
                    or continuation.original_trigger_expires_at
                    != pre.prefix.detection.trigger.expires_at
                )
            )
        ):
            raise ValueError("recheck_changed_original_event")
        if self.timing is not None and (
            self.timing.report_id != intent.report_id
            or self.timing.current_time != self.observed_at
            or self.timing.event_key != self.origin.original_event_key
            or self.timing.setup_time != pre.prefix.detection.setup_time
            or self.timing.trigger_time != pre.prefix.detection.trigger.trigger_time
            or self.timing.timing_window_type != pre.prefix.timing_policy.policy_id
            or self.timing.latest_valid_entry_time
            != pre.prefix.timing.latest_valid_entry_time
            or self.consumed_event_keys_sha256 is None
        ):
            raise ValueError("recheck_changed_original_timing")
        if self.location is not None and (
            self.location.report_id != intent.report_id
            or self.location.instrument_id != intent.instrument_id
            or self.location.quote_sha256 != self.quote_sha256
        ):
            raise ValueError("recheck_location_quote_mismatch")
        fixed = self.fixed_protection
        expected_fixed = (
            FixedProtectionResultV6
            if type(pre) is HistoryPreEvidenceRunV6
            else FixedProtectionResultV5
            if type(pre) is HistoryPreEvidenceRunV5
            else FixedProtectionResultV3
            if type(pre) is HistoryPreEvidenceRunV3
            else FixedProtectionResult
        )
        if fixed is not None and (
            type(fixed) is not expected_fixed
            or (
                type(fixed) is FixedProtectionResultV3
                and (
                    fixed.alignment_policy != pre.policy.prefix.reversal_policy
                    or fixed.history_admission_sha256
                    not in (None, pre.prefix.history_admission.evaluation_sha256)
                )
            )
        ):
            raise ValueError("recheck_fixed_contract_mismatch")
        if type(fixed) is FixedProtectionResultV5 and (
            fixed.alignment_policy != pre.policy.prefix.range_protection_policy
            or fixed.range_permission_sha256
            not in (
                None,
                json.loads(pre.protection_audit_json).get("range_permission_sha256"),
            )
        ):
            raise ValueError("recheck_fixed_range_binding_mismatch")
        if type(fixed) is FixedProtectionResultV6 and (
            fixed.alignment_policy != pre.policy.prefix.history_policy_id
            or fixed.sweep_permission_policy_sha256
            != pre.policy.prefix.sweep_permission_policy_sha256
            or fixed.sweep_selection_policy_sha256
            != pre.policy.prefix.sweep_selection_policy_sha256
            or fixed.sweep_permission_sha256
            not in (None, pre.prefix.history_admission.evaluation_sha256)
            or fixed.original_extreme_anchor_id
            not in (
                None,
                json.loads(pre.protection_audit_json).get("original_extreme_anchor_id"),
            )
        ):
            raise ValueError("recheck_fixed_sweep_binding_mismatch")
        if fixed is not None and (
            (fixed.report_id, fixed.instrument_id, fixed.direction)
            != (intent.report_id, intent.instrument_id, intent.direction)
            or (fixed.entry, fixed.stop_loss, fixed.take_profit)
            != (intent.candidate_entry, pre.result.stop_loss, pre.result.take_profit)
            or fixed.observed_at != self.observed_at
            or fixed.event_key != self.origin.original_event_key
            or fixed.policy != pre.policy.protection
            or fixed.data_policy != pre.policy.prefix.data
            or (
                fixed.passed
                and (
                    fixed.quote_sha256 != self.quote_sha256
                    or fixed.current_source_sha256 != current.data_result.source_sha256
                    or fixed.original_source_sha256
                    != self.origin.original_source_sha256
                )
            )
        ):
            raise ValueError("recheck_changed_fixed_bracket")
        risk = self.current_risk
        if risk is not None and (
            risk.origin_sha256 != self.origin.evaluation_sha256
            or risk.observed_at != self.observed_at
            or risk.current_risk_inputs_sha256 != self.current_risk_inputs_sha256
            or risk.original_event_key != self.origin.original_event_key
            or (risk.report_id, risk.instrument_id, risk.direction)
            != (intent.report_id, intent.instrument_id, intent.direction)
            or (risk.original_entry, risk.original_stop_loss, risk.original_take_profit)
            != (intent.candidate_entry, pre.result.stop_loss, pre.result.take_profit)
            or (risk.original_requested_contracts, risk.original_requested_leverage)
            != (pre.risk_inputs.requested_contracts, pre.risk_inputs.requested_leverage)
            or any(
                scenario is not None
                and scenario.quote_sha256 is not None
                and scenario.quote_sha256 != self.quote_sha256
                for scenario in (
                    risk.economics.candidate_result,
                    risk.economics.execution_result,
                )
            )
        ):
            raise ValueError("recheck_current_risk_pin_mismatch")
        return self

    @property
    def code(self):
        return self.checks[-1].code

    @property
    def evaluation_sha256(self):
        return _digest(copy_recorded_recheck(self))


def _subhash(value):
    if type(value) in (TimingResult, LocationResult):
        _bounded(value)
        return _digest(value)
    return value.evaluation_sha256


def _passed(value):
    return value.timing_valid if type(value) is TimingResult else value.passed


def copy_recorded_recheck(result):
    """Consistency only; verify_recorded_recheck replays all actual inputs."""
    if type(result) is not RecordedRecheckAssessment:
        raise ValueError("exact_recorded_recheck_required")
    _guard(result)
    return RecordedRecheckAssessment.model_validate(_plain(result), strict=True)


def _restore(data):
    # Only internally replayed, bounded canonical G1 output reaches this parser.
    source = json.loads(data.source_json)
    return (
        MarketSnapshot.model_validate_json(json.dumps(source["market"]), strict=True),
        MultiTimeframeAnalysis.model_validate_json(
            json.dumps(source["analysis"]), strict=True
        ),
    )


def _capture(origin, current_market, quote, reference, now):
    barrier = origin.publication_completed_at
    if not barrier < now < origin.deadline:
        raise _Denied("outside_original_recheck_window")
    if quote is None or reference is None:
        raise _Denied("new_capture_missing")
    quote = validate_collected_quote(quote)
    if quote.barrier_completed_at != barrier or any(
        p.request_started_at <= barrier for p in quote.provenance
    ):
        raise _Denied("publication_barrier_mismatch")
    if not quote.completed_at < now:
        raise _Denied("decision_not_after_capture_completion")
    market = _market_copy(current_market)
    reference = _bounded_scalars(reference, WSReferenceObservation)
    intent = origin.evidence.pre_evidence.prefix.intent
    if (
        (quote.quote.report_id, quote.quote.instrument_id)
        != (intent.report_id, intent.instrument_id)
        or (reference.report_id, reference.instrument_id)
        != (intent.report_id, intent.instrument_id)
        or market.instrument_id != intent.instrument_id
    ):
        raise _Denied("new_capture_identity_mismatch")
    if not barrier < market.received_at <= now:
        raise _Denied("market_capture_not_after_publication")
    if not (barrier < reference.source_time <= reference.received_at <= now):
        raise _Denied("reference_not_after_publication")
    # A current merged receipt is a claim, not proof of four fresh OHLC GETs.
    # Old confirmed OHLC remains valid overlap, never a post-barrier source ts.
    return market, quote, reference


def _account_claims(risk, origin, now):
    barrier = origin.publication_completed_at
    account_id = origin.evidence.pre_evidence.risk_inputs.account.account_id
    stamps = []
    if risk.account is not None:
        stamps.extend(
            getattr(risk.account, name)
            for name in (
                "balance_stamp",
                "positions_stamp",
                "history_stamp",
                "reservations_stamp",
            )
        )
    if risk.authority is not None:
        stamps.append(risk.authority.stamp)
    if any(s.account_id != account_id or s.environment != "demo" for s in stamps):
        raise _Denied("current_account_scope_changed")
    if risk.instrument is not None:
        stamps.append(risk.instrument)
    if any(not barrier < s.observed_at <= s.received_at <= now for s in stamps):
        raise _Denied("current_account_claim_not_after_publication")


def evaluate_recorded_recheck(
    original_market: MarketSnapshot,
    current_market: MarketSnapshot,
    *,
    origin: RecheckOrigin,
    original_inputs: dict,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    current_risk_inputs: PortfolioInputs,
    consumed_event_keys: frozenset[str],
    observed_at: datetime,
) -> RecordedRecheckAssessment:
    """Actual ordered offline calculations, without new-event/bracket selection.

    Syntactically malformed origin, clock or original-input envelope raises.
    An original replay failure stops before touching new captures. Each later
    bounded denial returns its first check; no later stage repairs an old one.
    Missing current accounts remain missing and are denied by current risk.
    """
    checked = copy_recheck_origin(origin)
    now = _utc(observed_at)
    if (
        type(original_inputs) is not dict
        or len(original_inputs) != 7
        or any(type(key) is not str for key in original_inputs)
        or set(original_inputs) != _ORIGINAL_INPUTS
    ):
        raise ValueError("exact_original_input_envelope_required")
    values = {"origin": checked, "observed_at": now}
    checks = []

    def record(step, sub=None, code="passed"):
        if sub is not None:
            values[step] = sub
            code = sub.code
        checks.append(
            RecheckCheck(
                step=step,
                passed=code == "passed",
                code=code,
                subresult_sha256=(
                    checked.evaluation_sha256
                    if step == "origin_replay"
                    else _subhash(sub)
                    if sub is not None
                    else None
                ),
            )
        )
        return code == "passed"

    def finish():
        return RecordedRecheckAssessment.model_validate(
            _plain(
                dict(
                    **values,
                    checks=tuple(checks),
                    computational_checks_passed=len(checks) == 8 and checks[-1].passed,
                )
            ),
            strict=True,
        )

    # No dependency is given a caller-written PASS or a changed policy/intent.
    with localcontext(Context(prec=100)):
        try:
            replay_recheck_origin(checked, original_market, **original_inputs)
        except _INVALID:
            record("origin_replay", code="original_replay_failed")
            return finish()
        record("origin_replay")
        try:
            market, collected, ref = _capture(
                checked, current_market, quote, reference, now
            )
        except _INVALID as exc:
            record(
                "capture_barrier",
                code=str(exc) if type(exc) is _Denied else "new_capture_invalid",
            )
            return finish()
        values.update(
            quote_bundle_sha256=collected.bundle_sha256,
            quote_sha256=quote_fingerprint(collected.quote),
            reference_sha256=_digest(ref),
            captured_market_sha256=_digest(market),
        )
        record("capture_barrier")
        pre = checked.evidence.pre_evidence
        intent, policy = pre.prefix.intent, pre.policy.prefix
        current = (
            (
                evaluate_history_current_conditions_v6
                if type(pre) is HistoryPreEvidenceRunV6
                else evaluate_history_current_conditions_v5
                if type(pre) is HistoryPreEvidenceRunV5
                else evaluate_history_current_conditions_v4
                if type(pre) is HistoryPreEvidenceRunV4
                else evaluate_history_current_conditions_v3
                if type(pre) is HistoryPreEvidenceRunV3
                else evaluate_history_current_conditions_v2
            )(
                market,
                origin=checked,
                quote=collected,
                reference=ref,
                observed_at=now,
            )
            if type(pre)
            in (
                HistoryPreEvidenceRunV2,
                HistoryPreEvidenceRunV3,
                HistoryPreEvidenceRunV4,
                HistoryPreEvidenceRunV5,
                HistoryPreEvidenceRunV6,
            )
            else evaluate_current_conditions(
                market,
                intent=intent,
                quote=collected,
                reference=ref,
                policy=policy,
                observed_at=now,
            )
        )
        if not record("current_conditions", current):
            return finish()
        old_market, old_analysis = _restore(pre.prefix.data_result)
        new_market, new_analysis = _restore(current.data_result)
        continuation = evaluate_continuation(
            old_market,
            old_analysis,
            new_market,
            detection=pre.prefix.detection,
            observed_at=now,
        )
        if not record("continuation", continuation):
            return finish()
        try:
            keys = _event_keys(consumed_event_keys)
            values["consumed_event_keys_sha256"] = _digest(sorted(keys))
        except _INVALID:
            record("timing", code="consumed_event_ledger_invalid")
            return finish()
        executable = collected.quote
        timing = evaluate_timing(
            pre.prefix.detection,
            current_time=now,
            candidate_created_at=intent.created_at,
            candidate_expires_at=intent.expires_at,
            reference_price=executable.ask
            if intent.direction == "long"
            else executable.bid,
            consumed_event_keys=keys,
            policy=pre.prefix.timing_policy,
        )
        if not record("timing", timing):
            return finish()
        location = evaluate_location(
            report_id=intent.report_id,
            instrument_id=intent.instrument_id,
            direction=intent.direction,
            zone=pre.result.entry_zone,
            candidate_entry=intent.candidate_entry,
            quote=executable,
            current_time=now,
            max_quote_age_seconds=policy.data.maximum_quote_age_seconds,
        )
        if not record("location", location):
            return finish()
        fixed_evaluator = (
            evaluate_fixed_protection_v6
            if type(pre) is HistoryPreEvidenceRunV6
            else evaluate_fixed_protection_v5
            if type(pre) is HistoryPreEvidenceRunV5
            else evaluate_fixed_protection_v3
            if type(pre) is HistoryPreEvidenceRunV3
            else evaluate_fixed_protection
        )
        fixed = fixed_evaluator(
            old_market,
            old_analysis,
            new_market,
            new_analysis,
            detection=pre.prefix.detection,
            original_selection_audit_json=pre.protection_audit_json,
            stop_loss=pre.result.stop_loss,
            take_profit=pre.result.take_profit,
            entry=intent.candidate_entry,
            policy=pre.policy.protection,
            tick_size=policy.tick_size,
            data_policy=policy.data,
            quote=executable,
            observed_at=now,
        )
        if not record("fixed_protection", fixed):
            return finish()
        try:
            risk_inputs = _copy_risk(current_risk_inputs, PortfolioInputs)
            values["current_risk_inputs_sha256"] = _digest(risk_inputs)
            _account_claims(risk_inputs, checked, now)
            risk = evaluate_current_risk(
                checked,
                quote=executable,
                current_risk_inputs=risk_inputs,
                observed_at=now,
            )
        except _INVALID as exc:
            record(
                "current_risk",
                code=str(exc) if type(exc) is _Denied else "current_risk_input_invalid",
            )
            return finish()
        record("current_risk", risk)
        return finish()


def verify_recorded_recheck(result, original_market, current_market, **inputs):
    """Recompute against original and current inputs, never trust a saved PASS."""
    checked = copy_recorded_recheck(result)
    actual = evaluate_recorded_recheck(original_market, current_market, **inputs)
    if checked != actual:
        raise ValueError("recorded_recheck_replay_mismatch")
    return actual
