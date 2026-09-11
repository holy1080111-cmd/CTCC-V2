"""Pure G1 data qualification, not a runtime collector or trading permit.

OHLC quality and analysis are rebuilt under one explicit clock. Legacy merged
mark/funding fields do not prove freshness; a separately validated public quote
is mandatory. The WS reference is a trusted-adapter input contract, not proof
that a websocket actually delivered it. No runtime WS adapter is wired here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
from fractions import Fraction
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.analysis.service import analyze_snapshot_at
from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import Candle, MarketSnapshot, OrderBook, OrderBookLevel, Ticker
from app.exchange.okx.symbols import to_canonical_symbol
from app.market.quality.candles import BAR_SECONDS, candle_closed_at, inspect_candles_at
from app.trade_qualification.event_models import Digest
from app.trade_qualification.location import (
    ExecutableQuote,
    _revalidate,
    inspect_executable_quote,
)
from app.trade_qualification.models import (
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

D = Decimal
TIMEFRAMES = ("4H", "1H", "15m", "5m")
MAX_SOURCE_BYTES = 8 * 1024 * 1024
Bps = Annotated[Decimal, Field(ge=0, le=1000, max_digits=30, decimal_places=20)]
_INVALID = (ValueError, TypeError, AttributeError, OverflowError, DecimalException)
_MARKET_MODELS = (MarketSnapshot, Candle, Ticker, OrderBook, OrderBookLevel)


def _utc(value):
    if type(value) is not datetime:
        raise ValueError("an exact aware datetime is required")
    return require_aware(value)


class DataQualificationPolicy(QualificationModel):
    policy_id: Text
    analysis_version: Annotated[str, Field(min_length=1, max_length=64)]
    minimum_confirmed_bars: int = Field(ge=200, le=1024)
    maximum_snapshot_age_seconds: int = Field(ge=1, le=60)
    maximum_quote_age_seconds: int = Field(ge=1, le=60)
    maximum_reference_age_seconds: int = Field(ge=1, le=60)
    maximum_candle_age_intervals: int = Field(ge=1, le=3)
    maximum_reference_conflict_bps: Bps
    maximum_mark_dislocation_bps: Bps
    maximum_spread_bps: Bps
    maximum_absolute_funding_bps: Bps


class WSReferenceObservation(QualificationModel):
    """Adapter-supplied observations; source labels/hashes do not authenticate IO."""

    report_id: ReportId
    instrument_id: Text
    source: Literal["ws"] = "ws"
    venue: Literal["OKX"] = "OKX"
    channel: Literal["tickers"] = "tickers"
    bid: Price
    ask: Price
    source_time: datetime
    received_at: datetime

    _times = field_validator("source_time", "received_at")(_utc)

    @model_validator(mode="after")
    def causal_quote(self):
        if self.bid >= self.ask:
            raise ValueError("reference quote must have positive spread")
        if self.source_time > self.received_at:
            raise ValueError("reference source is after receipt")
        return self


class DataQualificationResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    evaluated_at: datetime
    gate: GateAssessment
    policy: DataQualificationPolicy | None = None
    policy_sha256: Digest | None = None
    source_sha256: Digest | None = None
    source_json: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=False),
            Field(max_length=MAX_SOURCE_BYTES),
        ]
        | None
    ) = None
    quote_bundle_sha256: Digest | None = None
    reference_sha256: Digest | None = None
    execution_authority: Literal[False] = False
    source_authenticity_verified: Literal[False] = False

    _time = field_validator("evaluated_at")(_utc)

    @field_validator(
        "execution_authority", "source_authenticity_verified", mode="before"
    )
    @classmethod
    def no_authority(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError(
                "G1 data consistency cannot grant source or order authority"
            )
        return value

    @model_validator(mode="after")
    def consistent_record(self):
        if (
            self.gate.gate != QualificationGate.DATA
            or self.gate.report_id != self.report_id
        ):
            raise ValueError("G1 result identity mismatch")
        if self.gate.passed and any(
            value is None
            for value in (
                self.policy,
                self.policy_sha256,
                self.source_json,
                self.source_sha256,
                self.quote_bundle_sha256,
                self.reference_sha256,
            )
        ):
            raise ValueError("passing G1 requires complete source and policy pins")
        if self.source_json is not None:
            raw = self.source_json.encode("utf-8")
            if len(raw) > MAX_SOURCE_BYTES or _sha(raw) != self.source_sha256:
                raise ValueError("source bytes or digest mismatch")
        if (
            self.policy is not None
            and _sha(_canonical(self.policy)) != self.policy_sha256
        ):
            raise ValueError("data policy digest mismatch")
        return self

    @property
    def passed(self):
        return self.gate.passed

    @property
    def evaluation_sha256(self):
        """Bind a strictly reconstructed record, not proof that G1 was executed."""
        return _sha(_canonical(_validated_record(self)))


def _validated_record(value):
    if type(value) is not DataQualificationResult:
        raise ValueError("exact G1 result required")
    if value.source_json is not None and (
        type(value.source_json) is not str or len(value.source_json) > MAX_SOURCE_BYTES
    ):
        raise ValueError("source JSON exceeds the result contract")
    _revalidate(value.gate, GateAssessment)
    if value.policy is not None:
        _bounded_scalars(value.policy, DataQualificationPolicy)
    return _revalidate(value, DataQualificationResult)


def verify_data_result(
    result, market, *, report_id, instrument_id, quote, reference, policy, evaluated_at
):
    """Replay G1 against original inputs; self-signed result/hash is insufficient.

    A future consumer must use this replay boundary, not ``result.passed`` or a
    digest alone. The replay still cannot authenticate caller-supplied sources.
    """
    checked = _validated_record(result)
    replayed = evaluate_data(
        market,
        report_id=report_id,
        instrument_id=instrument_id,
        quote=quote,
        reference=reference,
        policy=policy,
        evaluated_at=evaluated_at,
    )
    if checked != replayed:
        raise ValueError("data_replay_mismatch")
    return replayed


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _market_copy(market: MarketSnapshot) -> MarketSnapshot:
    """Bound exact fields BEFORE serializers can drop hidden keys or consume IO.

    Caller quality is deliberately ignored, never serialized or traversed. All
    quality used downstream is recomputed. This boundary accepts no iterators,
    model subclasses or undeclared fields anywhere in the consumed raw source.
    """
    if type(market) is not MarketSnapshot:
        raise ValueError("exact market source required")
    budget = [100000]

    def copy(value, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 8:
            raise ValueError("source tree exceeds bounds")
        if type(value) in _MARKET_MODELS:
            expected = set(type(value).model_fields)
            if set(value.__dict__) != expected or value.__pydantic_extra__:
                raise ValueError("source fields differ from schema")
            return {
                name: {}
                if type(value) is MarketSnapshot and name == "quality"
                else copy(value.__dict__[name], depth + 1)
                for name in type(value).model_fields
            }
        if type(value) is dict:
            if len(value) > 16 or any(
                type(k) is not str or len(k) > 128 for k in value
            ):
                raise ValueError("source mapping exceeds bounds")
            return {key: copy(item, depth + 1) for key, item in value.items()}
        if type(value) is list:
            if len(value) > 1024:
                raise ValueError("source list exceeds bounds")
            return [copy(item, depth + 1) for item in value]
        if type(value) is datetime:
            return _utc(value)
        if type(value) is Decimal:
            if (
                not value.is_finite()
                or len(value.as_tuple().digits) > 80
                or abs(value.as_tuple().exponent) > 40
                or abs(value) >= D("1e40")
            ):
                raise ValueError("source decimal exceeds bounds")
            return value
        if type(value) is str and len(value) <= 512:
            return value
        if (
            value is None
            or type(value) is bool
            or (type(value) is int and abs(value) <= 10**40)
        ):
            return value
        raise ValueError("invalid source value")

    raw = copy(market)
    checked = MarketSnapshot.model_validate(raw, strict=True)
    for levels in (checked.order_book.bids, checked.order_book.asks):
        if not 1 <= len(levels) <= 50:
            raise ValueError("book depth out of bounds")
    if len(_canonical(checked)) > MAX_SOURCE_BYTES:
        raise ValueError("source bytes exceed bounds")
    return checked


def _positive_price(value):
    return D(0) < value < D("1e20") and value.as_tuple().exponent >= -20


def _bounded_scalars(value, expected):
    """Pydantic digit limits ignore trailing zeros; bound raw representation too."""
    if (
        type(value) is not expected
        or set(value.__dict__) != set(expected.model_fields)
        or value.__pydantic_extra__
    ):
        raise ValueError("exact scalar contract required")
    for item in value.__dict__.values():
        if type(item) is Decimal:
            if (
                not item.is_finite()
                or len(item.as_tuple().digits) > 80
                or abs(item.as_tuple().exponent) > 40
            ):
                raise ValueError("decimal representation exceeds bounds")
        elif type(item) is str:
            if len(item) > 512:
                raise ValueError("scalar text exceeds bounds")
        elif type(item) is int:
            if abs(item) > 10**40:
                raise ValueError("integer exceeds bounds")
        elif item is not None and type(item) not in (bool, datetime):
            raise ValueError("unexpected scalar type")
    return _revalidate(value, expected)


def _validate_market_quotes(market):
    ticker, book = market.ticker, market.order_book
    if not all(
        _positive_price(getattr(ticker, name))
        for name in (
            "last",
            "bid",
            "ask",
            "bid_size",
            "ask_size",
            "open_24h",
            "high_24h",
            "low_24h",
        )
    ):
        raise ValueError("invalid ticker number")
    if ticker.bid >= ticker.ask or ticker.low_24h > ticker.high_24h:
        raise ValueError("invalid ticker geometry")
    if (
        min(
            ticker.volume_24h,
            ticker.volume_quote_24h,
            market.open_interest_contracts,
            market.open_interest_currency,
        )
        < 0
    ):
        raise ValueError("negative market volume")
    for levels, descending in ((book.bids, True), (book.asks, False)):
        for level in levels:
            if not _positive_price(level.price) or not _positive_price(level.size):
                raise ValueError("invalid book level")
            if level.order_count < 0 or level.deprecated_liquidated_orders < 0:
                raise ValueError("negative book count")
        for left, right in pairwise(levels):
            if not (
                left.price > right.price if descending else left.price < right.price
            ):
                raise ValueError("unordered book")
    if book.bids[0].price >= book.asks[0].price:
        raise ValueError("crossed book")


def _indicator_values_valid(analysis, market):
    def finite_tree(value):
        if isinstance(value, BaseModel):
            return all(finite_tree(item) for item in value.__dict__.values())
        if type(value) is dict:
            return all(finite_tree(item) for item in value.values())
        if type(value) in (list, tuple):
            return all(finite_tree(item) for item in value)
        if type(value) is Decimal:
            return value.is_finite()
        return type(value) is not float

    if not finite_tree(analysis):
        return False
    required = (
        "ema20",
        "ema50",
        "ema200",
        "atr14",
        "atr_pct",
        "rsi14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "adx14",
        "vwap",
        "volume_ratio20",
    )
    for tf in TIMEFRAMES:
        view = analysis.timeframe_analyses[tf]
        rows = [row for row in market.candles[tf] if row.confirmed]
        if (
            view.timeframe != tf
            or view.last_closed_at != candle_closed_at(rows[-1], tf)
            or view.close != rows[-1].close
            or view.candle_count != len(rows)
        ):
            return False
        indicators = view.indicators
        if any(
            type(getattr(indicators, name)) is not Decimal
            or not getattr(indicators, name).is_finite()
            for name in required
        ):
            return False
        if any(
            getattr(indicators, name) is None
            for name in ("causal_trend", "causal_state", "return_interval")
        ):
            return False
        if any(
            getattr(indicators, name) <= 0
            for name in ("ema20", "ema50", "ema200", "atr14", "atr_pct", "vwap")
        ):
            return False
        if not 0 <= indicators.rsi14 <= 100 or not 0 <= indicators.adx14 <= 100:
            return False
        if indicators.volume_ratio20 < 0:
            return False
    return True


def evaluate_data(
    market: MarketSnapshot,
    *,
    report_id: str,
    instrument_id: str,
    quote: CollectedQuote | None,
    reference: WSReferenceObservation | None,
    policy: DataQualificationPolicy | None,
    evaluated_at: datetime,
) -> DataQualificationResult:
    """Recompute G1 only. A pass does not imply any later gate or authentic IO.

    No caller analysis is accepted. Missing WS evidence is a failure, not an
    inferred REST/WS agreement. Legacy mark/funding/next-settlement fields are
    retained in source history but never used as current component observations.
    """
    identity = {
        "report_id": TypeAdapter(ReportId).validate_python(report_id, strict=True),
        "instrument_id": TypeAdapter(Text).validate_python(instrument_id, strict=True),
        "evaluated_at": _utc(evaluated_at),
    }
    now = identity["evaluated_at"]
    evidence = {}
    measured = {
        "quality_recomputed": False,
        "analysis_recomputed": False,
        "source_authenticity_verified": False,
        "execution_authority": False,
    }

    def result(code, reason):
        return DataQualificationResult(
            **identity,
            **evidence,
            gate=GateAssessment(
                report_id=report_id,
                gate=QualificationGate.DATA,
                passed=code == "passed",
                code=code,
                reason=reason,
                measured_values=measured,
            ),
        )

    try:
        checked_policy = _bounded_scalars(policy, DataQualificationPolicy)
        evidence.update(
            policy=checked_policy, policy_sha256=_sha(_canonical(checked_policy))
        )
    except _INVALID:
        return result(
            "data_policy_invalid", "Explicit bounded data policy is required."
        )
    try:
        # Private fixed arithmetic context also protects the preflight bounds.
        with localcontext(Context(prec=100)):
            market = _market_copy(market)
            _validate_market_quotes(market)
            # Keep prices compatible with the downstream event/zone engine.
            for rows in market.candles.values():
                if any(
                    not _positive_price(price)
                    for row in rows
                    for price in (row.open, row.high, row.low, row.close)
                ):
                    return result(
                        "market_source_invalid",
                        "OHLC exceeds the shared source price contract.",
                    )
        if (
            market.instrument_id != instrument_id
            or market.ticker.instrument_id != instrument_id
            or market.order_book.instrument_id != instrument_id
            or market.symbol != to_canonical_symbol(instrument_id)
        ):
            return result("identity_mismatch", "Market and G1 identities differ.")
    except _INVALID:
        return result(
            "market_source_invalid", "Raw market source failed bounded reconstruction."
        )
    if set(market.candles) != set(TIMEFRAMES):
        return result("missing_timeframe", "Exactly 4H, 1H, 15m and 5m are required.")
    if (
        market.received_at > now
        or market.ticker.timestamp > market.received_at
        or market.order_book.timestamp > market.received_at
    ):
        return result(
            "future_market_data", "Market component timestamps are not causal."
        )
    if now - market.received_at > timedelta(
        seconds=checked_policy.maximum_snapshot_age_seconds
    ):
        return result("stale_market_data", "Market snapshot receipt is stale.")
    if any(
        now - at > timedelta(seconds=checked_policy.maximum_quote_age_seconds)
        for at in (market.ticker.timestamp, market.order_book.timestamp)
    ):
        return result(
            "stale_market_data", "Ticker or order book source observation is stale."
        )
    try:
        if type(quote) is not CollectedQuote:
            raise ValueError("exact public capture required")
        _bounded_scalars(quote.quote, ExecutableQuote)
        collected = validate_collected_quote(quote)
        current_quote, _ = inspect_executable_quote(
            collected.quote,
            current_time=now,
            max_quote_age_seconds=checked_policy.maximum_quote_age_seconds,
        )
        if collected.completed_at > now or current_quote is None:
            return result(
                "stale_market_data", "Public quote is stale or after evaluation time."
            )
        if (
            current_quote.report_id != report_id
            or current_quote.instrument_id != instrument_id
        ):
            return result("identity_mismatch", "Collected quote identity differs.")
        evidence["quote_bundle_sha256"] = collected.bundle_sha256
    except _INVALID:
        return result(
            "quote_provenance_invalid",
            "Independent public component observations are required.",
        )
    if reference is None:
        return result(
            "reference_source_missing", "An independent WS observation is required."
        )
    try:
        reference = _bounded_scalars(reference, WSReferenceObservation)
        if reference.report_id != report_id or reference.instrument_id != instrument_id:
            return result("identity_mismatch", "WS reference identity differs.")
        if reference.received_at > now or now - reference.source_time > timedelta(
            seconds=checked_policy.maximum_reference_age_seconds
        ):
            return result(
                "stale_market_data", "WS source observation is stale or future-dated."
            )
        evidence["reference_sha256"] = _sha(_canonical(reference))
    except _INVALID:
        return result(
            "reference_source_invalid", "WS observation failed strict reconstruction."
        )
    # Decision comparisons use exact rational arithmetic, not rounded bps.
    with localcontext(Context(prec=100)):
        midpoint = (Fraction(current_quote.bid) + Fraction(current_quote.ask)) / 2
        spreads = (
            (Fraction(ask) - Fraction(bid))
            / ((Fraction(ask) + Fraction(bid)) / 2)
            * 10000
            for bid, ask in (
                (current_quote.bid, current_quote.ask),
                (reference.bid, reference.ask),
                (market.ticker.bid, market.ticker.ask),
                (market.order_book.bids[0].price, market.order_book.asks[0].price),
            )
        )
        spread_bps = max(spreads)
        conflict_bps = max(
            abs(Fraction(bid) - Fraction(current_quote.bid)) / midpoint * 10000
            for bid in (
                reference.bid,
                market.ticker.bid,
                market.order_book.bids[0].price,
            )
        )
        conflict_bps = max(
            conflict_bps,
            *(
                abs(Fraction(ask) - Fraction(current_quote.ask)) / midpoint * 10000
                for ask in (
                    reference.ask,
                    market.ticker.ask,
                    market.order_book.asks[0].price,
                )
            ),
            abs(Fraction(market.ticker.last) - midpoint) / midpoint * 10000,
        )
        mark_bps = abs(Fraction(current_quote.mark_price) - midpoint) / midpoint * 10000
        funding_bps = abs(Fraction(current_quote.funding_rate)) * 10000
        for key, value in (
            ("maximum_observed_spread_bps", spread_bps),
            ("reference_conflict_bps", conflict_bps),
            ("mark_dislocation_bps", mark_bps),
            ("absolute_funding_bps", funding_bps),
        ):
            measured[key] = D(value.numerator) / D(value.denominator)
    if conflict_bps > Fraction(
        checked_policy.maximum_reference_conflict_bps
    ) or mark_bps > Fraction(checked_policy.maximum_mark_dislocation_bps):
        return result(
            "reference_price_conflict",
            "Source references or mark differ beyond policy.",
        )
    if spread_bps > Fraction(checked_policy.maximum_spread_bps):
        return result(
            "spread_exceeded", "Observed spread exceeds the explicit data policy."
        )
    if funding_bps > Fraction(checked_policy.maximum_absolute_funding_bps):
        return result("funding_exceeded", "Absolute observed funding exceeds policy.")
    qualities = {}
    for tf in TIMEFRAMES:
        rows = market.candles[tf]
        closed = [row for row in rows if row.confirmed]
        measured[f"{tf}_confirmed_bars"] = len(closed)
        if len(closed) < checked_policy.minimum_confirmed_bars:
            return result(
                "indicator_invalid",
                "Insufficient confirmed history for required indicators.",
            )
        try:
            if any(
                row.timestamp > market.received_at
                or candle_closed_at(row, tf) > market.received_at
                for row in closed
            ):
                return result(
                    "future_market_data",
                    "Confirmed candle closes after the source capture.",
                )
            qualities[tf] = inspect_candles_at(rows, tf, current_time=now)
        except _INVALID:
            return result(
                "ohlc_quality_invalid",
                "Candle timestamps or quality cannot be safely evaluated.",
            )
        if not qualities[tf].ok:
            measured["quality_issue"] = qualities[tf].issues[0].code
            return result(
                "ohlc_quality_invalid", "Recomputed candle quality rejected the source."
            )
        age = now - candle_closed_at(closed[-1], tf)
        measured[f"{tf}_last_closed_age_seconds"] = D(str(age.total_seconds()))
        if age > timedelta(
            seconds=BAR_SECONDS[tf] * checked_policy.maximum_candle_age_intervals
        ):
            return result(
                "stale_market_data",
                "Confirmed candle exceeds its timeframe age policy.",
            )
    market = market.model_copy(update={"quality": qualities})
    measured["quality_recomputed"] = True
    try:
        analysis = analyze_snapshot_at(
            market, evaluated_at=now, version=checked_policy.analysis_version
        )
        if not _indicator_values_valid(analysis, market):
            return result(
                "indicator_invalid",
                "Required recomputed indicators are missing or misaligned.",
            )
        analysis = MultiTimeframeAnalysis.model_validate_json(
            analysis.model_dump_json(), strict=True
        )
        source = _canonical(
            {
                "market": market.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
            }
        )
        if len(source) > MAX_SOURCE_BYTES:
            return result(
                "market_source_invalid",
                "Rebuilt evidence exceeds the source byte bound.",
            )
        evidence.update(source_json=source.decode("utf-8"), source_sha256=_sha(source))
    except _INVALID:
        return result(
            "indicator_invalid",
            "Deterministic analysis could not reconstruct a valid source.",
        )
    measured["analysis_recomputed"] = True
    return result(
        "passed",
        "G1 data consistency passed; source adapters and later gates remain separate.",
    )
