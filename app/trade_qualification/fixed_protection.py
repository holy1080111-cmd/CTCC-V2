"""R3: revalidate one original bracket without selecting or moving new prices.

Original selection is replayed against its original source/audit. Current OHLC
quality, descriptive analysis, ATR and the existing bounded anchor universe are
rebuilt from raw data; caller analysis and quality flags are not proof. The new
source is never passed to the full bracket selector. Original policy values,
entry, anchor, invalidation, SL and TP are not loosened, aligned or repriced.

This is not G13. History append/overlap and intrabar/event survival belong to
R2, current route/required predicates belong to the coordinator, and execution
economics/account/reservation belong to later checks. A fresh quote proves only
a supplied point observation, not an untouched interval or authenticated feed.
The coordinator must pin the supplied data policy and quote to its original run
and fresh collection; this pure API has no source, IO or execution authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.analysis.service import analyze_snapshot_at
from app.domain import analysis as analysis_models
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.indicators.core import atr
from app.market.quality.candles import BAR_SECONDS, candle_closed_at, inspect_candles_at
from app.strategies.structural_protection import (
    _source_anchors,
    select_structural_protection,
)
from app.trade_qualification.data import (
    DataQualificationPolicy,
    _bounded_scalars,
    _indicator_values_valid,
    _market_copy,
    _positive_price,
    _validate_market_quotes,
)
from app.trade_qualification.engine import ProtectionPolicy
from app.trade_qualification.event_models import Digest, StrategyName, TriggerDetection
from app.trade_qualification.events import _copy_source
from app.trade_qualification.location import (
    ExecutableQuote,
    inspect_executable_quote,
    quote_fingerprint,
)
from app.trade_qualification.models import (
    EntryTrigger,
    GateAssessment,
    Measurement,
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.service import _plain
from app.trade_qualification.timing import event_identity

D = Decimal
STEPS = (
    "original_source",
    "original_selection",
    "current_source",
    "quote",
    "fixed_geometry",
    "current_noise",
    "current_liquidity",
    "current_target",
)
Step = Literal[
    "original_source",
    "original_selection",
    "current_source",
    "quote",
    "fixed_geometry",
    "current_noise",
    "current_liquidity",
    "current_target",
]
_ANALYSIS_TYPES = {
    analysis_models.CausalTrendSnapshot,
    analysis_models.CausalStateSnapshot,
    analysis_models.CausalReturnIntervalSnapshot,
    analysis_models.MathematicalCoreComponent,
    analysis_models.MathematicalCoreSnapshot,
    analysis_models.IndicatorSnapshot,
    analysis_models.SwingPoint,
    analysis_models.FairValueGap,
    analysis_models.OrderBlock,
    analysis_models.StructureSnapshot,
    analysis_models.TimeframeAnalysis,
    MultiTimeframeAnalysis,
}
_ERRORS = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    ArithmeticError,
    RecursionError,
)
_MAX_AUDIT = 8 * 1024 * 1024


class FixedProtectionError(ValueError):
    """An input cannot identify a valid bounded fixed-bracket calculation."""


def _time(value):
    if type(value) is not datetime:
        raise FixedProtectionError("exact_aware_datetime_required")
    return require_aware(value)


def _guard(value, depth=0, budget=None):
    if budget is None:
        budget = [100000]
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise FixedProtectionError("fixed_protection_traversal_limit")
    if isinstance(value, BaseModel):
        allowed = _ANALYSIS_TYPES | {
            ProtectionPolicy,
            DataQualificationPolicy,
            TriggerDetection,
            EntryTrigger,
            ExecutableQuote,
            FixedProtectionCheck,
            FixedProtectionResult,
        }
        if type(value) not in allowed:
            raise FixedProtectionError("exact_fixed_protection_model_required")
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise FixedProtectionError("fixed_protection_dirty_model")
        for item in value.__dict__.values():
            _guard(item, depth + 1, budget)
    elif type(value) in (dict, MappingProxyType):
        if len(value) > 64:
            raise FixedProtectionError("fixed_protection_mapping_limit")
        for key, item in value.items():
            _guard(key, depth + 1, budget)
            _guard(item, depth + 1, budget)
    elif type(value) in (tuple, list):
        if len(value) > 2048:
            raise FixedProtectionError("fixed_protection_sequence_limit")
        for item in value:
            _guard(item, depth + 1, budget)
    elif type(value) is Decimal:
        if (
            not value.is_finite()
            or len(value.as_tuple().digits) > 256
            or abs(value.as_tuple().exponent) > 256
        ):
            raise FixedProtectionError("fixed_protection_decimal_limit")
    elif type(value) is datetime:
        _time(value)
    elif type(value) is str:
        if len(value) > _MAX_AUDIT:
            raise FixedProtectionError("fixed_protection_text_limit")
    elif isinstance(value, Enum):
        _guard(value.value, depth + 1, budget)
    elif (
        value is None
        or type(value) is bool
        or (type(value) is int and abs(value) <= 10**40)
    ):
        pass
    else:
        raise FixedProtectionError("fixed_protection_value_invalid")


def _copy(value, expected):
    if type(value) is not expected:
        raise FixedProtectionError("exact_fixed_protection_model_required")
    _guard(value)
    return expected.model_validate(_plain(value), strict=True)


def _canonical(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(raw.encode()) > 2 * _MAX_AUDIT:
        raise FixedProtectionError("fixed_protection_record_too_large")
    return raw


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _constraint_record(value):
    """Bound an output's embedded audit before parsing or fingerprinting it."""
    if value is None:
        return None
    if type(value) is not str or len(value.encode()) > _MAX_AUDIT:
        raise ValueError("fixed_constraints_size_invalid")
    depth, quoted, escaped = 0, False, False
    for char in value:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 24:
                raise ValueError("fixed_constraints_depth_invalid")
        elif char in "]}":
            depth -= 1
    try:
        decoded = json.loads(value)
        _guard(decoded)
        if (
            type(decoded) is not dict
            or not decoded
            or not set(decoded) <= {"noise", "liquidity", "targets"}
            or _canonical(decoded) != value
        ):
            raise ValueError("fixed_constraints_must_be_canonical")
    except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
        raise ValueError("fixed_constraints_invalid") from exc
    return value


