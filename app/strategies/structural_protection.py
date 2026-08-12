from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Literal

from app.domain.analysis import MultiTimeframeAnalysis, TimeframeAnalysis
from app.domain.strategy import StructuralProtectionGeometry

D = Decimal


def _levels(view: TimeframeAnalysis, kind: Literal["support", "resistance"]) -> set[Decimal]:
    structure = view.structure
    if kind == "support":
        values = set(structure.support_levels)
        if structure.last_swing_low is not None:
            values.add(structure.last_swing_low)
        return values
    values = set(structure.resistance_levels)
    if structure.last_swing_high is not None:
        values.add(structure.last_swing_high)
    return values


def structural_protection_geometry(
    analysis: MultiTimeframeAnalysis,
    *,
    direction: Literal["long", "short"],
    entry: Decimal,
    timeframes: Iterable[str] = ("15m", "1H", "4H"),
    atr_buffer_multiplier: Decimal = D("0.25"),
    minimum_buffer_bps: Decimal = D("5"),
) -> StructuralProtectionGeometry | None:
    """Return the first complete, confirmed structural bracket.

    ``analysis`` is already built exclusively from confirmed candles.  This
    function consumes only that immutable snapshot and therefore cannot see a
    candle that arrives after the strategy decision.
    """

    if entry <= 0 or atr_buffer_multiplier < 0 or minimum_buffer_bps <= 0:
        raise ValueError("invalid structural protection configuration")

    minimum_buffer = entry * minimum_buffer_bps / D("10000")
    for timeframe in timeframes:
        view = analysis.timeframe_analyses.get(timeframe)
        if view is None or not view.data_quality_ok:
            continue
        atr = view.indicators.atr14 or D("0")
        buffer = max(minimum_buffer, atr * atr_buffer_multiplier)

        supports = _levels(view, "support")
        resistances = _levels(view, "resistance")
        if direction == "long":
            stop_candidates = [level for level in supports if level < entry]
            target_candidates = [level for level in resistances if level > entry]
            if not stop_candidates or not target_candidates:
                continue
            stop_anchor = max(stop_candidates)
            target_anchor = min(target_candidates)
            stop_loss = stop_anchor - buffer
            take_profit = target_anchor
            if stop_loss <= 0 or not stop_loss < entry < take_profit:
                continue
            risk = entry - stop_loss
            reward = take_profit - entry
        else:
            stop_candidates = [level for level in resistances if level > entry]
            target_candidates = [level for level in supports if level < entry]
            if not stop_candidates or not target_candidates:
                continue
            stop_anchor = min(stop_candidates)
            target_anchor = max(target_candidates)
            stop_loss = stop_anchor + buffer
            take_profit = target_anchor
            if not take_profit < entry < stop_loss:
                continue
            risk = stop_loss - entry
            reward = entry - take_profit

        if risk <= 0 or reward <= 0:
            continue
        return StructuralProtectionGeometry(
            timeframe=timeframe,
            source_closed_at=view.last_closed_at,
            reference_entry=entry,
            stop_anchor=stop_anchor,
            target_anchor=target_anchor,
            volatility_buffer=buffer,
            stop_loss=stop_loss,
            take_profit=take_profit,
            gross_risk_reward=reward / risk,
        )
    return None
