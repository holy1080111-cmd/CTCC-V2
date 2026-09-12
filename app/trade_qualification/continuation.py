"""Append-only confirmed OHLC proof for one original event, never a recheck gate.

Quality and the original analysis/event are recomputed. Current analysis,
freshness policy, publication barriers and account state belong to the parent
coordinator. Confirmed-bar coverage does not prove an untouched intrabar path.
"""

from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.analysis.service import analyze_snapshot_at
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.market.quality.candles import BAR_SECONDS, candle_closed_at, inspect_candles_at
from app.trade_qualification.data import (
    _canonical,
    _market_copy,
    _sha,
    _validate_market_quotes,
)
from app.trade_qualification.event_models import Digest, StrategyName, TriggerDetection
from app.trade_qualification.events import extract_trigger
from app.trade_qualification.models import (
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)
from app.trade_qualification.service import _bounded, _plain
from app.trade_qualification.timing import TIMING_POLICIES, event_identity

Timeframe = Literal["4H", "1H", "15m", "5m"]
TIMEFRAMES = ("4H", "1H", "15m", "5m")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _utc(value):
    if type(value) is not datetime:
        raise ValueError("continuation requires an exact aware datetime")
    return require_aware(value)


class FrameCoverage(QualificationModel):
    timeframe: Timeframe
    original_count: int = Field(ge=1, le=1024)
    appended_count: int = Field(ge=0, le=1024)
    original_last_close: datetime
    verified_through: datetime
    expected_closed_through: datetime
    blind_from: datetime
    blind_to: datetime
    intrabar_status: Literal["unknown"] = "unknown"

    _times = field_validator(
        "original_last_close",
        "verified_through",
        "expected_closed_through",
        "blind_from",
        "blind_to",
    )(_utc)

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.original_count + self.appended_count > 1024
            or not self.original_last_close <= self.verified_through
            or self.verified_through != self.expected_closed_through
            or self.blind_from != self.verified_through
            or self.blind_to < self.blind_from
            or self.verified_through
            != self.original_last_close
            + timedelta(seconds=self.appended_count * BAR_SECONDS[self.timeframe])
        ):
            raise ValueError("inconsistent confirmed-frame coverage")
        return self


class ContinuationResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    strategy: StrategyName
    direction: Literal["long", "short"]
    observed_at: datetime
    passed: bool
    code: Text
    reason: Text
    detection_sha256: Digest
    original_source_sha256: Digest | None = None
    original_market_sha256: Digest | None = None
    current_market_sha256: Digest | None = None
    original_event_key: Digest | None = None
    original_trigger_expires_at: datetime | None = None
    original_capture_at: datetime | None = None
    current_capture_at: datetime | None = None
    coverage: Annotated[tuple[FrameCoverage, ...], Field(max_length=4)] = ()
    invalidation_timeframe: Timeframe | None = None
    invalidation_known_at: datetime | None = None
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False
    execution_recheck_performed: Literal[False] = False
    complete_path_verified: Literal[False] = False

    _time = field_validator("observed_at")(_utc)

    @field_validator(
        "original_trigger_expires_at",
        "original_capture_at",
        "current_capture_at",
        "invalidation_known_at",
    )
    @classmethod
    def optional_time(cls, value):
        return None if value is None else _utc(value)

    @field_validator(
        "execution_authority",
        "source_authenticity_verified",
        "execution_recheck_performed",
        "complete_path_verified",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError(
                "continuation cannot grant authority or complete-path proof"
            )
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.passed != (self.code == "passed"):
            raise ValueError("continuation status and code disagree")
        if (
            self.coverage
            and tuple(item.timeframe for item in self.coverage) != TIMEFRAMES
        ):
            raise ValueError("coverage must contain the exact ordered four frames")
        if (self.invalidation_known_at is None) != (
            self.invalidation_timeframe is None
        ):
            raise ValueError("invalidation requires both frame and known time")
        if self.invalidation_known_at is not None and (
            self.code != "trigger_invalidated"
            or self.current_capture_at is None
            or self.invalidation_known_at > self.current_capture_at
        ):
            raise ValueError("invalidation exceeds captured knowledge")
        if (
            self.current_capture_at is not None
            and self.current_capture_at > self.observed_at
        ):
            raise ValueError("capture follows continuation evaluation")
        if any(item.blind_to != self.observed_at for item in self.coverage):
            raise ValueError("coverage blind interval uses another observation")
        if self.passed and (
            len(self.coverage) != 4
            or any(
                value is None
                for value in (
                    self.original_source_sha256,
                    self.original_market_sha256,
                    self.current_market_sha256,
                    self.original_event_key,
                    self.original_trigger_expires_at,
                    self.original_capture_at,
                    self.current_capture_at,
                )
            )
            or not self.original_capture_at <= self.current_capture_at
            or self.observed_at >= self.original_trigger_expires_at
        ):
            raise ValueError("passing continuation lacks original current evidence")
        return self

    @property
    def evaluation_sha256(self):
        return _sha(_canonical(_copy_result(self)))


def _same_tree(value, expected, depth=0):
    """Compare against a trusted bounded recomputation before any serializer.

    Exact nested types prevent subclass fields/serializers from disappearing.
    We traverse only the shape already produced by the trusted evaluator.
    """
    if depth > 24 or type(value) is not type(expected):
        raise ValueError("source tree type or depth mismatch")
    if isinstance(expected, BaseModel):
        if (
            set(value.__dict__) != set(type(expected).model_fields)
            or value.__pydantic_extra__
        ):
            raise ValueError("source tree has hidden or missing fields")
        for key in type(expected).model_fields:
            _same_tree(value.__dict__[key], expected.__dict__[key], depth + 1)
    elif type(expected) is dict:
        if value.keys() != expected.keys():
            raise ValueError("source mapping differs")
        for key in expected:
            _same_tree(value[key], expected[key], depth + 1)
    elif type(expected) in (tuple, list):
        if len(value) != len(expected):
            raise ValueError("source sequence differs")
        for left, right in zip(value, expected, strict=True):
            _same_tree(left, right, depth + 1)
    elif type(expected) is datetime:
        if _utc(value) != expected:
            raise ValueError("source instant differs")
    elif value != expected:
        raise ValueError("source value differs")


def _copy_result(value):
    if type(value) is not ContinuationResult:
        raise ValueError("an exact ContinuationResult is required")
    if (
        set(value.__dict__) != set(ContinuationResult.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("dirty continuation result")
    if type(value.coverage) is not tuple or len(value.coverage) > 4:
        raise ValueError("invalid bounded coverage tuple")
    for frame in value.coverage:
        if (
            type(frame) is not FrameCoverage
            or set(frame.__dict__) != set(FrameCoverage.model_fields)
            or frame.__pydantic_extra__
        ):
            raise ValueError("dirty continuation coverage")
    # Only scalar fields and four flat coverage models are supported here.
    for record in (value, *value.coverage):
        for key, item in record.__dict__.items():
            if key == "coverage":
                continue
            if not (item is None or type(item) in (str, int, bool, datetime)):
                raise ValueError("unsupported continuation scalar")
            if type(item) is str and len(item) > 512:
                raise ValueError("continuation text exceeds bounds")
    return ContinuationResult.model_validate(_plain(value), strict=True)


def _prepared_market(value, at):
    market = _market_copy(value)
    _validate_market_quotes(market)
    if set(market.candles) != set(TIMEFRAMES):
        raise ValueError("exactly four timeframes are required")
    if (
        market.received_at > at
        or market.ticker.instrument_id != market.instrument_id
        or market.order_book.instrument_id != market.instrument_id
        or max(market.ticker.timestamp, market.order_book.timestamp)
        > market.received_at
    ):
        raise ValueError("market capture identity or time is invalid")
    quality = {}
    for timeframe in TIMEFRAMES:
        rows = market.candles[timeframe]
        quality[timeframe] = inspect_candles_at(rows, timeframe, current_time=at)
        if not quality[timeframe].ok or any(
            candle_closed_at(row, timeframe) > market.received_at for row in rows
        ):
            raise ValueError("confirmed candle quality rejected")
        if any(
            not (
                Decimal(0) < price < Decimal("1e20")
                and price.as_tuple().exponent >= -20
            )
            for row in rows
            for price in (row.open, row.high, row.low, row.close)
        ):
            raise ValueError("candle price exceeds event bounds")
    return market.model_copy(update={"quality": quality})


def evaluate_continuation(
    original_market: MarketSnapshot,
    original_analysis: MultiTimeframeAnalysis,
    current_market: MarketSnapshot,
    *,
    detection: TriggerDetection,
    observed_at: datetime,
) -> ContinuationResult:
    """Re-prove the original event and its append-only confirmed-bar survival.

    Invalid envelope/detection types raise; invalid source evidence returns a
    deterministic failure. No latest event, renewed expiry or new gate is made.
    """
    now = _utc(observed_at)
    if type(detection) is not TriggerDetection:
        raise ValueError("an exact original TriggerDetection is required")
    _bounded(detection)
    detection = TriggerDetection.model_validate(_plain(detection), strict=True)
    values = {
        "report_id": detection.report_id,
        "instrument_id": detection.instrument_id,
        "strategy": detection.strategy,
        "direction": detection.direction,
        "observed_at": now,
        "detection_sha256": _sha(_canonical(detection)),
    }

    def finish(code, reason):
        return ContinuationResult(
            **values, passed=code == "passed", code=code, reason=reason
        )

    if now < detection.observed_at:
        return finish(
            "capture_order_invalid",
            "Continuation predates the original event observation.",
        )
    with localcontext(Context(prec=100)):
        try:
            if type(original_analysis) is not MultiTimeframeAnalysis:
                raise ValueError("original analysis must be exact")
            at = _utc(original_analysis.generated_at)
            if not at <= detection.observed_at:
                raise ValueError("analysis follows original detection")
            original = _prepared_market(original_market, at)
            analysis = analyze_snapshot_at(
                original, evaluated_at=at, version=original_analysis.version
            )
        except (ValueError, TypeError, AttributeError, ArithmeticError, OverflowError):
            return finish(
                "original_source_invalid",
                "Original raw source cannot be safely rebuilt.",
            )
        try:
            _same_tree(original_analysis, analysis)
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            return finish(
                "original_analysis_mismatch",
                "Original analysis differs from deterministic raw-source analysis.",
            )
        values.update(
            original_market_sha256=_sha(_canonical(original)),
            original_capture_at=original.received_at,
        )
        trigger = detection.trigger
        if detection.fail_codes or trigger is None or trigger.invalidation_reason:
            return finish(
                "original_event_invalid", "Original source event was absent or invalid."
            )
        ttl = trigger.expires_at - trigger.trigger_time
        cap = TIMING_POLICIES[detection.strategy].trigger_ttl_seconds
        if ttl.microseconds or not timedelta(seconds=1) <= ttl <= timedelta(
            seconds=cap
        ):
            return finish(
                "original_event_invalid",
                "Original event lifetime exceeds its fixed timing policy.",
            )
        repeated = extract_trigger(
            original,
            analysis,
            report_id=detection.report_id,
            strategy=detection.strategy,
            direction=detection.direction,
            observed_at=detection.observed_at,
            trigger_ttl_seconds=ttl // timedelta(seconds=1),
        )
        if repeated != detection or repeated.fail_codes:
            return finish(
                "original_event_mismatch",
                "Original event does not replay from the unchanged original source.",
            )
        values.update(
            original_source_sha256=repeated.source_sha256,
            original_event_key=event_identity(repeated),
            original_trigger_expires_at=trigger.expires_at,
        )
        try:
            current = _prepared_market(current_market, now)
        except (ValueError, TypeError, AttributeError, ArithmeticError, OverflowError):
            return finish(
                "current_source_invalid",
                "Current raw source violates bounded confirmed-candle requirements.",
            )
        if (current.instrument_id, current.symbol) != (
            original.instrument_id,
            original.symbol,
        ):
            return finish(
                "source_identity_mismatch",
                "Current and original market identities differ.",
            )
        if current.received_at < original.received_at:
            return finish(
                "capture_order_invalid",
                "Current capture predates the original capture.",
            )
        values.update(
            current_market_sha256=_sha(_canonical(current)),
            current_capture_at=current.received_at,
        )
        coverage = []
        for timeframe in TIMEFRAMES:
            before, after = original.candles[timeframe], current.candles[timeframe]
            if len(after) < len(before):
                return finish(
                    "history_truncated",
                    "Current history omits original confirmed candles.",
                )
            if before != after[: len(before)]:
                return finish(
                    "history_rewritten",
                    "Every original confirmed candle field must be preserved.",
                )
            interval = timedelta(seconds=BAR_SECONDS[timeframe])
            expected = _EPOCH + ((current.received_at - _EPOCH) // interval) * interval
            closed = candle_closed_at(after[-1], timeframe)
            if closed != expected:
                return finish(
                    "closed_tail_missing",
                    "Current source omits a candle already closed at capture.",
                )
            coverage.append(
                FrameCoverage(
                    timeframe=timeframe,
                    original_count=len(before),
                    appended_count=len(after) - len(before),
                    original_last_close=candle_closed_at(before[-1], timeframe),
                    verified_through=closed,
                    expected_closed_through=expected,
                    blind_from=closed,
                    blind_to=now,
                )
            )
        values["coverage"] = tuple(coverage)
        invalidating = [
            (candle_closed_at(row, timeframe), BAR_SECONDS[timeframe], timeframe)
            for timeframe in TIMEFRAMES
            for row in current.candles[timeframe]
            if row.timestamp >= detection.setup_time
            and (
                row.low <= detection.invalidation_price
                if detection.direction == "long"
                else row.high >= detection.invalidation_price
            )
        ]
        if invalidating:
            known, _, timeframe = min(invalidating)
            values.update(invalidation_timeframe=timeframe, invalidation_known_at=known)
            return finish(
                "trigger_invalidated",
                "A fully post-setup confirmed interval touched the original invalidation.",
            )
        if now >= trigger.expires_at:
            return finish(
                "original_event_expired",
                "The original event deadline cannot be renewed by new candles.",
            )
        return finish(
            "passed",
            "Original event survives the append-only confirmed intervals; intrabar path remains unknown.",
        )


def verify_continuation(
    result, original_market, original_analysis, current_market, **inputs
):
    """A self-consistent record/hash is insufficient: re-execute original inputs."""
    checked = _copy_result(result)
    repeated = evaluate_continuation(
        original_market, original_analysis, current_market, **inputs
    )
    if checked != repeated:
        raise ValueError("continuation_replay_mismatch")
    return repeated