def _fraction_record(value):
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _measured_fraction(value):
    """Display at Context100; decisions below retain the exact rational value."""
    return D(value.numerator) / D(value.denominator)


class FixedProtectionCheck(QualificationModel):
    step: Step
    passed: bool
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
    measured_values: Mapping[Text, Measurement] = Field(min_length=1, max_length=32)

    @field_validator("measured_values", mode="before")
    @classmethod
    def decode_measurements(cls, value, info):
        return GateAssessment.decode_decimal_measurements(value, info)

    @field_validator("measured_values")
    @classmethod
    def bounded_measurements(cls, value):
        return GateAssessment.bounded_values(value)

    @field_serializer("measured_values")
    def encode_measurements(self, values, info):
        return GateAssessment.serialize_values(self, values, info)

    @model_validator(mode="after")
    def consistent(self):
        if self.passed != (self.code == "passed"):
            raise ValueError("fixed_protection_step_code_mismatch")
        return self


class FixedProtectionResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    strategy: StrategyName
    direction: Literal["long", "short"]
    observed_at: datetime
    entry: Price
    stop_loss: Price
    take_profit: Price
    tick_size: Price
    policy: ProtectionPolicy
    data_policy: DataQualificationPolicy
    policy_sha256: Digest
    data_policy_sha256: Digest
    original_source_sha256: Digest | None = None
    current_source_sha256: Digest | None = None
    original_audit_sha256: Digest | None = None
    quote_sha256: Digest | None = None
    event_key: Digest
    current_constraints_json: str | None = Field(default=None, max_length=_MAX_AUDIT)
    checks: tuple[FixedProtectionCheck, ...] = Field(min_length=1, max_length=8)
    passed: bool
    code: Text
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    continuation_verified: Literal[False] = False
    conditions_verified: Literal[False] = False
    economics_verified: Literal[False] = False
    atomic_risk_reserved: Literal[False] = False

    _utc = field_validator("observed_at")(_time)
    _constraints = field_validator("current_constraints_json", mode="before")(
        _constraint_record
    )

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "continuation_verified",
        "conditions_verified",
        "economics_verified",
        "atomic_risk_reserved",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("fixed_protection_cannot_grant_authority")
        return value

    @model_validator(mode="after")
    def consistent(self):
        _guard(self)
        if tuple(item.step for item in self.checks) != STEPS[: len(self.checks)] or any(
            not item.passed for item in self.checks[:-1]
        ):
            raise ValueError("fixed_protection_checks_must_stop_on_failure")
        if len(self.checks) < len(STEPS) and self.checks[-1].passed:
            raise ValueError("fixed_protection_result_must_finish_or_fail")
        expected_pass = len(self.checks) == len(STEPS) and all(
            item.passed for item in self.checks
        )
        if self.passed != expected_pass or self.code != self.checks[-1].code:
            raise ValueError("fixed_protection_result_code_mismatch")
        if self.policy_sha256 != _hash(self.policy) or self.data_policy_sha256 != _hash(
            self.data_policy
        ):
            raise ValueError("fixed_protection_policy_pin_mismatch")
        if self.passed and any(
            value is None
            for value in (
                self.original_source_sha256,
                self.current_source_sha256,
                self.original_audit_sha256,
                self.quote_sha256,
                self.current_constraints_json,
            )
        ):
            raise ValueError("fixed_protection_pass_needs_all_pins")
        return self

    @property
    def evaluation_sha256(self):
        """A strict output fingerprint, not source verification or a permit."""
        return _hash(_copy(self, FixedProtectionResult))


