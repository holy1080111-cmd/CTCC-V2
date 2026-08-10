from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Sequence

D = Decimal
_EPSILON = D("1e-30")


@dataclass(frozen=True)
class CausalTrendEstimate:
    """Endpoint derivatives from a past-only local quadratic log-price fit."""

    window: int
    log_velocity_per_bar: Decimal
    log_acceleration_per_bar2: Decimal
    log_return_rms_per_bar: Decimal
    velocity_to_volatility: Decimal
    acceleration_to_volatility: Decimal
    fit_r2: Decimal
    residual_std: Decimal
    confidence: Decimal
    direction: str


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def _solve_3x3(
    matrix: list[list[Decimal]], rhs: list[Decimal]
) -> tuple[Decimal, Decimal, Decimal] | None:
    augmented = [row[:] + [value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(3):
        pivot_row = max(
            range(column, 3), key=lambda row: abs(augmented[row][column])
        )
        pivot = augmented[pivot_row][column]
        if abs(pivot) <= _EPSILON:
            return None
        if pivot_row != column:
            augmented[column], augmented[pivot_row] = (
                augmented[pivot_row],
                augmented[column],
            )

        pivot = augmented[column][column]
        augmented[column] = [value / pivot for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0:
                continue
            augmented[row] = [
                current - factor * source
                for current, source in zip(
                    augmented[row], augmented[column], strict=True
                )
            ]
    return augmented[0][3], augmented[1][3], augmented[2][3]


def causal_log_trend(
    closes: Sequence[Decimal], window: int = 21
) -> CausalTrendEstimate | None:
    """Estimate causal log-price slope, curvature, noise, and fit confidence.

    Only the latest ``window`` observations are used.  The endpoint is x=0 and
    all other samples have x<0, so this estimator never reads a future sample.
    A linear recency weight from 1 to 3 reduces the influence of older bars.
    """

    if window < 5 or len(closes) < window:
        return None
    values = list(closes[-window:])
    if any(not value.is_finite() or value <= 0 for value in values):
        return None

    with localcontext() as context:
        context.prec = 50
        logs = [value.ln() for value in values]
    return causal_log_trend_from_logs(logs, window=window)


def causal_log_trend_from_logs(
    log_prices: Sequence[Decimal], window: int = 21
) -> CausalTrendEstimate | None:
    """Apply the causal trend fit to already-computed log prices.

    This is mathematically identical to :func:`causal_log_trend`; it avoids
    recomputing Decimal logarithms during sequential calibration.
    """

    if window < 5 or len(log_prices) < window:
        return None
    logs = list(log_prices[-window:])
    if any(not value.is_finite() for value in logs):
        return None

    with localcontext() as context:
        context.prec = 50
        span = D(window - 1)
        last_log = logs[-1]
        samples: list[tuple[Decimal, Decimal, Decimal]] = []
        for index, value in enumerate(logs):
            x = D(index - (window - 1)) / span
            y = value - last_log
            weight = D("1") + D("2") * D(index) / span
            samples.append((x, y, weight))

        moments = [
            sum(
                (
                    weight * (D("1") if power == 0 else x**power)
                    for x, _, weight in samples
                ),
                D("0"),
            )
            for power in range(5)
        ]
        targets = [
            sum(
                (
                    weight * y * (D("1") if power == 0 else x**power)
                    for x, y, weight in samples
                ),
                D("0"),
            )
            for power in range(3)
        ]
        coefficients = _solve_3x3(
            [
                [moments[0], moments[1], moments[2]],
                [moments[1], moments[2], moments[3]],
                [moments[2], moments[3], moments[4]],
            ],
            targets,
        )
        if coefficients is None:
            return None
        intercept, linear, quadratic = coefficients

        weight_sum = moments[0]
        weighted_mean = targets[0] / weight_sum
        squared_error = D("0")
        total_variation = D("0")
        for x, y, weight in samples:
            fitted = intercept + linear * x + quadratic * x * x
            squared_error += weight * (y - fitted) ** 2
            total_variation += weight * (y - weighted_mean) ** 2

        if total_variation <= _EPSILON:
            fit_r2 = D("1") if squared_error <= _EPSILON else D("0")
        else:
            fit_r2 = _clamp(
                D("1") - squared_error / total_variation, D("0"), D("1")
            )
        residual_std = (max(D("0"), squared_error) / weight_sum).sqrt()

        log_returns = [
            current - previous
            for previous, current in zip(logs[:-1], logs[1:], strict=True)
        ]
        return_rms = (
            sum((value * value for value in log_returns), D("0")) / span
        ).sqrt()

        velocity = linear / span
        acceleration = D("2") * quadratic / (span * span)
        scale = max(return_rms, _EPSILON)
        velocity_ratio = _clamp(velocity / scale, D("-10"), D("10"))
        acceleration_ratio = _clamp(
            acceleration / scale, D("-10"), D("10")
        )
        signal_strength = min(D("1"), abs(velocity_ratio))
        confidence = _clamp(fit_r2 * signal_strength, D("0"), D("1"))

        flat_threshold = max(_EPSILON, return_rms * D("0.10"))
        if velocity > flat_threshold:
            direction = "rising"
        elif velocity < -flat_threshold:
            direction = "falling"
        else:
            direction = "flat"

        return CausalTrendEstimate(
            window=window,
            log_velocity_per_bar=+velocity,
            log_acceleration_per_bar2=+acceleration,
            log_return_rms_per_bar=+return_rms,
            velocity_to_volatility=+velocity_ratio,
            acceleration_to_volatility=+acceleration_ratio,
            fit_r2=+fit_r2,
            residual_std=+residual_std,
            confidence=+confidence,
            direction=direction,
        )
