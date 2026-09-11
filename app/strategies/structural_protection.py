"""Source-bound static protection proposals; never execution/EV authority.

The bounded source universe is the existing structure engine's last three
confirmed pivots per side and unmitigated zones on 15m/1H/4H, plus the actual
event invalidation. Every stop/target combination is assessed, including target
barriers from a timeframe lacking a usable stop. Ranking is uncalibrated
engineering policy. No trailing, break-even move or entry repricing occurs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
from itertools import islice
from typing import Literal

from app.domain.analysis import MultiTimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.strategy import StructuralProtectionGeometry
from app.indicators.core import atr
from app.market.quality.candles import BAR_SECONDS, candle_closed_at
from app.structure.engine import analyze_structure, find_swings
from app.trade_qualification.event_models import TriggerDetection
from app.trade_qualification.events import _checked_tree, _copy_source, extract_trigger
from app.trade_qualification.timing import TIMING_POLICIES

D = Decimal
TIMEFRAMES = ("15m", "1H", "4H")
_INVALID = (
    ValueError,
    TypeError,
    AttributeError,
    KeyError,
    OverflowError,
    DecimalException,
)
_MAX_ANCHORS = 128
_RANKING = "noise_clearance_atr_desc_then_net_rr_desc_then_canonical_anchor_ids"
_MAX_VALUE = D("1e20")
_LEGACY_ATR_MULTIPLIER = D("0.25")
_LEGACY_MINIMUM_BPS = D("5")


@dataclass(frozen=True)
class StructuralAnchor:
    anchor_id: str
    timeframe: str
    source: str
    anchor_price: Decimal
    source_closed_at: datetime
    known_at: datetime
    atr: Decimal | None
    zone_low: Decimal | None = None
    zone_high: Decimal | None = None
    rejection_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StopAlternative:
    anchor: StructuralAnchor
    buffer: Decimal | None
    buffer_atr_multiple: Decimal | None
    final_stop: Decimal | None
    stop_distance_pct: Decimal | None
    stop_distance_atr: Decimal | None
    noise_clearance_atr: Decimal | None
    liquidity_warnings: tuple[str, ...]
    rejection_codes: tuple[str, ...]

    @property
    def structure_validity(self) -> bool:
        return not self.rejection_codes


@dataclass(frozen=True)
class TargetAlternative:
    anchor: StructuralAnchor
    final_target: Decimal | None
    rejection_codes: tuple[str, ...]


@dataclass(frozen=True)
class ProtectionBracket:
    stop: StopAlternative
    target: TargetAlternative
    gross_rr: Decimal | None
    net_rr: Decimal | None
    rejection_codes: tuple[str, ...]
    ranking_reason: str

    @property
    def valid(self) -> bool:
        return not self.rejection_codes


@dataclass(frozen=True)
class StructuralProtectionSelection:
    report_id: str | None
    instrument_id: str | None
    direction: str | None
    reference_entry: Decimal | None
    observed_at: datetime | None
    source_sha256: str | None
    evidence: Literal["confirmed_ohlc", "legacy_incomplete"]
    stops: tuple[StopAlternative, ...]
    targets: tuple[TargetAlternative, ...]
    alternatives: tuple[ProtectionBracket, ...]
    selected: ProtectionBracket | None
    fail_codes: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    selection_reason: str
    spread: Decimal | None = None
    expected_slippage_bps: Decimal | None = None
    cost_bps: Decimal | None = None
    policy_inputs: tuple[tuple[str, Decimal], ...] = ()
    strategy: str | None = None
    event_setup_basis: tuple[tuple[str, str], ...] = ()

    @property
    def protection_valid(self) -> bool:
        return (
            self.evidence == "confirmed_ohlc"
            and self.selected is not None
            and not self.fail_codes
        )

    @property
    def execution_authority(self) -> Literal[False]:
        return False

    @property
    def economics_validated(self) -> Literal[False]:
        return False

    @property
    def policy_calibrated(self) -> Literal[False]:
        return False

    def to_audit_json(self) -> str:
        """Canonical output only; there is deliberately no input-authority API."""

        def encode(value):
            if isinstance(value, Decimal):
                return format(value, "f")
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [encode(item) for item in value]
            return value

        payload = encode(asdict(self))
        payload.update(
            schema="ctcc_structural_selection_v1",
            record_kind="audit_not_authority",
            execution_authority=False,
            economics_validated=False,
            policy_calibrated=False,
            protection_valid=self.protection_valid,
        )
        return json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True)
class _Policy:
    entry: Decimal
    direction: str
    tick: Decimal | None
    spread: Decimal
    slippage_bps: Decimal
    cost_bps: Decimal
    min_net_rr: Decimal
    min_stop_atr: Decimal
    atr_multiplier: Decimal
    minimum_bps: Decimal
    thesis: Decimal | None


def _decimal(value, *, positive=False, upper=_MAX_VALUE) -> Decimal:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or len(value.as_tuple().digits) > 40
        or abs(value.as_tuple().exponent) > 20
        or value < 0
        or (positive and value == 0)
        or value >= upper
    ):
        raise ValueError("invalid_decimal")
    return value


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid_utc_time")
    return value.astimezone(UTC)


def _aligned(value: Decimal, tick: Decimal | None, *, ceiling: bool) -> Decimal:
    if tick is None:
        return value
    numerator, denominator = value.as_integer_ratio()
    tick_numerator, tick_denominator = tick.as_integer_ratio()
    numerator *= tick_denominator
    denominator *= tick_numerator
    ticks = -(-numerator // denominator) if ceiling else numerator // denominator
    return ticks * tick


def _anchor(
    timeframe,
    source,
    price,
    closed_at,
    known_at,
    volatility,
    *,
    low=None,
    high=None,
    codes=(),
):
    return StructuralAnchor(
        anchor_id=f"{timeframe}:{source}:{known_at.isoformat()}:{price.normalize()}",
        timeframe=timeframe,
        source=source,
        anchor_price=price,
        source_closed_at=closed_at,
        known_at=known_at,
        atr=volatility,
        zone_low=low,
        zone_high=high,
        rejection_codes=tuple(codes),
    )


def _ordered(anchors):
    if len(anchors) > _MAX_ANCHORS:
        raise ValueError("structural_candidate_limit")
    identities = [item.anchor_id for item in anchors]
    if len(identities) != len(set(identities)):
        raise ValueError("structural_source_duplicate")
    return tuple(sorted(anchors, key=lambda item: item.anchor_id))


def _source_anchors(frames, event):
    stops, targets = [], []
    long = event.direction == "long"
    basis = dict(event.setup_basis)
    native = basis["setup_timeframe"]
    for timeframe in TIMEFRAMES:
        rows = frames[timeframe]
        closed_at = candle_closed_at(rows[-1], timeframe)
        volatility = atr(rows, 14)
        structure = analyze_structure(rows, None, None, None)
        swings = find_swings(rows, 2)
        for kind in ("low", "high"):
            retained = [item for item in swings if item.kind == kind][-3:]
            destination = stops if (kind == "low") == long else targets
            for pivot in retained:
                destination.append(
                    _anchor(
                        timeframe,
                        f"swing_{kind}",
                        pivot.price,
                        closed_at,
                        candle_closed_at(rows[pivot.index + 2], timeframe),
                        volatility,
                    )
                )
            # Separate confirmed pivots, not duplicate support-list aliases.
            for price in sorted({item.price for item in retained}):
                matching = [item for item in retained if item.price == price]
                if len(matching) >= 2:
                    destination.append(
                        _anchor(
                            timeframe,
                            f"equal_{kind}s",
                            price,
                            closed_at,
                            candle_closed_at(rows[matching[-1].index + 2], timeframe),
                            volatility,
                        )
                    )
        for kind, zones in (
            ("fvg", structure.fair_value_gaps),
            ("order_block", structure.order_blocks),
        ):
            for zone in zones:
                formation = zone.created_at + timedelta(
                    seconds=BAR_SECONDS[timeframe] * (1 if kind == "fvg" else 2)
                )
                same = (zone.direction == "bullish") == long
                price = zone.lower if long else zone.upper
                codes = ()
                if (
                    same
                    and kind == "fvg"
                    and not (
                        event.strategy == "fvg_return"
                        and native == timeframe
                        and basis.get("formation_closed_at") == formation.isoformat()
                        and D(basis["zone_low"]) == zone.lower
                        and D(basis["zone_high"]) == zone.upper
                    )
                ):
                    codes = ("fvg_setup_mismatch",)
                (stops if same else targets).append(
                    _anchor(
                        timeframe,
                        kind,
                        price,
                        closed_at,
                        formation,
                        volatility,
                        low=zone.lower,
                        high=zone.upper,
                        codes=codes,
                    )
                )
    # Only reconstructed, strategy-specific structure invalidations are added.
    # A trend EMA band is not relabelled as a swing/OB/FVG.
    event_sources = {
        "breakout_continuation": "bos_invalidation",
        "structure_reversal": "choch_invalidation",
        "volatility_expansion": "compression_break_invalidation",
        "liquidity_sweep_reversal": "sweep_extreme_invalidation",
        "fvg_return": "fvg_setup_invalidation",
        "order_block_return": "order_block_setup_invalidation",
    }
    if event.strategy in event_sources:
        rows = frames[native]
        raw = D(basis.get("invalidation_unrounded", str(event.invalidation_price)))
        zone_backed = event.strategy in {"fvg_return", "order_block_return"}
        stops.append(
            _anchor(
                native,
                event_sources[event.strategy],
                raw,
                candle_closed_at(rows[-1], native),
                datetime.fromisoformat(basis["formation_closed_at"])
                if zone_backed
                else event.setup_time,
                atr(rows, 14),
                low=D(basis["zone_low"]) if zone_backed else None,
                high=D(basis["zone_high"]) if zone_backed else None,
            )
        )
    return _ordered(stops), _ordered(targets)


def _evaluate(stops, targets, policy):
    """One shared full enumeration for source evidence and legacy proposals."""
    p = policy
    long = p.direction == "long"
    stop_results, target_results = [], []
    slippage = p.entry * p.slippage_bps / D(10000)
    friction = p.spread + slippage + (p.tick or D(0))
    pools = [item for item in stops if item.source.startswith("equal_")]
    for anchor in stops:
        codes = list(anchor.rejection_codes)
        warnings = []
        buffer = final = distance_pct = distance_atr = clearance = multiple = None
        if anchor.atr is None or anchor.atr <= 0:
            codes.append("atr_missing_or_invalid")
        elif anchor.anchor_price <= 0:
            codes.append("anchor_invalid")
        else:
            noise = max(
                p.entry * p.minimum_bps / D(10000), anchor.atr * p.atr_multiplier
            )
            buffer = noise + friction
            final = _aligned(
                anchor.anchor_price - buffer if long else anchor.anchor_price + buffer,
                p.tick,
                ceiling=not long,
            )
            buffer = abs(anchor.anchor_price - final)
            multiple = buffer / anchor.atr
            distance = p.entry - final if long else final - p.entry
            distance_pct = distance / p.entry * D(100)
            distance_atr = distance / anchor.atr
            clearance = abs(p.entry - anchor.anchor_price) / anchor.atr
            if not (
                0 < anchor.anchor_price < p.entry
                if long
                else anchor.anchor_price > p.entry
            ):
                codes.append("stop_wrong_side")
            if not 0 < final < _MAX_VALUE or distance <= 0:
                codes.append("stop_geometry_invalid")
            if p.thesis is not None and (
                anchor.anchor_price > p.thesis
                if long
                else anchor.anchor_price < p.thesis
            ):
                codes.append("thesis_anchor_inside")
            if distance_atr < p.min_stop_atr:
                codes.append("stop_inside_atr_noise")
            for pool in pools:
                if pool.atr is None or pool.atr <= 0:
                    continue
                pool_buffer = (
                    max(p.entry * p.minimum_bps / D(10000), pool.atr * p.atr_multiplier)
                    + friction
                )
                if abs(final - pool.anchor_price) < pool_buffer:
                    codes.append("stop_inside_liquidity_pool_buffer")
                    warnings.append(pool.anchor_id)
        stop_results.append(
            StopAlternative(
                anchor,
                buffer,
                multiple,
                final,
                distance_pct,
                distance_atr,
                clearance,
                tuple(sorted(set(warnings))),
                tuple(sorted(set(codes))),
            )
        )
    # An incomplete timeframe's opposing sources still block more distant TPs.
    barriers = [
        item.anchor_price
        for item in targets
        if (item.anchor_price > p.entry if long else item.anchor_price < p.entry)
    ]
    nearest = (min(barriers) if long else max(barriers)) if barriers else None
    occupied = any(
        item.zone_low is not None and item.zone_low <= p.entry <= item.zone_high
        for item in targets
    )
    for anchor in targets:
        codes = list(anchor.rejection_codes)
        final = _aligned(anchor.anchor_price, p.tick, ceiling=not long)
        if not (final > p.entry if long else 0 < final < p.entry):
            codes.append("target_wrong_side_or_tick_collapsed")
        if nearest is not None and (
            anchor.anchor_price > nearest if long else anchor.anchor_price < nearest
        ):
            codes.append("intervening_structural_target")
        if occupied:
            codes.append("entry_inside_opposing_zone")
        target_results.append(
            TargetAlternative(anchor, final, tuple(sorted(set(codes))))
        )
    alternatives = []
    cost = p.entry * p.cost_bps / D(10000)
    for stop in stop_results:
        for target in target_results:
            codes = list(stop.rejection_codes + target.rejection_codes)
            gross = net = None
            if stop.final_stop is not None and target.final_target is not None:
                risk = p.entry - stop.final_stop if long else stop.final_stop - p.entry
                reward = (
                    target.final_target - p.entry
                    if long
                    else p.entry - target.final_target
                )
                if risk > 0 and reward > 0:
                    gross = reward / risk
                    net = (reward - cost) / (risk + cost)
                    if net < p.min_net_rr:
                        codes.append("net_rr_below_minimum")
                else:
                    codes.append("bracket_geometry_invalid")
            alternatives.append(
                ProtectionBracket(
                    stop,
                    target,
                    gross,
                    net,
                    tuple(sorted(set(codes))),
                    "rejected" if codes else _RANKING,
                )
            )
    valid = [item for item in alternatives if item.valid]
    selected = (
        min(
            valid,
            key=lambda item: (
                -item.stop.noise_clearance_atr,
                -item.net_rr,
                item.stop.anchor.anchor_id,
                item.target.anchor.anchor_id,
            ),
        )
        if valid
        else None
    )
    return tuple(stop_results), tuple(target_results), tuple(alternatives), selected


def select_structural_protection(
    detection: TriggerDetection,
    market: MarketSnapshot,
    analysis: MultiTimeframeAnalysis,
    *,
    observed_at: datetime,
    entry: Decimal,
    tick_size: Decimal,
    expected_slippage_bps: Decimal,
    cost_bps: Decimal,
    min_net_rr: Decimal,
    min_stop_distance_atr: Decimal,
    atr_buffer_multiplier: Decimal,
    minimum_buffer_bps: Decimal,
) -> StructuralProtectionSelection:
    """Select a source-bound bracket without moving the supplied entry.

    Cost bps is an explicit conservative round-trip scenario, not an EV claim.
    The caller must already pass location/timing and later perform authoritative
    economics, portfolio risk and post-render executable-quote recheck. No price
    freshness threshold is invented here; no execution state is changed.
    """
    identity = {
        "report_id": None,
        "instrument_id": None,
        "direction": None,
        "reference_entry": None,
        "observed_at": None,
        "source_sha256": None,
        "evidence": "confirmed_ohlc",
    }

    def failed(code):
        return StructuralProtectionSelection(
            **identity,
            stops=(),
            targets=(),
            alternatives=(),
            selected=None,
            fail_codes=(code,),
            missing_evidence=(),
            selection_reason="fail_closed",
        )

    try:
        now = _utc(observed_at)
        price = _decimal(entry, positive=True)
        tick = _decimal(tick_size, positive=True)
        slip = _decimal(expected_slippage_bps, upper=D(10000))
        cost = _decimal(cost_bps, upper=D(10000))
        minimum_rr = _decimal(min_net_rr, positive=True, upper=D(10000))
        minimum_atr = _decimal(min_stop_distance_atr, positive=True, upper=D(10000))
        multiplier = _decimal(atr_buffer_multiplier, positive=True, upper=D(10000))
        minimum_bps = _decimal(minimum_buffer_bps, positive=True, upper=D(10000))
        identity.update(reference_entry=price, observed_at=now)
    except _INVALID:
        return failed("protection_input_invalid")
    try:
        if type(detection) is not TriggerDetection or set(detection.__dict__) != set(
            TriggerDetection.model_fields
        ):
            return failed("source_event_invalid")
        raw = _checked_tree(
            detection.model_dump(mode="python", round_trip=True, serialize_as_any=True)
        )
        event = TriggerDetection.model_validate(raw, strict=True)
        if (
            event.fail_codes
            or event.trigger is None
            or event.invalidation_price is None
            or event.trigger.invalidation_reason is not None
            or event.setup_time is None
            or event.source_sha256 is None
        ):
            return failed("source_event_invalid")
        _utc(event.observed_at)
        if event.observed_at > now or now >= event.trigger.expires_at:
            return failed("source_event_time_invalid")
        market, analysis, frames, digest = _copy_source(
            market, analysis, event.observed_at
        )
        if digest != event.source_sha256:
            return failed("source_sha256_mismatch")
        duration = event.trigger.expires_at - event.trigger.trigger_time
        if duration.microseconds:
            return failed("source_event_invalid")
        ttl = duration.days * 86400 + duration.seconds
        # Reconstructing with a caller-supplied expiry cannot authenticate that
        # configuration. Existing timing policy is the maximum; shorter TTLs
        # remain conservative. This is not the consumed-event/timing gate.
        maximum_ttl = TIMING_POLICIES[event.strategy].trigger_ttl_seconds
        if ttl > maximum_ttl:
            return failed("source_event_ttl_exceeds_policy")
        if now >= event.trigger.trigger_time + timedelta(seconds=maximum_ttl):
            return failed("source_event_time_invalid")
        rebuilt = extract_trigger(
            market,
            analysis,
            report_id=event.report_id,
            strategy=event.strategy,
            direction=event.direction,
            observed_at=event.observed_at,
            trigger_ttl_seconds=ttl,
        )
        if rebuilt != event:
            return failed("source_event_reconstruction_mismatch")
        identity.update(
            report_id=event.report_id,
            instrument_id=event.instrument_id,
            direction=event.direction,
            source_sha256=digest,
        )
        if analysis.blockers:
            return failed("source_data_blockers")
        bid, ask = (
            _decimal(market.ticker.bid, positive=True),
            _decimal(market.ticker.ask, positive=True),
        )
        if bid > ask or market.ticker.bid_size <= 0 or market.ticker.ask_size <= 0:
            return failed("source_spread_invalid")
        with localcontext(Context(prec=100)):
            spread = ask - bid
            # An explicit cost scenario cannot erase observed spread/slippage.
            # Completeness of fees and funding is an authoritative phase-9 check.
            if cost < spread / price * D(10000) + slip:
                return failed("cost_below_observed_friction")
            stops, targets = _source_anchors(frames, event)
            thesis = D(
                dict(event.setup_basis).get(
                    "invalidation_unrounded", str(event.invalidation_price)
                )
            )
            policy = _Policy(
                price,
                event.direction,
                tick,
                spread,
                slip,
                cost,
                minimum_rr,
                minimum_atr,
                multiplier,
                minimum_bps,
                thesis,
            )
            stop_results, target_results, alternatives, selected = _evaluate(
                stops, targets, policy
            )
        failures = (
            ()
            if selected is not None
            else (
                "structural_stop_missing"
                if not stops
                else "structural_target_missing"
                if not targets
                else "no_valid_structural_bracket",
            )
        )
        missing = ["measured_objective_not_defined", "non_pivot_liquidity_not_observed"]
        if not any(item.source.startswith("equal_") for item in stops + targets):
            missing.append("equal_pivot_pool_not_observed")
        return StructuralProtectionSelection(
            **identity,
            stops=stop_results,
            targets=target_results,
            alternatives=alternatives,
            selected=selected,
            fail_codes=failures,
            missing_evidence=tuple(missing),
            selection_reason=_RANKING if selected else "all_brackets_rejected",
            spread=spread,
            expected_slippage_bps=slip,
            cost_bps=cost,
            strategy=event.strategy,
            event_setup_basis=event.setup_basis,
            policy_inputs=(
                ("atr_buffer_multiplier", multiplier),
                ("cost_bps", cost),
                ("expected_slippage_bps", slip),
                ("min_net_rr", minimum_rr),
                ("min_stop_distance_atr", minimum_atr),
                ("minimum_buffer_bps", minimum_bps),
                ("spread", spread),
                ("tick_size", tick),
            ),
        )
    except _INVALID:
        return failed("structural_source_invalid")


def _legacy_selection(
    analysis, *, direction, entry, timeframes, multiplier, minimum_bps
):
    """Same enumeration, explicitly incomplete and unauthenticated evidence."""
    stops, targets = [], []
    frames = tuple(islice(timeframes, 4))
    if (
        len(frames) > 3
        or len(set(frames)) != len(frames)
        or not set(frames) <= set(TIMEFRAMES)
    ):
        raise ValueError("invalid structural timeframes")
    if type(analysis) is not MultiTimeframeAnalysis:
        raise ValueError("invalid analysis")
    raw = _checked_tree(analysis.model_dump(mode="python", serialize_as_any=True))
    copied = MultiTimeframeAnalysis.model_validate(raw, strict=True)
    now = _utc(copied.generated_at)
    if copied.blockers:
        return None
    for timeframe in sorted(frames):
        view = copied.timeframe_analyses.get(timeframe)
        if view is None:
            continue
        if (
            not view.data_quality_ok
            or view.data_quality_issues
            or view.timeframe != timeframe
            or _utc(view.last_closed_at) > now
        ):
            return None
        for kind in ("support", "resistance"):
            if len(getattr(view.structure, f"{kind}_levels")) > _MAX_ANCHORS:
                raise ValueError("structural_candidate_limit")
            values = set(getattr(view.structure, f"{kind}_levels"))
            swing = (
                view.structure.last_swing_low
                if kind == "support"
                else view.structure.last_swing_high
            )
            if swing is not None:
                values.add(swing)
            destination = (
                stops if (kind == "support") == (direction == "long") else targets
            )
            for value in sorted(values):
                _decimal(value, positive=True)
                destination.append(
                    _anchor(
                        timeframe,
                        f"legacy_{kind}",
                        value,
                        view.last_closed_at,
                        view.last_closed_at,
                        view.indicators.atr14,
                    )
                )
    stop_results, target_results, alternatives, selected = _evaluate(
        _ordered(stops),
        _ordered(targets),
        _Policy(
            entry,
            direction,
            None,
            D(0),
            D(0),
            D(0),
            D(0),
            D(0),
            multiplier,
            minimum_bps,
            None,
        ),
    )
    return StructuralProtectionSelection(
        report_id=None,
        instrument_id=copied.instrument_id,
        direction=direction,
        reference_entry=entry,
        observed_at=now,
        source_sha256=None,
        evidence="legacy_incomplete",
        stops=stop_results,
        targets=target_results,
        alternatives=alternatives,
        selected=selected,
        fail_codes=("legacy_source_incomplete",),
        missing_evidence=(
            "confirmed_ohlc",
            "source_event",
            "tick",
            "spread",
            "slippage",
            "costs",
        ),
        selection_reason=_RANKING if selected else "all_brackets_rejected",
        policy_inputs=(
            ("atr_buffer_multiplier", multiplier),
            ("minimum_buffer_bps", minimum_bps),
        ),
    )


def structural_protection_geometry(
    analysis: MultiTimeframeAnalysis,
    *,
    direction: Literal["long", "short"],
    entry: Decimal,
    timeframes: Iterable[str] = TIMEFRAMES,
    atr_buffer_multiplier: Decimal = _LEGACY_ATR_MULTIPLIER,
    minimum_buffer_bps: Decimal = _LEGACY_MINIMUM_BPS,
) -> StructuralProtectionGeometry | None:
    """Compatibility-only incomplete proposal, not source-qualified protection.

    Defaults are historical engineering inputs, not calibrated limits. All
    supplied candidates use the shared selector. Missing ATR is unknown, not
    zero; no runtime eligibility is granted. The raw target is preserved by the
    legacy geometry contract, without fabricated tick or cost assumptions.
    """
    if type(direction) is not str or direction not in {"long", "short"}:
        raise ValueError("invalid direction")
    _decimal(entry, positive=True)
    _decimal(atr_buffer_multiplier)
    _decimal(minimum_buffer_bps, positive=True)
    try:
        with localcontext(Context(prec=100)):
            selection = _legacy_selection(
                analysis,
                direction=direction,
                entry=entry,
                timeframes=timeframes,
                multiplier=atr_buffer_multiplier,
                minimum_bps=minimum_buffer_bps,
            )
            if selection is None or selection.selected is None:
                return None
            chosen = selection.selected
            return StructuralProtectionGeometry(
                timeframe=chosen.stop.anchor.timeframe,
                source_closed_at=max(
                    chosen.stop.anchor.source_closed_at,
                    chosen.target.anchor.source_closed_at,
                ),
                reference_entry=entry,
                stop_anchor=chosen.stop.anchor.anchor_price,
                target_anchor=chosen.target.anchor.anchor_price,
                volatility_buffer=chosen.stop.buffer,
                stop_loss=chosen.stop.final_stop,
                take_profit=chosen.target.final_target,
                gross_risk_reward=chosen.gross_rr,
            )
    except _INVALID:
        return None