def _price(value):
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or len(value.as_tuple().digits) > 40
        or abs(value.as_tuple().exponent) > 20
        or not _positive_price(value)
    ):
        raise FixedProtectionError("fixed_price_contract_invalid")
    return value


def _source(market, supplied_analysis, *, at, data_policy):
    market = _market_copy(market)
    supplied = _copy(supplied_analysis, MultiTimeframeAnalysis)
    if set(market.candles) != set(BAR_SECONDS):
        raise FixedProtectionError("four_timeframes_required")
    _validate_market_quotes(market)
    if (
        not timedelta(0)
        <= at - market.received_at
        <= timedelta(seconds=data_policy.maximum_snapshot_age_seconds)
    ):
        raise FixedProtectionError("source_capture_age_invalid")
    if any(
        not timedelta(0)
        <= at - stamp
        <= timedelta(seconds=data_policy.maximum_quote_age_seconds)
        for stamp in (market.ticker.timestamp, market.order_book.timestamp)
    ):
        raise FixedProtectionError("source_quote_age_invalid")
    quality = {}
    for tf, rows in market.candles.items():
        if not data_policy.minimum_confirmed_bars <= len(rows) <= 1024:
            raise FixedProtectionError("source_bar_count_invalid")
        if any(
            not _positive_price(value)
            for row in rows
            for value in (row.open, row.high, row.low, row.close)
        ):
            raise FixedProtectionError("source_ohlc_precision_invalid")
        quality[tf] = inspect_candles_at(rows, tf, current_time=at)
        if not quality[tf].ok:
            raise FixedProtectionError("source_candle_quality_invalid")
        if any(candle_closed_at(row, tf) > market.received_at for row in rows):
            raise FixedProtectionError("source_candle_after_capture")
        if at - candle_closed_at(rows[-1], tf) > timedelta(
            seconds=BAR_SECONDS[tf] * data_policy.maximum_candle_age_intervals
        ):
            raise FixedProtectionError("source_candle_age_invalid")
    market = market.model_copy(update={"quality": quality})
    analysis = analyze_snapshot_at(
        market, evaluated_at=at, version=data_policy.analysis_version
    )
    if not _indicator_values_valid(analysis, market) or analysis != supplied:
        raise FixedProtectionError("source_analysis_recompute_mismatch")
    return _copy_source(market, analysis, at)


