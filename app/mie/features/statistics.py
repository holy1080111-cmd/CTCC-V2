from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.mie.features._math import EPSILON, log_returns_from_prices, median
from app.mie.features.models import StatisticsFeatures

D = Decimal


def statistical_features(
    closes: Sequence[Decimal],
) -> StatisticsFeatures | None:
    """Return descriptive log-return statistics without a market claim."""

    if len(closes) < 3:
        return None
    try:
        returns = log_returns_from_prices(closes)
    except ValueError:
        return None

    sample_size = len(returns)
    mean = sum(returns, D("0")) / D(sample_size)
    variance = sum(
        ((value - mean) ** 2 for value in returns), D("0")
    ) / D(sample_size)
    center = median(returns)
    mad_scale = median([abs(value - center) for value in returns]) * D(
        "1.4826"
    )
    downside_deviation = (
        sum((min(value, D("0")) ** 2 for value in returns), D("0"))
        / D(sample_size)
    ).sqrt()
    outlier_threshold = D("3") * max(mad_scale, EPSILON)
    outlier_count = sum(
        abs(value - center) > outlier_threshold for value in returns
    )

    return StatisticsFeatures(
        sample_size=sample_size,
        mean_log_return=+mean,
        return_std=+variance.sqrt(),
        median_log_return=+center,
        mad_scale=+mad_scale,
        downside_deviation=+downside_deviation,
        outlier_fraction=+(D(outlier_count) / D(sample_size)),
    )
