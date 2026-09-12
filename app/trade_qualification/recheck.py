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

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

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
from app.trade_qualification.current_conditions import (
    CurrentConditionsResult,
    copy_current_conditions,
    evaluate_current_conditions,
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
    evaluate_fixed_protection,
)
from app.trade_qualification.fixed_protection import (
    _copy as _copy_protection,
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
from app.trade_qualification.timing import TimingResult, evaluate_timing

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
        ContinuationResult: _copy_continuation,
        FixedProtectionResult: lambda v: _copy_protection(v, FixedProtectionResult),
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
    current_conditions: CurrentConditionsResult | None = None
    continuation: ContinuationResult | None = None
    timing: TimingResult | None = None
    location: LocationResult | None = None
    fixed_protection: FixedProtectionResult | None = None
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
            # Strict JSON has its own decimal/datetime/tuple decoding rules.
            # Restore ONLY the declared subcontracts through their JSON APIs;
            # Python callers never receive this coercion path.
            value = dict(value)
            models = {
                "origin": RecheckOrigin,
                "current_conditions": CurrentConditionsResult,
                "continuation": ContinuationResult,
                "timing": TimingResult,
                "location": LocationResult,
                "fixed_protection": FixedProtectionResult,
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
        current = evaluate_current_conditions(
            market,
            intent=intent,
            quote=collected,
            reference=ref,
            policy=policy,
            observed_at=now,
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
        fixed = evaluate_fixed_protection(
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
