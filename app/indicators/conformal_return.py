from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, localcontext
from typing import Sequence

from app.indicators.causal_trend import causal_log_trend_from_logs

D = Decimal
_MINIMUM_PREQUENTIAL_CALIBRATION = 20
_RESIDUAL_NUMERICAL_FLOOR = D("1e-24")


@dataclass(frozen=True)
class CausalReturnIntervalEstimate:
    """Past-only prequential conformal interval for a one-bar log return."""

    horizon_bars: int
    confidence_level: Decimal
    predicted_log_return: Decimal
    lower_log_return: Decimal
    upper_log_return: Decimal
    half_width: Decimal
    calibration_size: int
    coverage_sample_size: int
    empirical_coverage: Decimal
    direction: str


def _finite_sample_quantile(
    residuals: Sequence[Decimal], confidence_level: Decimal
) -> Decimal:
    ordered = sorted(residuals)
    rank = int(
        (D(len(ordered) + 1) * confidence_level).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    rank = min(len(ordered), max(1, rank))
    return ordered[rank - 1]


def causal_return_interval(
    closes: Sequence[Decimal],
    *,
    trend_window: int = 21,
    calibration_size: int = 60,
    confidence_level: Decimal = D("0.90"),
) -> CausalReturnIntervalEstimate | None:
    """Calibrate a one-bar interval without reading any future candle.

    Each calibration residual is produced by a trend fitted only through the
    candle immediately before that residual. The final prediction uses all
    supplied closes and therefore remains causal at the live endpoint.
    """

    if (
        trend_window < 5
        or calibration_size < 20
        or not D("0.50") < confidence_level < D("1")
        or len(closes) < trend_window + calibration_size
    ):
        return None
    values = list(closes)
    if any(not value.is_finite() or value <= 0 for value in values):
        return None

    with localcontext() as context:
        context.prec = 50
        logs = [value.ln() for value in values]
        residuals: list[Decimal] = []
        first_target = len(values) - calibration_size
        for target_index in range(first_target, len(values)):
            estimate = causal_log_trend_from_logs(
                logs[:target_index], window=trend_window
            )
            if estimate is None:
                return None
            predicted = (
                estimate.log_velocity_per_bar
                + estimate.log_acceleration_per_bar2 / D("2")
            )
            observed = logs[target_index] - logs[target_index - 1]
            residuals.append(
                max(_RESIDUAL_NUMERICAL_FLOOR, abs(observed - predicted))
            )

        # Evaluate calibration honestly: each outcome is compared against a
        # quantile formed only from residuals available before that outcome.
        covered = 0
        coverage_sample_size = 0
        for index in range(_MINIMUM_PREQUENTIAL_CALIBRATION, len(residuals)):
            prior_half_width = _finite_sample_quantile(
                residuals[:index], confidence_level
            )
            covered += int(residuals[index] <= prior_half_width)
            coverage_sample_size += 1
        if coverage_sample_size == 0:
            return None
        empirical_coverage = D(covered) / D(coverage_sample_size)
        half_width = _finite_sample_quantile(residuals, confidence_level)

        endpoint = causal_log_trend_from_logs(logs, window=trend_window)
        if endpoint is None:
            return None
        predicted = (
            endpoint.log_velocity_per_bar
            + endpoint.log_acceleration_per_bar2 / D("2")
        )
        lower = predicted - half_width
        upper = predicted + half_width
        if lower > 0:
            direction = "rising"
        elif upper < 0:
            direction = "falling"
        else:
            direction = "uncertain"

        return CausalReturnIntervalEstimate(
            horizon_bars=1,
            confidence_level=+confidence_level,
            predicted_log_return=+predicted,
            lower_log_return=+lower,
            upper_log_return=+upper,
            half_width=+half_width,
            calibration_size=len(residuals),
            coverage_sample_size=coverage_sample_size,
            empirical_coverage=+empirical_coverage,
            direction=direction,
        )