def evaluate_fixed_protection(
    original_market: MarketSnapshot,
    original_analysis: MultiTimeframeAnalysis,
    current_market: MarketSnapshot,
    current_analysis: MultiTimeframeAnalysis,
    *,
    detection: TriggerDetection,
    original_selection_audit_json: str,
    stop_loss: Decimal,
    take_profit: Decimal,
    entry: Decimal,
    policy: ProtectionPolicy,
    tick_size: Decimal,
    data_policy: DataQualificationPolicy,
    quote: ExecutableQuote,
    observed_at: datetime,
) -> FixedProtectionResult:
    """Compare original prices only; current-source history continuity is separate.

    Noise uses the original stop anchor's timeframe ATR, the original stop
    distance, original slippage/buffer policy and the newly observed spread.
    Equal-pivot pool exclusion and nearer opposing targets reuse the selector's
    bounded 15m/1H/4H source universe. Equality clears the same buffer boundary
    as the original selector; a target exactly at a barrier is not beyond it.
    """
    try:
        event = _copy(detection, TriggerDetection)
        chosen_policy = _bounded_scalars(policy, ProtectionPolicy)
        data = _bounded_scalars(data_policy, DataQualificationPolicy)
        now = _time(observed_at)
        entry, stop_loss, take_profit, tick_size = map(
            _price, (entry, stop_loss, take_profit, tick_size)
        )
        if (
            event.trigger is None
            or event.source_sha256 is None
            or event.invalidation_price is None
            or event_identity(event) is None
        ):
            raise FixedProtectionError("original_event_missing")
        if (
            type(original_selection_audit_json) is not str
            or not 0 < len(original_selection_audit_json) <= _MAX_AUDIT
            or len(original_selection_audit_json.encode()) > _MAX_AUDIT
        ):
            raise FixedProtectionError("original_audit_contract_invalid")
    except _ERRORS as exc:
        raise FixedProtectionError("fixed_protection_input_invalid") from exc
    identity = {
        "report_id": event.report_id,
        "instrument_id": event.instrument_id,
        "strategy": event.strategy,
        "direction": event.direction,
        "observed_at": now,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "tick_size": tick_size,
        "policy": chosen_policy,
        "data_policy": data,
        "policy_sha256": _hash(chosen_policy),
        "data_policy_sha256": _hash(data),
        "event_key": event_identity(event),
    }
    evidence, checks, constraints = {}, [], {}

    def check(step, code, measured):
        checks.append(
            FixedProtectionCheck(
                step=step, passed=code == "passed", code=code, measured_values=measured
            )
        )
        return code == "passed"

    def finish():
        return FixedProtectionResult.model_validate(
            _plain(
                {
                    **identity,
                    **evidence,
                    "checks": tuple(checks),
                    "current_constraints_json": _canonical(constraints)
                    if constraints
                    else None,
                    "passed": len(checks) == len(STEPS)
                    and all(c.passed for c in checks),
                    "code": checks[-1].code,
                }
            ),
            strict=True,
        )

    with localcontext(Context(prec=100)):
        try:
            old_market, old_analysis, _, old_sha = _source(
                original_market,
                original_analysis,
                at=event.observed_at,
                data_policy=data,
            )
            if old_sha != event.source_sha256 or (
                old_market.instrument_id,
                old_market.symbol,
            ) != (event.instrument_id, event.symbol):
                raise FixedProtectionError("original_source_pin_mismatch")
            if not event.observed_at <= now < event.trigger.expires_at:
                raise FixedProtectionError("original_event_time_invalid")
            evidence["original_source_sha256"] = old_sha
        except _ERRORS as exc:
            check(
                "original_source",
                "fixed_original_source_invalid",
                {"cause": str(exc)[:512] or "invalid source"},
            )
            return finish()
        check(
            "original_source",
            "passed",
            {"source_sha256": old_sha, "analysis_recomputed": True},
        )
        try:
            original = select_structural_protection(
                event,
                old_market,
                old_analysis,
                observed_at=event.observed_at,
                entry=entry,
                tick_size=tick_size,
                **{k: v for k, v in _plain(chosen_policy).items() if k != "policy_id"},
            )
            if (
                not original.protection_valid
                or original.to_audit_json() != original_selection_audit_json
            ):
                raise FixedProtectionError("original_selection_replay_mismatch")
            selected = original.selected
            if (selected.stop.final_stop, selected.target.final_target) != (
                stop_loss,
                take_profit,
            ):
                raise FixedProtectionError("original_fixed_prices_mismatch")
            evidence["original_audit_sha256"] = hashlib.sha256(
                original_selection_audit_json.encode()
            ).hexdigest()
        except _ERRORS as exc:
            check(
                "original_selection",
                "fixed_original_selection_invalid",
                {"cause": str(exc)[:512] or "invalid selection"},
            )
            return finish()
        check(
            "original_selection",
            "passed",
            {
                "original_audit_sha256": evidence["original_audit_sha256"],
                "entry": entry,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            },
        )
        try:
            new_market, _new_analysis, frames, new_sha = _source(
                current_market, current_analysis, at=now, data_policy=data
            )
            if (new_market.instrument_id, new_market.symbol) != (
                old_market.instrument_id,
                old_market.symbol,
            ) or new_market.received_at < old_market.received_at:
                raise FixedProtectionError("current_source_identity_or_time_invalid")
            evidence["current_source_sha256"] = new_sha
        except _ERRORS as exc:
            check(
                "current_source",
                "fixed_current_source_invalid",
                {"cause": str(exc)[:512] or "invalid source"},
            )
            return finish()
        check(
            "current_source",
            "passed",
            {
                "source_sha256": new_sha,
                "analysis_recomputed": True,
                **{
                    f"{tf}_closed_through": candle_closed_at(rows[-1], tf).isoformat()
                    for tf, rows in frames.items()
                },
                "continuation_verified": False,
            },
        )
        try:
            raw_quote = _bounded_scalars(quote, ExecutableQuote)
            checked_quote, quote_code = inspect_executable_quote(
                raw_quote,
                current_time=now,
                max_quote_age_seconds=data.maximum_quote_age_seconds,
            )
            if checked_quote is None or (
                checked_quote.report_id,
                checked_quote.instrument_id,
            ) != (event.report_id, event.instrument_id):
                raise FixedProtectionError(
                    quote_code if checked_quote is None else "quote_identity_mismatch"
                )
            evidence["quote_sha256"] = quote_fingerprint(checked_quote)
            spread = checked_quote.ask - checked_quote.bid
        except _ERRORS as exc:
            check(
                "quote",
                "fixed_quote_invalid",
                {"cause": str(exc)[:512] or "invalid quote"},
            )
            return finish()
        check(
            "quote",
            "passed",
            {"quote_sha256": evidence["quote_sha256"], "spread": spread},
        )
        long = event.direction == "long"
        thesis = D(
            dict(event.setup_basis).get(
                "invalidation_unrounded", str(event.invalidation_price)
            )
        )
        aligned = all(
            Fraction(value) % Fraction(tick_size) == 0
            for value in (stop_loss, take_profit)
        )
        geometry = (
            stop_loss < min(thesis, event.invalidation_price) < entry < take_profit
            if long
            else take_profit < entry < max(thesis, event.invalidation_price) < stop_loss
        )
        quote_between = all(
            stop_loss < value < take_profit if long else take_profit < value < stop_loss
            for value in (
                checked_quote.bid,
                checked_quote.ask,
                checked_quote.mark_price,
            )
        )
        if not check(
            "fixed_geometry",
            "passed"
            if aligned and geometry and quote_between
            else "fixed_geometry_invalid",
            {
                "tick_aligned": aligned,
                "thesis_invalidation": thesis,
                "quote_within_original_bracket": quote_between,
                "prices_changed": False,
            },
        ):
            return finish()
        anchor = selected.stop.anchor
        volatility = atr(frames[anchor.timeframe], 14)
        friction = (
            Fraction(spread)
            + Fraction(entry) * Fraction(chosen_policy.expected_slippage_bps) / 10000
            + Fraction(tick_size)
        )
        if volatility is None or volatility <= 0:
            check(
                "current_noise",
                "fixed_current_atr_invalid",
                {"timeframe": anchor.timeframe},
            )
            return finish()
        required_buffer_exact = (
            max(
                Fraction(entry) * Fraction(chosen_policy.minimum_buffer_bps) / 10000,
                Fraction(volatility) * Fraction(chosen_policy.atr_buffer_multiplier),
            )
            + friction
        )
        clearance = (
            anchor.anchor_price - stop_loss if long else stop_loss - anchor.anchor_price
        )
        distance = entry - stop_loss if long else stop_loss - entry
        minimum_distance_exact = Fraction(volatility) * Fraction(
            chosen_policy.min_stop_distance_atr
        )
        required_buffer = _measured_fraction(required_buffer_exact)
        minimum_distance = _measured_fraction(minimum_distance_exact)
        enough = (
            Fraction(clearance) >= required_buffer_exact
            and Fraction(distance) >= minimum_distance_exact
        )
        constraints["noise"] = {
            "original_anchor_id": anchor.anchor_id,
            "timeframe": anchor.timeframe,
            "atr": str(volatility),
            "clearance": str(clearance),
            "required_buffer": str(required_buffer),
            "required_buffer_exact": _fraction_record(required_buffer_exact),
            "minimum_stop_distance": str(minimum_distance),
            "minimum_stop_distance_exact": _fraction_record(minimum_distance_exact),
            "actual_stop_distance": str(distance),
        }
        if not check(
            "current_noise",
            "passed" if enough else "fixed_stop_noise_insufficient",
            {
                "timeframe": anchor.timeframe,
                "atr": volatility,
                "anchor_price": anchor.anchor_price,
                "clearance": clearance,
                "required_buffer": required_buffer,
                "stop_distance": distance,
                "minimum_stop_distance": minimum_distance,
                "comparison": "exact_fraction",
            },
        ):
            return finish()
        try:
            stops, targets = _source_anchors(frames, event)
        except _ERRORS as exc:
            check(
                "current_liquidity",
                "fixed_current_anchors_invalid",
                {"cause": str(exc)[:512] or "invalid anchors"},
            )
            return finish()
        pool_records = []
        for pool in (item for item in stops if item.source.startswith("equal_")):
            if pool.atr is None or pool.atr <= 0:
                check(
                    "current_liquidity",
                    "fixed_liquidity_atr_invalid",
                    {"anchor_id": pool.anchor_id},
                )
                return finish()
            buffer_exact = (
                max(
                    Fraction(entry)
                    * Fraction(chosen_policy.minimum_buffer_bps)
                    / 10000,
                    Fraction(pool.atr) * Fraction(chosen_policy.atr_buffer_multiplier),
                )
                + friction
            )
            gap = abs(stop_loss - pool.anchor_price)
            buffer = _measured_fraction(buffer_exact)
            pool_records.append(
                {
                    "anchor_id": pool.anchor_id,
                    "price": str(pool.anchor_price),
                    "required_buffer": str(buffer),
                    "required_buffer_exact": _fraction_record(buffer_exact),
                    "actual_clearance": str(gap),
                    "passed": Fraction(gap) >= buffer_exact,
                }
            )
        constraints["liquidity"] = pool_records
        blocked_pools = [item for item in pool_records if not item["passed"]]
        if not check(
            "current_liquidity",
            "passed" if not blocked_pools else "fixed_stop_liquidity_buffer",
            {
                "pool_count": len(pool_records),
                "blocked_pool_count": len(blocked_pools),
                "first_blocking_anchor": blocked_pools[0]["anchor_id"]
                if blocked_pools
                else None,
            },
        ):
            return finish()
        barriers = [
            item
            for item in targets
            if (item.anchor_price > entry if long else item.anchor_price < entry)
        ]
        nearest = (
            (
                min(item.anchor_price for item in barriers)
                if long
                else max(item.anchor_price for item in barriers)
            )
            if barriers
            else None
        )
        occupied = any(
            item.zone_low is not None and item.zone_low <= entry <= item.zone_high
            for item in targets
        )
        blocked = nearest is not None and (
            nearest < take_profit if long else nearest > take_profit
        )
        constraints["targets"] = [
            {
                "anchor_id": item.anchor_id,
                "price": str(item.anchor_price),
                "known_at": item.known_at.isoformat(),
                "zone_low": str(item.zone_low) if item.zone_low is not None else None,
                "zone_high": str(item.zone_high)
                if item.zone_high is not None
                else None,
            }
            for item in targets
        ]
        check(
            "current_target",
            "fixed_entry_inside_opposing_zone"
            if occupied
            else "fixed_target_intervening_barrier"
            if blocked
            else "passed",
            {
                "nearest_barrier": nearest,
                "original_take_profit": take_profit,
                "target_count": len(targets),
                "entry_inside_opposing_zone": occupied,
                "prices_changed": False,
            },
        )
        return finish()
