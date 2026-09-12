"""Versioned OHLC history-route evidence, not a replacement G2 or permission.

The legacy router and its hashes are unchanged. An eventual opt-in coordinator
must replay this record AND retain the real G1, G3--G12 and execution boundaries.
Sweep history can be observed, but its unresolved HTF policy remains blocked.
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Context, Decimal, DecimalException, localcontext
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic_core import TzInfo

from app.analysis.service import analyze_snapshot_at
from app.domain.market import MarketSnapshot
from app.exchange.okx.symbols import to_canonical_symbol
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.base import StrategyContext
from app.strategies.conditions import assess_conditions
from app.strategies.mathematical_confirmation import mathematical_confirmation
from app.strategies.regime import route_regime
from app.trade_qualification.continuation import _prepared_market
from app.trade_qualification.data import _MARKET_MODELS, _canonical, _sha
from app.trade_qualification.event_models import Digest, StrategyName, TriggerDetection
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.models import (
    EntryTrigger,
    MarketRegime,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

POLICY_ID = "ctcc-history-regime-admission-v1"
_HISTORY = {
    "structure_reversal": "opposed_trend_structure_break",
    "volatility_expansion": "observed_compression_break",
    "liquidity_sweep_reversal": "sweep_reclaim_with_co_confirmed_structure",
}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INVALID = (ValueError, TypeError, AttributeError, OverflowError, DecimalException)
_Codes = Annotated[tuple[Text, ...], Field(max_length=32)]


def _utc(value):
    if type(value) is not datetime or type(value.tzinfo) not in (
        timezone,
        ZoneInfo,
        TzInfo,
    ):
        raise ValueError("exact datetime with a supported timezone is required")
    return require_aware(value)


def _guard(value, *, source=False, depth=0, budget=None):
    """Bound exact raw types before any serializer or timezone callback.

    Caller quality is intentionally unconsumed: source quality is rebuilt.
    The separate market copier then performs contextual schema validation.
    """
    if budget is None:
        budget = [100000 if source else 2000]
    budget[0] -= 1
    if depth > 12 or budget[0] < 0:
        raise ValueError("regime evidence tree exceeds bounds")
    kind = type(value)
    models = (
        _MARKET_MODELS
        if source
        else (
            RegimeAdmissionResult,
            TriggerDetection,
            EntryTrigger,
        )
    )
    if any(kind is cls for cls in models):
        fields = object.__getattribute__(value, "__dict__")
        extras = object.__getattribute__(value, "__pydantic_extra__")
        private = object.__getattribute__(value, "__pydantic_private__")
        fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
        expected = set(kind.model_fields)
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
            raise ValueError("hidden or missing regime evidence fields")
        if (
            not source
            and kind is RegimeAdmissionResult
            and value.detection is not None
            and type(value.detection) is not TriggerDetection
        ):
            raise ValueError("exact detection required")
        if (
            not source
            and kind is TriggerDetection
            and value.trigger is not None
            and type(value.trigger) is not EntryTrigger
        ):
            raise ValueError("exact trigger required")
        for name, item in fields.items():
            if source and kind is MarketSnapshot and name == "quality":
                continue
            _guard(item, source=source, depth=depth + 1, budget=budget)
    elif kind is dict and source:
        if len(value) > 16 or any(
            type(key) is not str or len(key) > 128 for key in value
        ):
            raise ValueError("source mapping exceeds bounds")
        for item in value.values():
            _guard(item, source=True, depth=depth + 1, budget=budget)
    elif kind is (list if source else tuple):
        if len(value) > (1024 if source else 32):
            raise ValueError("regime evidence sequence exceeds bounds")
        for item in value:
            _guard(item, source=source, depth=depth + 1, budget=budget)
    elif kind is datetime:
        _utc(value)
    elif kind is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 80
            or abs(value.as_tuple().exponent) > 40
        ):
            raise ValueError("regime evidence decimal exceeds bounds")
    elif kind is str:
        if len(value) > 512 or (not source and value != value.strip()):
            raise ValueError("regime evidence text is noncanonical")
    elif value is None or kind is bool or (kind is int and abs(value) <= 10**40):
        pass
    else:
        raise ValueError("undeclared regime evidence type")


class RegimeAdmissionResult(QualificationModel):
    """A replayable policy observation, never a caller-signed admission token."""

    model_config = ConfigDict(str_strip_whitespace=False)
    policy_id: Literal["ctcc-history-regime-admission-v1"] = POLICY_ID
    permission_scope: Literal["history_route_evidence_only"] = (
        "history_route_evidence_only"
    )
    report_id: ReportId
    instrument_id: Text
    strategy: StrategyName
    direction: Literal["long", "short"]
    observed_at: datetime
    analysis_version: str = Field(min_length=1, max_length=64)
    admitted: bool
    code: Text
    source_sha256: Digest | None = None
    snapshot_sha256: Digest | None = None
    snapshot_regime: Text | None = None
    snapshot_allowed_strategies: _Codes = ()
    snapshot_fail_codes: _Codes = ()
    analysis_blockers: _Codes = ()
    required_failures: _Codes = ()
    veto_failures: _Codes = ()
    condition_score: int | None = Field(default=None, ge=0, le=100)
    detection: TriggerDetection | None = None
    event_key: Digest | None = None
    history_verified: bool = False
    execution_authority: Literal[False] = False
    runtime_admissible: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    qualification_performed: Literal[False] = False
    complete_path_verified: Literal[False] = False
    strategy_calibrated: Literal[False] = False

    _clock = field_validator("observed_at")(_utc)

    @field_validator(
        "execution_authority",
        "runtime_admissible",
        "source_authenticity_verified",
        "qualification_performed",
        "complete_path_verified",
        "strategy_calibrated",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("history evidence cannot grant authority or calibration")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.admitted != (self.code == "passed"):
            raise ValueError("admission status and code disagree")
        if self.detection is not None:
            detection = self.detection
            if any(
                getattr(detection, key) != getattr(self, key)
                for key in (
                    "report_id",
                    "instrument_id",
                    "strategy",
                    "direction",
                    "observed_at",
                    "source_sha256",
                )
            ) or self.event_key != event_identity(detection):
                raise ValueError("history source or identity pin differs")
            if detection.trigger is not None and (
                detection.trigger.expires_at
                != detection.trigger.trigger_time
                + timedelta(seconds=TIMING_POLICIES[self.strategy].trigger_ttl_seconds)
            ):
                raise ValueError("history event expiry differs from fixed policy")
        elif self.history_verified or self.event_key is not None:
            raise ValueError("history claim requires a detection")
        if self.history_verified and (
            self.detection.trigger is None
            or self.detection.fail_codes
            or self.detection.setup_type != _HISTORY.get(self.strategy)
            or self.observed_at >= self.detection.trigger.expires_at
            or self.detection.setup_time is None
            or self.detection.trigger.trigger_time - self.detection.setup_time
            > timedelta(
                seconds=TIMING_POLICIES[self.strategy].max_setup_to_trigger_seconds
            )
        ):
            raise ValueError("history claim lacks an unexpired verified event")
        if self.admitted and (
            not self.history_verified
            or self.strategy not in {"structure_reversal", "volatility_expansion"}
            or self.source_sha256 is None
            or self.snapshot_sha256 is None
            or self.required_failures
            or self.veto_failures
        ):
            raise ValueError("admission lacks required historical evidence")
        return self

    @property
    def evaluation_sha256(self):
        return _sha(_canonical(validate_regime_admission(self)))


def validate_regime_admission(value: RegimeAdmissionResult) -> RegimeAdmissionResult:
    """Check immutable record shape, not authenticity or evaluator execution."""
    if type(value) is not RegimeAdmissionResult:
        raise ValueError("exact regime admission result required")
    _guard(value)
    return RegimeAdmissionResult.model_validate(
        value.model_dump(round_trip=True), strict=True
    )


def evaluate_regime_admission(
    market: MarketSnapshot,
    *,
    report_id: str,
    strategy: str,
    direction: Literal["long", "short"],
    observed_at: datetime,
    analysis_version: str,
) -> RegimeAdmissionResult:
    """Rebuild source and assess bounded route history without changing G2.

    No fresh executable quote, candidate, score threshold, account or order
    authority is inferred here. An existing full qualification consumer still
    has to run each of those independent gates against its original policies.
    """
    now = _utc(observed_at)
    # Validate envelope before inspecting any untrusted source attribute.
    envelope = RegimeAdmissionResult(
        report_id=report_id,
        instrument_id="unknown",
        strategy=strategy,
        direction=direction,
        observed_at=now,
        analysis_version=analysis_version,
        admitted=False,
        code="source_invalid",
    )
    if analysis_version != analysis_version.strip():
        raise ValueError("analysis version must be canonical")
    values = envelope.model_dump(round_trip=True)

    def finish(code):
        values.update(admitted=code == "passed", code=code)
        return RegimeAdmissionResult.model_validate(values, strict=True)

    try:
        if type(market) is not MarketSnapshot:
            raise ValueError("exact market required")
        _guard(market, source=True)
        checked = _prepared_market(market, now)
        if checked.symbol != to_canonical_symbol(checked.instrument_id):
            raise ValueError("symbol differs from instrument")
        for timeframe, rows in checked.candles.items():
            if len(rows) < 200:
                return finish("insufficient_history")
            seconds = BAR_SECONDS[timeframe]
            expected_close = _EPOCH + timedelta(
                seconds=((now - _EPOCH) // timedelta(seconds=seconds)) * seconds
            )
            if candle_closed_at(rows[-1], timeframe) != expected_close:
                return finish("confirmed_tail_missing")
        with localcontext(Context(prec=100)):
            analysis = analyze_snapshot_at(
                checked, evaluated_at=now, version=analysis_version
            )
            route = route_regime(analysis)
        values.update(
            instrument_id=checked.instrument_id,
            snapshot_sha256=route.snapshot_sha256,
            snapshot_regime=route.regime.value,
            snapshot_allowed_strategies=route.allowed_strategies,
            snapshot_fail_codes=route.fail_codes,
            analysis_blockers=tuple(analysis.blockers),
        )
    except _INVALID:
        return finish("source_invalid")
    if strategy not in _HISTORY:
        return finish("legacy_route_only")
    policy = TIMING_POLICIES[strategy]
    detection = extract_trigger(
        checked,
        analysis,
        report_id=report_id,
        strategy=strategy,
        direction=direction,
        observed_at=now,
        trigger_ttl_seconds=policy.trigger_ttl_seconds,
    )
    values.update(
        source_sha256=detection.source_sha256,
        detection=detection.model_dump(round_trip=True),
        event_key=event_identity(detection),
    )
    if detection.fail_codes:
        return finish(detection.fail_codes[0])
    if detection.trigger is None or detection.setup_time is None:
        return finish("trigger_missing")
    if detection.setup_type != _HISTORY[strategy]:
        return finish("history_type_mismatch")
    if now >= detection.trigger.expires_at:
        return finish("trigger_expired")
    if detection.trigger.trigger_time - detection.setup_time > timedelta(
        seconds=policy.max_setup_to_trigger_seconds
    ):
        return finish("setup_expired")
    values["history_verified"] = True
    if strategy == "liquidity_sweep_reversal":
        return finish("sweep_htf_policy_unspecified")
    if any(code != "multi_timeframe_not_aligned" for code in analysis.blockers):
        return finish("analysis_blocked")
    if route.regime in (
        MarketRegime.RISK_OFF,
        MarketRegime.HIGH_VOLATILITY,
        MarketRegime.COMPRESSION,
    ):
        return finish("regime_safety_blocked")
    with localcontext(Context(prec=100)):
        math = mathematical_confirmation(analysis, direction)
        if math.status in {"unstable", "opposed"} or math.risk_grade == "blocked":
            return finish("mathematical_veto")
        assessment = assess_conditions(
            StrategyContext(analysis, checked, 0, Decimal(2)), strategy
        )
    values.update(
        required_failures=assessment.required_failures,
        veto_failures=assessment.veto_failures,
        condition_score=assessment.score,
    )
    if assessment.direction != direction:
        return finish("direction_mismatch")
    if assessment.required_failures or assessment.veto_failures:
        return finish("required_conditions_failed")
    if strategy == "volatility_expansion" and (
        route.regime != MarketRegime.EXPANSION
        or "breakout_continuation" not in route.allowed_strategies
    ):
        return finish("expansion_htf_permission_missing")
    return finish("passed")


def verify_regime_admission(
    result: RegimeAdmissionResult, market: MarketSnapshot, **inputs
):
    """Replay original raw inputs; a self-consistent result/hash is not proof."""
    checked = validate_regime_admission(result)
    replayed = evaluate_regime_admission(market, **inputs)
    if checked != replayed:
        raise ValueError("regime_admission_replay_mismatch")
    return replayed
