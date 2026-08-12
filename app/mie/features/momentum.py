from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.mie.features._math import EPSILON, clamp, log_prices, rms
from app.mie.features.models import MomentumFeatures

D = Decimal


def momentum_features(
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal] | None = None,
    *,
    fast_bars: int = 5,
    slow_bars: int = 20,
) -> MomentumFeatures | None:
    """Measure scale-normalized momentum without assigning probability."""

    if (
        fast_bars < 2
        or slow_bars <= fast_bars
        or len(closes) < slow_bars + 1
    ):
        return None
    try:
        logs = log_prices(closes)
    except ValueError:
        return None
    returns = [
        current - previous
        for previous, current in zip(logs[:-1], logs[1:], strict=True)
    ]
    return_rms = rms(returns[-slow_bars:])
    scale = max(return_rms, EPSILON)
    fast_return = logs[-1] - logs[-fast_bars - 1]
    slow_return = logs[-1] - logs[-slow_bars - 1]
    fast_normalized = fast_return / (scale * D(fast_bars).sqrt())
    slow_normalized = slow_return / (scale * D(slow_bars).sqrt())
    normalized = clamp(
        D("0.60") * fast_normalized + D("0.40") * slow_normalized,
        D("-10"),
        D("10"),
    )

    if normalized > D("0.25"):
        direction = "rising"
        aligned = sum(value > 0 for value in returns[-slow_bars:])
    elif normalized < D("-0.25"):
        direction = "falling"
        aligned = sum(value < 0 for value in returns[-slow_bars:])
    else:
        direction = "flat"
        aligned = 0
    persistence = D(aligned) / D(slow_bars)

    volume_ratio: Decimal | None = None
    if volumes is not None:
        if len(volumes) != len(closes):
            return None
        selected = list(volumes[-slow_bars - 1:])
        if not all(value.is_finite() and value >= 0 for value in selected):
            return None
        baseline = sum(selected[:-1], D("0")) / D(slow_bars)
        if baseline > 0:
            volume_ratio = selected[-1] / baseline
    volume_confirmation = (
        D("0.50")
        if volume_ratio is None
        else clamp(volume_ratio / D("1.50"), D("0"), D("1"))
    )
    strength = clamp(
        min(D("1"), abs(normalized) / D("3"))
        * (D("0.50") + D("0.50") * persistence)
        * (D("0.50") + D("0.50") * volume_confirmation),
        D("0"),
        D("1"),
    )

    return MomentumFeatures(
        fast_bars=fast_bars,
        slow_bars=slow_bars,
        fast_log_return=+fast_return,
        slow_log_return=+slow_return,
        normalized_momentum=+normalized,
        directional_persistence=+persistence,
        volume_ratio=None if volume_ratio is None else +volume_ratio,
        volume_confirmation=+volume_confirmation,
        strength=+strength,
        direction=direction,
    )
