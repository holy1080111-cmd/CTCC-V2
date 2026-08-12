from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.mie.features._math import clamp
from app.mie.features.models import FeatureBar, GeometryFeatures, SwingPoint

D = Decimal


def _is_unique_extreme(
    values: Sequence[Decimal], index: int, *, maximum: bool
) -> bool:
    candidate = values[index]
    extreme = max(values) if maximum else min(values)
    return candidate == extreme and values.count(extreme) == 1


def confirmed_geometry_features(
    bars: Sequence[FeatureBar],
    *,
    left_bars: int = 2,
    right_bars: int = 2,
) -> GeometryFeatures | None:
    """Find pivots only after every right-side confirmation bar has closed."""

    if left_bars < 1 or right_bars < 1:
        return None
    minimum_size = left_bars + right_bars + 1
    if len(bars) < minimum_size:
        return None
    if any(
        current.closed_at <= previous.closed_at
        for previous, current in zip(
            bars[:-1], bars[1:], strict=True
        )
    ):
        return None

    pivots: list[SwingPoint] = []
    for index in range(left_bars, len(bars) - right_bars):
        start = index - left_bars
        end = index + right_bars + 1
        selected = bars[start:end]
        relative_index = left_bars
        highs = [bar.high for bar in selected]
        lows = [bar.low for bar in selected]
        confirmed_at = bars[index + right_bars].closed_at

        is_swing_high = _is_unique_extreme(
            highs, relative_index, maximum=True
        )
        is_swing_low = _is_unique_extreme(
            lows, relative_index, maximum=False
        )
        # An outside bar can be both the unique high and unique low.  That is
        # structurally ambiguous, so it must not emit contradictory pivots.
        if is_swing_high and is_swing_low:
            continue

        if is_swing_high:
            pivots.append(
                SwingPoint(
                    kind="high",
                    index=index,
                    price=bars[index].high,
                    occurred_at=bars[index].closed_at,
                    confirmed_at=confirmed_at,
                )
            )
        if is_swing_low:
            pivots.append(
                SwingPoint(
                    kind="low",
                    index=index,
                    price=bars[index].low,
                    occurred_at=bars[index].closed_at,
                    confirmed_at=confirmed_at,
                )
            )

    highs = [point for point in pivots if point.kind == "high"]
    lows = [point for point in pivots if point.kind == "low"]
    last_high = max(highs, key=lambda item: item.index) if highs else None
    last_low = max(lows, key=lambda item: item.index) if lows else None
    close = bars[-1].close
    supports = [point.price for point in lows if point.price < close]
    resistances = [point.price for point in highs if point.price > close]
    nearest_support = max(supports) if supports else None
    nearest_resistance = min(resistances) if resistances else None

    range_position: Decimal | None = None
    if last_high is not None and last_low is not None:
        lower = min(last_low.price, last_high.price)
        upper = max(last_low.price, last_high.price)
        if upper > lower:
            range_position = clamp(
                (close - lower) / (upper - lower), D("0"), D("1")
            )

    return GeometryFeatures(
        pivot_left_bars=left_bars,
        pivot_right_bars=right_bars,
        confirmed_pivot_count=len(pivots),
        last_swing_high=last_high,
        last_swing_low=last_low,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        range_position=range_position,
    )
