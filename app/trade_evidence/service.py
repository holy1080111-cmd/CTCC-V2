"""Pure preparation/revalidation from captured sources; no files or order IO.

The report records a G1–G11 prefix supplied by its caller. It does not execute
those gates, authenticate market data, or constitute a real/shadow sample.
Consumers must call validate_snapshot before rendering or publishing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext

from pydantic import BaseModel

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.indicators.core import ema_series
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.strategies.structural_protection import (
    StructuralProtectionSelection,
    select_structural_protection,
)
from app.structure.engine import analyze_structure, find_swings
from app.trade_evidence.models import (
    TIMEFRAMES,
    EvidenceCandle,
    EvidenceLevel,
    EvidencePanel,
    EvidenceSnapshot,
    Purpose,
)
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.events import _copy_source, extract_trigger
from app.trade_qualification.location import (
    ExecutableQuote,
    build_entry_zone,
    inspect_executable_quote,
)
from app.trade_qualification.models import EntryQualificationResult, require_aware
from app.trade_qualification.timing import TIMING_POLICIES


class EvidenceError(ValueError):
    """The supplied packet cannot be safely bound to its source."""


def _guard(value, depth=0):
    """Reject hidden model_copy extras before serializers can discard them."""
    if depth > 24:
        raise EvidenceError("metadata_nesting_exceeded")
    if isinstance(value, BaseModel):
        if (
            set(value.__dict__) != set(type(value).model_fields)
            or value.__pydantic_extra__
        ):
            raise EvidenceError("dirty_model_fields")
        values = value.__dict__.values()
    elif is_dataclass(value) and not isinstance(value, type):
        if set(value.__dict__) != {field.name for field in fields(value)}:
            raise EvidenceError("dirty_dataclass_fields")
        values = value.__dict__.values()
    elif isinstance(value, Mapping):
        if len(value) > 4096:
            raise EvidenceError("metadata_mapping_too_large")
        values = value.values()
    elif isinstance(value, (tuple, list)):
        if len(value) > 16384:
            raise EvidenceError("metadata_sequence_too_large")
        values = value
    else:
        if isinstance(value, float):
            raise EvidenceError("nondecimal_metadata")
        if isinstance(value, Decimal) and (
            not value.is_finite()
            or len(value.as_tuple().digits) > 256
            or abs(value.as_tuple().exponent) > 256
        ):
            raise EvidenceError("invalid_metadata_decimal")
        if isinstance(value, str) and len(value) > 8 * 1024 * 1024:
            raise EvidenceError("metadata_text_too_large")
        if isinstance(value, datetime):
            require_aware(value)
        return
    for item in values:
        _guard(item, depth + 1)


def _copy(value, expected):
    if type(value) is not expected:
        raise EvidenceError("unexpected_model_type")
    _guard(value)
    return expected.model_validate(
        value.model_dump(mode="python", round_trip=True, serialize_as_any=True),
        strict=True,
    )


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode(value, *, ascii_only=False):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise EvidenceError("duplicate_json_key")
            result[key] = item
        return result

    decoded = json.loads(
        value,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("nonfinite_json")),
    )
    _guard(decoded)
    if (
        json.dumps(
            decoded,
            ensure_ascii=ascii_only,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        != value
    ):
        raise EvidenceError("noncanonical_json")
    return decoded


def _panel(timeframe, rows, strategy, limit):
    closes = [row.close for row in rows]
    historical = {period: ema_series(closes, period) for period in (20, 50, 200)}
    start = max(0, len(rows) - limit)
    candles = tuple(
        EvidenceCandle(
            open_time=row.timestamp,
            close_time=candle_closed_at(row, timeframe),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            **{f"ema{period}": historical[period][index] for period in (20, 50, 200)},
        )
        for index, row in enumerate(rows)
        if index >= start
    )
    structure = analyze_structure(
        rows, *(historical[period][-1] for period in (20, 50, 200))
    )
    as_of = candle_closed_at(rows[-1], timeframe)
    levels = []

    def add(kind, low, high, known_at, source):
        levels.append(
            EvidenceLevel(
                kind=kind,
                low=low,
                high=high,
                known_at=known_at,
                source=source,
                as_of=as_of,
            )
        )

    swings = find_swings(rows, window=2)
    for side in ("low", "high"):
        selected = [swing for swing in swings if swing.kind == side][-3:]
        for swing in selected:
            known = candle_closed_at(rows[swing.index + 2], timeframe)
            add(
                "support" if side == "low" else "resistance",
                swing.price,
                swing.price,
                known,
                f"confirmed_{side}_pivot",
            )
        for price in sorted({swing.price for swing in selected}):
            equal = [swing for swing in selected if swing.price == price]
            if len(equal) >= 2:
                add(
                    "liquidity",
                    price,
                    price,
                    candle_closed_at(rows[equal[-1].index + 2], timeframe),
                    f"equal_{side}_pivots",
                )
    if strategy == "fvg_return":
        for gap in structure.fair_value_gaps:
            add(
                "fvg",
                gap.lower,
                gap.upper,
                gap.created_at + timedelta(seconds=BAR_SECONDS[timeframe]),
                f"{gap.direction}_fvg_active_as_of",
            )
    if strategy == "order_block_return":
        for block in structure.order_blocks:
            add(
                "order_block",
                block.lower,
                block.upper,
                block.created_at + timedelta(seconds=2 * BAR_SECONDS[timeframe]),
                f"{block.direction}_order_block_active_as_of",
            )
    return EvidencePanel(
        timeframe=timeframe,
        candles=candles,
        trend=structure.trend,
        structure=structure.swing_structure,
        levels=tuple(levels),
        crop_start=candles[0].open_time,
        crop_end=as_of,
        total_confirmed_candles=len(rows),
    )


def _event(detection, market, analysis, qualification):
    if detection is None:
        if qualification.trigger is not None or qualification.entry_zone is not None:
            raise EvidenceError("source_event_missing")
        return None
    event = _copy(detection, TriggerDetection)
    if (
        event.report_id,
        event.symbol,
        event.instrument_id,
        event.strategy,
        event.direction,
    ) != (
        qualification.report_id,
        qualification.symbol,
        market.instrument_id,
        qualification.strategy,
        qualification.direction,
    ):
        raise EvidenceError("event_identity_mismatch")
    if event.observed_at > qualification.evaluated_at:
        raise EvidenceError("event_after_qualification")
    ttl = TIMING_POLICIES[event.strategy].trigger_ttl_seconds
    if event.trigger:
        seconds = (
            event.trigger.expires_at - event.trigger.trigger_time
        ).total_seconds()
        if not seconds.is_integer() or not 0 < seconds <= ttl:
            raise EvidenceError("invalid_trigger_ttl")
        ttl = int(seconds)
    rebuilt = extract_trigger(
        market,
        analysis,
        report_id=event.report_id,
        strategy=event.strategy,
        direction=event.direction,
        observed_at=event.observed_at,
        trigger_ttl_seconds=ttl,
    )
    if rebuilt != event or event.source_sha256 is None:
        raise EvidenceError("source_event_mismatch")
    if qualification.trigger != event.trigger:
        raise EvidenceError("qualification_trigger_mismatch")
    return event


def _protection(protection, event, market, analysis, qualification):
    if protection is None:
        if qualification.stop_loss is not None or qualification.take_profit is not None:
            raise EvidenceError("structural_evidence_missing")
        return None, None
    if type(protection) is not StructuralProtectionSelection or event is None:
        raise EvidenceError("structural_evidence_invalid")
    _guard(protection)
    policy = dict(protection.policy_inputs)
    required = {
        "tick_size",
        "expected_slippage_bps",
        "cost_bps",
        "min_net_rr",
        "min_stop_distance_atr",
        "atr_buffer_multiplier",
        "minimum_buffer_bps",
        "spread",
    }
    if set(policy) != required or len(policy) != len(protection.policy_inputs):
        raise EvidenceError("structural_policy_missing")
    policy.pop("spread")
    if (
        protection.observed_at is None
        or protection.observed_at > qualification.evaluated_at
    ):
        raise EvidenceError("structural_observation_invalid")
    rebuilt = select_structural_protection(
        event,
        market,
        analysis,
        observed_at=protection.observed_at,
        entry=qualification.candidate_entry,
        **policy,
    )
    if rebuilt != protection:
        raise EvidenceError("structural_rebuild_mismatch")
    if protection.selected is not None:
        if (qualification.stop_loss, qualification.take_profit) != (
            protection.selected.stop.final_stop,
            protection.selected.target.final_target,
        ):
            raise EvidenceError("candidate_protection_mismatch")
    elif qualification.stop_loss is not None or qualification.take_profit is not None:
        raise EvidenceError("candidate_protection_missing")
    # Equal aware instants may have different offsets. Store the rebuilt UTC
    # audit, not an input dataclass's presentation, so revalidation is stable.
    return rebuilt.to_audit_json(), policy["tick_size"]


def prepare_evidence(
    market: MarketSnapshot,
    analysis: MultiTimeframeAnalysis,
    *,
    qualification: EntryQualificationResult,
    detection: TriggerDetection | None = None,
    quote: ExecutableQuote | None = None,
    protection: StructuralProtectionSelection | None = None,
    prepared_at: datetime,
    purpose: Purpose,
    candle_limit: int = 80,
    zone_tick_size: Decimal | None = None,
) -> EvidenceSnapshot:
    """Prepare bounded chart data; even passing caller gates remain unverified."""
    try:
        if (
            type(prepared_at) is not datetime
            or type(candle_limit) is not int
            or not 80 <= candle_limit <= 200
        ):
            raise EvidenceError("invalid_preparation_policy")
        now = require_aware(prepared_at)
        q = _copy(qualification, EntryQualificationResult)
        if q.evaluated_at > now or len(q.gates) > 11:
            raise EvidenceError("evidence_must_precede_g12_and_recheck")
        _guard(market)
        _guard(analysis)
        market, analysis, frames, source_sha = _copy_source(
            market, analysis, q.evaluated_at
        )
        if q.symbol != market.symbol:
            raise EvidenceError("qualification_symbol_mismatch")
        event = _event(detection, market, analysis, q)
        audit, protection_tick = _protection(protection, event, market, analysis, q)
        if (
            zone_tick_size is not None
            and protection_tick is not None
            and zone_tick_size != protection_tick
        ):
            raise EvidenceError("zone_tick_conflict")
        tick = protection_tick if zone_tick_size is None else zone_tick_size
        if q.entry_zone is not None:
            if tick is None:
                raise EvidenceError("zone_tick_missing")
            zone, code = build_entry_zone(
                event,
                tick_size=tick,
                max_allowed_drift_bps=q.entry_zone.max_allowed_drift_bps,
                expires_at=q.entry_zone.expires_at,
            )
            if code != "passed" or zone != q.entry_zone:
                raise EvidenceError("zone_rebuild_mismatch")
        checked_quote = None
        if quote is not None:
            _guard(quote)
            checked_quote, code = inspect_executable_quote(
                quote, current_time=q.evaluated_at, max_quote_age_seconds=86400
            )
            if checked_quote is None:
                raise EvidenceError(code)
            if (checked_quote.report_id, checked_quote.instrument_id) != (
                q.report_id,
                market.instrument_id,
            ):
                raise EvidenceError("quote_identity_mismatch")
            reference = (
                checked_quote.ask if q.direction == "long" else checked_quote.bid
            )
            if q.reference_price is not None and q.reference_price != reference:
                raise EvidenceError("reference_quote_mismatch")
        elif q.reference_price is not None:
            raise EvidenceError("reference_quote_missing")
        source_json = _canonical(
            {
                "market": market.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json"),
            }
        )
        with localcontext(Context(prec=100)):
            panels = tuple(
                _panel(tf, frames[tf], q.strategy, candle_limit) for tf in TIMEFRAMES
            )
        candidate_json = _canonical(
            {
                "source_sha256": source_sha,
                "qualification": q.model_dump(mode="json", round_trip=True),
                "detection": event.model_dump(mode="json", round_trip=True)
                if event
                else None,
                "quote": checked_quote.model_dump(mode="json", round_trip=True)
                if checked_quote
                else None,
                "protection_audit_json": audit,
                "zone_tick_size": str(tick) if tick is not None else None,
            }
        )
        return EvidenceSnapshot(
            report_id=q.report_id,
            instrument_id=market.instrument_id,
            symbol=q.symbol,
            strategy=q.strategy,
            direction=q.direction,
            qualification=q,
            detection=event,
            quote=checked_quote,
            panels=panels,
            source_sha256=source_sha,
            candidate_sha256=_digest(candidate_json),
            source_json=source_json,
            protection_audit_json=audit,
            prepared_at=now,
            purpose=purpose,
            candle_limit=candle_limit,
            zone_tick_size=tick,
        )
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invalid_evidence_input") from exc


def validate_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    """Rebuild every plotted value; metadata hashes are not market authentication."""
    try:
        value = _copy(snapshot, EvidenceSnapshot)
        source = _decode(value.source_json)
        if (
            set(source) != {"market", "analysis"}
            or _digest(value.source_json) != value.source_sha256
        ):
            raise EvidenceError("source_digest_mismatch")
        market = MarketSnapshot.model_validate_json(
            _canonical(source["market"]), strict=True
        )
        analysis = MultiTimeframeAnalysis.model_validate_json(
            _canonical(source["analysis"]), strict=True
        )
        protection = None
        if value.protection_audit_json is not None:
            audit = _decode(value.protection_audit_json, ascii_only=True)
            policy = {
                key: Decimal(item)
                for key, item in audit["policy_inputs"]
                if key != "spread"
            }
            protection = select_structural_protection(
                value.detection,
                market,
                analysis,
                observed_at=datetime.fromisoformat(audit["observed_at"]),
                entry=value.qualification.candidate_entry,
                **policy,
            )
            if protection.to_audit_json() != value.protection_audit_json:
                raise EvidenceError("structural_audit_mismatch")
        rebuilt = prepare_evidence(
            market,
            analysis,
            qualification=value.qualification,
            detection=value.detection,
            quote=value.quote,
            protection=protection,
            prepared_at=value.prepared_at,
            purpose=value.purpose,
            candle_limit=value.candle_limit,
            zone_tick_size=value.zone_tick_size,
        )
        if rebuilt != value:
            raise EvidenceError("snapshot_rebuild_mismatch")
        return rebuilt
    except (
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        ArithmeticError,
        RecursionError,
    ) as exc:
        if isinstance(exc, EvidenceError):
            raise
        raise EvidenceError("invalid_evidence_snapshot") from exc
