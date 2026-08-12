from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.mie.features._math import EPSILON, clamp, log_returns_from_prices, rms
from app.mie.features.models import SignalFeatures

D = Decimal


def causal_signal_features(
    closes: Sequence[Decimal],
    *,
    alpha: Decimal = D("0.25"),
) -> SignalFeatures | None:
    """Apply a past-only EWMA to returns and quantify residual noise."""

    if len(closes) < 6 or not alpha.is_finite() or not D("0") < alpha <= D("1"):
        return None
    try:
        returns = log_returns_from_prices(closes)
    except ValueError:
        return None

    smoothed = returns[0]
    residuals = [D("0")]
    for value in returns[1:]:
        smoothed = alpha * value + (D("1") - alpha) * smoothed
        residuals.append(value - smoothed)

    raw_rms = rms(returns)
    residual_rms = rms(residuals)
    noise_ratio = clamp(
        residual_rms / max(raw_rms, EPSILON), D("0"), D("1")
    )
    strength = clamp(
        abs(smoothed) / (abs(smoothed) + residual_rms + EPSILON),
        D("0"),
        D("1"),
    )
    flat_threshold = max(EPSILON, residual_rms * D("0.10"))
    if smoothed > flat_threshold:
        direction = "rising"
    elif smoothed < -flat_threshold:
        direction = "falling"
    else:
        direction = "flat"

    return SignalFeatures(
        sample_size=len(returns),
        alpha=+alpha,
        smoothed_log_return=+smoothed,
        raw_return_rms=+raw_rms,
        residual_rms=+residual_rms,
        noise_ratio=+noise_ratio,
        strength=+strength,
        direction=direction,
    )
