"""Source-derived entry bands and quote checks without IO or order authority.

The caller supplies trusted instrument metadata and source observations. These
pure functions validate consistency; a digest does not authenticate a source.
Timing, economics, source collection and the post-render fetch barrier are
separate gates. Candidate prices and structural invalidation are never moved.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    Context,
    Decimal,
    DecimalException,
    localcontext,
)
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from app.market.quality.candles import BAR_SECONDS
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.models import (
    EntryTrigger,
    EntryZone,
    Price,
    QualificationModel,
    ReportId,
    Text,
    require_aware,
)

_PRICE = TypeAdapter(Price)
_DRIFT_LIMIT = TypeAdapter(
    Annotated[Decimal, Field(ge=0, le=10000, max_digits=30, decimal_places=20)]
)
_REPORT_ID = TypeAdapter(ReportId)
_INSTRUMENT_ID = TypeAdapter(Text)
_SOURCE_PRICE = re.compile(r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?")
_INVALID = (ValueError, TypeError, AttributeError, OverflowError, DecimalException)


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime:
        raise ValueError("an exact aware datetime is required")
    return require_aware(value).astimezone(UTC)


def _revalidate[Model: BaseModel](value: Model, expected: type[Model]) -> Model:
    if type(value) is not expected:
        raise ValueError(f"an exact {expected.__name__} is required")
    # model_copy can inject undeclared __dict__ keys that model_dump would drop.
    if set(value.__dict__) != set(expected.model_fields) or value.__pydantic_extra__:
        raise ValueError("model fields were removed or undeclared fields were added")
    return expected.model_validate(
        value.model_dump(mode="python", round_trip=True, serialize_as_any=True),
        strict=True,
    )


class ExecutableQuote(QualificationModel):
    """Independent component timestamps, never a merged-channel freshness stamp.

    ``funding_time`` is the funding record's source observation/update time, not
    its next effective or settlement time. The collector must retain that true
    source time; copying ``received_at`` into missing component times is invalid.
    """

    report_id: ReportId
    instrument_id: Text
    source: Literal["rest", "ws"]
    bid: Price
    ask: Price
    mark_price: Price
    bid_size: Price
    ask_size: Price
    funding_rate: Decimal = Field(max_digits=40, decimal_places=20)
    quote_time: datetime
    mark_time: datetime
    funding_time: datetime
    received_at: datetime
    request_started_at: datetime | None = None

    _utc_components = field_validator(
        "quote_time", "mark_time", "funding_time", "received_at"
    )(_utc)

    @field_validator("request_started_at")
    @classmethod
    def optional_request_time(cls, value):
        return None if value is None else _utc(value)


class LocationResult(QualificationModel):
    report_id: ReportId
    instrument_id: Text
    passed: bool
    code: Text
    reason: Text
    reference_price: Price | None = None
    drift_bps: Decimal | None = Field(default=None, ge=0)
    quote_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    execution_authority: Literal[False] = False

    @field_validator("execution_authority", mode="before")
    @classmethod
    def exact_false(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("location checks cannot grant execution authority")
        return value

    @model_validator(mode="after")
    def consistent_result(self):
        if self.passed != (self.code == "passed"):
            raise ValueError("only passing location checks use the passed code")
        if self.passed and any(
            value is None
            for value in (self.reference_price, self.drift_bps, self.quote_sha256)
        ):
            raise ValueError("passing location checks require quote and drift evidence")
        return self


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def quote_fingerprint(quote: ExecutableQuote) -> str:
    """Hash the complete validated quote with numeric and UTC normalization."""
    checked = _revalidate(quote, ExecutableQuote)
    payload = {
        key: _decimal_text(value)
        if isinstance(value, Decimal)
        else value.astimezone(UTC).isoformat()
        if isinstance(value, datetime)
        else value
        for key, value in checked.model_dump(mode="python", round_trip=True).items()
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _basis_price(value: str) -> Decimal:
    if (
        type(value) is not str
        or len(value) > 128
        or _SOURCE_PRICE.fullmatch(value) is None
    ):
        raise ValueError("source prices require bounded decimal text")
    parsed = Decimal(value)
    # Source indicators retain more than contract Price's 20 decimal places.
    # Keep that exact band until real instrument tick alignment, with explicit
    # numeric limits so an exponent cannot amplify arithmetic or serialization.
    if (
        not parsed.is_finite()
        or not Decimal(0) < parsed < Decimal("1e20")
        or len(parsed.as_tuple().digits) > 80
        or abs(parsed.as_tuple().exponent) > 100
    ):
        raise ValueError("source prices exceed bounded decimal precision")
    return parsed


def inspect_executable_quote(
    quote: ExecutableQuote | None,
    *,
    current_time: datetime,
    max_quote_age_seconds: int,
) -> tuple[ExecutableQuote | None, str]:
    """Shared quote integrity/freshness check, without caller identity authority.

    Consumers must also match the returned report/instrument to their own
    trusted source. Receipt freshness never substitutes for component freshness.
    """
    try:
        now = _utc(current_time)
    except _INVALID:
        return None, "current_time_invalid"
    if (
        type(max_quote_age_seconds) is not int
        or not 1 <= max_quote_age_seconds <= 86400
    ):
        return None, "quote_age_limit_invalid"
    if quote is None:
        return None, "executable_quote_missing"
    try:
        checked = _revalidate(quote, ExecutableQuote)
    except _INVALID:
        return None, "executable_quote_invalid"
    if checked.bid > checked.ask:
        return None, "quote_geometry_invalid"
    components = (checked.quote_time, checked.mark_time, checked.funding_time)
    if (
        any(value > now or value > checked.received_at for value in components)
        or checked.received_at > now
        or (
            checked.request_started_at is not None
            and checked.request_started_at > checked.received_at
        )
    ):
        return None, "quote_timestamp_invalid"
    max_age = timedelta(seconds=max_quote_age_seconds)
    if any(now - value > max_age for value in (*components, checked.received_at)):
        return None, "quote_stale"
    return checked, "passed"


def _ticks(value: Decimal, tick: Decimal, *, ceiling: bool) -> int:
    # Integer ratios avoid a rounded repeating quotient moving a boundary onto
    # an invalid tick. All operands were bounded before reaching this helper.
    numerator, denominator = value.as_integer_ratio()
    tick_numerator, tick_denominator = tick.as_integer_ratio()
    numerator *= tick_denominator
    denominator *= tick_numerator
    return -(-numerator // denominator) if ceiling else numerator // denominator


def _source_anchor(
    event: TriggerDetection, basis: dict[str, str], close: Decimal
) -> Decimal:
    raw_text = basis.get("invalidation_unrounded")
    rounding_label = basis.get("invalidation_rounding")
    if raw_text is None and rounding_label is None:
        return event.invalidation_price
    if raw_text is None or rounding_label != "decimal20_toward_setup_close":
        raise ValueError("invalidation rounding provenance is incomplete or unknown")
    raw = _basis_price(raw_text)
    if raw.as_tuple().exponent >= -20 or (
        raw >= close if event.direction == "long" else raw <= close
    ):
        raise ValueError("invalidation representation must round toward setup close")
    rounding = ROUND_CEILING if event.direction == "long" else ROUND_FLOOR
    if raw.quantize(Decimal("1e-20"), rounding=rounding) != event.invalidation_price:
        raise ValueError("typed invalidation differs from recorded source rounding")
    return raw


def build_entry_zone(
    detection: TriggerDetection,
    *,
    tick_size: Decimal,
    max_allowed_drift_bps: Decimal,
    expires_at: datetime,
) -> tuple[EntryZone | None, str]:
    """Intersect a recorded source band with the real instrument's tick grid.

    For a zero-width break/reclaim level, only its actual confirmed setup close
    can form a body band. Tick alignment is inward; it never creates a wider zone
    or changes the original invalidation anchor. The source pin is audit metadata,
    not an independently verified market identity.
    """
    try:
        tick = _PRICE.validate_python(tick_size, strict=True)
    except _INVALID:
        return None, "instrument_tick_invalid"
    try:
        drift = _DRIFT_LIMIT.validate_python(max_allowed_drift_bps, strict=True)
    except _INVALID:
        return None, "drift_limit_invalid"
    try:
        if detection.trigger is not None:
            _revalidate(detection.trigger, EntryTrigger)
        event = _revalidate(detection, TriggerDetection)
        supplied_expiry = _utc(expires_at)
        if (
            event.fail_codes
            or event.trigger is None
            or event.trigger.invalidation_reason is not None
            or event.setup_time is None
            or event.setup_type is None
            or event.source_sha256 is None
            or event.invalidation_price is None
        ):
            return None, "source_event_invalid"
        if event.setup_time >= event.trigger.trigger_time:
            return None, "source_event_invalid"
    except _INVALID:
        return None, "source_event_invalid"
    basis = dict(event.setup_basis)
    if not {"zone_low", "zone_high", "source_closed_at", "setup_close"} <= basis.keys():
        return None, "source_basis_missing"
    try:
        low, high, close = (
            _basis_price(basis[name])
            for name in ("zone_low", "zone_high", "setup_close")
        )
        closed_at = _utc(datetime.fromisoformat(basis["source_closed_at"]))
        setup_timeframe = basis.get("setup_timeframe")
        if (
            low > high
            or closed_at != event.setup_time
            or closed_at > event.trigger.trigger_time
            or closed_at > event.observed_at
            or (setup_timeframe is not None and setup_timeframe not in BAR_SECONDS)
            or basis.get("ohlc_ordering", "close_confirmed_only")
            != "close_confirmed_only"
        ):
            return None, "source_basis_invalid"
        expiry = min(supplied_expiry, _utc(event.trigger.expires_at))
        if expiry <= event.observed_at:
            return None, "entry_zone_expired"
        anchor = event.invalidation_price
        with localcontext(Context(prec=100)):
            source_anchor = _source_anchor(event, basis, close)
            if low == high:
                if close == low:
                    return None, "no_executable_zone"
                if (event.direction == "long" and close < low) or (
                    event.direction == "short" and close > high
                ):
                    return None, "source_basis_invalid"
                low, high = min(low, close), max(high, close)
            if (event.direction == "long" and source_anchor > low) or (
                event.direction == "short" and source_anchor < high
            ):
                return None, "source_basis_invalid"
            aligned_low = _ticks(low, tick, ceiling=True) * tick
            aligned_high = _ticks(high, tick, ceiling=False) * tick
            if event.direction == "long" and aligned_low <= anchor:
                aligned_low = (_ticks(anchor, tick, ceiling=False) + 1) * tick
            if event.direction == "short" and aligned_high >= anchor:
                aligned_high = (_ticks(anchor, tick, ceiling=True) - 1) * tick
            if aligned_low > aligned_high or aligned_low <= 0:
                return None, "no_executable_zone"
        source = ":".join(
            (
                event.source_sha256,
                event.instrument_id,
                event.direction,
                setup_timeframe or "unknown_setup_timeframe",
                closed_at.isoformat(),
            )
        )
        return EntryZone(
            report_id=event.report_id,
            instrument_id=event.instrument_id,
            direction=event.direction,
            source_sha256=event.source_sha256,
            zone_low=aligned_low,
            zone_high=aligned_high,
            zone_type=event.setup_type,
            zone_source=source,
            created_at=closed_at,
            expires_at=expiry,
            max_allowed_drift_bps=drift,
            invalidation_price=anchor,
        ), "passed"
    except _INVALID:
        return None, "source_basis_invalid"


def evaluate_location(
    *,
    report_id: str,
    instrument_id: str,
    direction: Literal["long", "short"],
    zone: EntryZone | None,
    candidate_entry: Decimal,
    quote: ExecutableQuote | None,
    current_time: datetime,
    max_quote_age_seconds: int = 30,
) -> LocationResult:
    """Check an unchanged candidate against executable ask/bid and source zone.

    Fresh quote, mark, funding and receive timestamps are checked independently.
    The conservative invalidation policy rejects either book side touching the
    thesis anchor; only the executable side must also lie inside the entry zone.
    The request-start/render-completion barrier belongs to the later recheck gate.
    Invalid caller report/instrument syntax raises validation error because no
    valid result identity can be emitted; malformed evidence otherwise fails closed.
    """
    report_id = _REPORT_ID.validate_python(report_id, strict=True)
    instrument_id = _INSTRUMENT_ID.validate_python(instrument_id, strict=True)
    reference = None
    drift = None
    digest = None

    def result(code: str, reason: str) -> LocationResult:
        return LocationResult(
            report_id=report_id,
            instrument_id=instrument_id,
            passed=code == "passed",
            code=code,
            reason=reason,
            reference_price=reference,
            drift_bps=drift,
            quote_sha256=digest,
        )

    if type(direction) is not str or direction not in {"long", "short"}:
        return result("direction_invalid", "Direction must be long or short.")
    try:
        now = _utc(current_time)
    except _INVALID:
        return result(
            "current_time_invalid", "Evaluation time must be an aware datetime."
        )
    if (
        type(max_quote_age_seconds) is not int
        or not 1 <= max_quote_age_seconds <= 86400
    ):
        return result(
            "quote_age_limit_invalid",
            "Quote age limit must be a bounded positive integer.",
        )
    if zone is None:
        return result("entry_zone_missing", "A source-derived entry zone is required.")
    if quote is None:
        return result("executable_quote_missing", "An executable quote is required.")
    try:
        checked_zone = _revalidate(zone, EntryZone)
    except _INVALID:
        return result(
            "entry_zone_invalid", "Entry zone failed validated reconstruction."
        )
    try:
        checked_quote = _revalidate(quote, ExecutableQuote)
        reference = checked_quote.ask if direction == "long" else checked_quote.bid
        digest = quote_fingerprint(checked_quote)
    except _INVALID:
        return result(
            "executable_quote_invalid", "Quote failed validated reconstruction."
        )
    if any(
        value is None
        for value in (
            checked_zone.instrument_id,
            checked_zone.direction,
            checked_zone.source_sha256,
        )
    ):
        return result(
            "zone_provenance_missing", "Typed source-zone provenance is required."
        )
    if (
        checked_zone.report_id != report_id
        or checked_zone.instrument_id != instrument_id
        or checked_zone.direction != direction
        or checked_quote.report_id != report_id
        or checked_quote.instrument_id != instrument_id
    ):
        return result(
            "identity_mismatch", "Report and instrument identities must agree."
        )
    try:
        entry = _PRICE.validate_python(candidate_entry, strict=True)
    except _INVALID:
        return result(
            "candidate_entry_invalid",
            "Candidate entry must be an exact finite positive price.",
        )
    _, quote_code = inspect_executable_quote(
        checked_quote,
        current_time=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    if quote_code != "passed":
        return result(
            quote_code, "Quote geometry or independent component freshness failed."
        )
    if now < checked_zone.created_at:
        return result("entry_zone_not_open", "The source entry zone has not opened.")
    if now >= checked_zone.expires_at:
        return result("entry_zone_expired", "The source entry zone has expired.")
    try:
        with localcontext(Context(prec=100)):
            drift = abs(reference - entry) / entry * Decimal(10000)
            anchor = checked_zone.invalidation_price
            if (
                direction == "long"
                and min(
                    entry,
                    checked_quote.bid,
                    checked_quote.ask,
                    checked_quote.mark_price,
                    checked_zone.zone_low,
                )
                <= anchor
            ) or (
                direction == "short"
                and max(
                    entry,
                    checked_quote.bid,
                    checked_quote.ask,
                    checked_quote.mark_price,
                    checked_zone.zone_high,
                )
                >= anchor
            ):
                return result(
                    "invalidation_crossed",
                    "Price or source-zone direction crosses the unchanged invalidation.",
                )
            if not checked_zone.zone_low <= entry <= checked_zone.zone_high:
                return result(
                    "candidate_outside_entry_zone",
                    "The original candidate is outside its source entry zone.",
                )
            if not checked_zone.zone_low <= reference <= checked_zone.zone_high:
                return result(
                    "reference_outside_entry_zone",
                    "Executable ask/bid is outside the source entry zone.",
                )
            if drift > checked_zone.max_allowed_drift_bps:
                return result(
                    "entry_drift_exceeds_limit",
                    "Executable quote drift exceeds the recorded entry-zone limit.",
                )
    except _INVALID:
        return result(
            "candidate_entry_invalid", "Entry-location arithmetic failed validation."
        )
    return result(
        "passed",
        "Unchanged candidate and executable quote remain inside the valid source zone.",
    )
