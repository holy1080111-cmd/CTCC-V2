from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Sequence

D = Decimal
_EPSILON = D("1e-30")
_MINIMUM_SCALE = D("1e-10")
_HUBER_Z = D("2.5")
_CREDIBLE_Z = D("1.96")


@dataclass(frozen=True)
class CausalStateEstimate:
    """Robust constant-acceleration state estimate using past closes only."""

    window: int
    log_velocity_per_bar: Decimal
    log_acceleration_per_bar2: Decimal
    velocity_std: Decimal
    acceleration_std: Decimal
    velocity_z: Decimal
    acceleration_z: Decimal
    innovation_z: Decimal
    shock_score: Decimal
    confidence: Decimal
    direction: str
    outlier_count: int


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def _median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / D("2")


def _transpose(matrix: list[list[Decimal]]) -> list[list[Decimal]]:
    return [list(column) for column in zip(*matrix, strict=True)]


def _multiply(
    left: list[list[Decimal]], right: list[list[Decimal]]
) -> list[list[Decimal]]:
    transposed = _transpose(right)
    return [
        [sum((a * b for a, b in zip(row, column, strict=True)), D("0")) for column in transposed]
        for row in left
    ]


def _add(
    left: list[list[Decimal]], right: list[list[Decimal]]
) -> list[list[Decimal]]:
    return [
        [a + b for a, b in zip(left_row, right_row, strict=True)]
        for left_row, right_row in zip(left, right, strict=True)
    ]


def _predict_covariance(
    covariance: list[list[Decimal]],
    transition: list[list[Decimal]],
    process_noise: list[list[Decimal]],
) -> list[list[Decimal]]:
    return _add(
        _multiply(_multiply(transition, covariance), _transpose(transition)),
        process_noise,
    )


def _joseph_update(
    predicted: list[list[Decimal]],
    gain: list[Decimal],
    observation_variance: Decimal,
) -> list[list[Decimal]]:
    identity_minus_kh = [
        [
            (D("1") if row == column else D("0"))
            - (gain[row] if column == 0 else D("0"))
            for column in range(3)
        ]
        for row in range(3)
    ]
    covariance = _multiply(
        _multiply(identity_minus_kh, predicted),
        _transpose(identity_minus_kh),
    )
    for row in range(3):
        for column in range(3):
            covariance[row][column] += (
                gain[row] * observation_variance * gain[column]
            )

    # Decimal round-off can create tiny asymmetry or negative diagonal noise.
    for row in range(3):
        covariance[row][row] = max(covariance[row][row], _EPSILON)
        for column in range(row + 1, 3):
            symmetric = (covariance[row][column] + covariance[column][row]) / D("2")
            covariance[row][column] = symmetric
            covariance[column][row] = symmetric
    return covariance


def causal_state_estimate(
    closes: Sequence[Decimal], window: int = 34
) -> CausalStateEstimate | None:
    """Estimate level, velocity, acceleration, uncertainty, and shock risk.

    The filter is causal and bounded to the latest ``window`` confirmed closes.
    A Huber innovation weight inflates observation variance for isolated price
    shocks, so a wick cannot receive more influence than an ordinary sample.
    """

    if window < 20 or len(closes) < window:
        return None
    values = list(closes[-window:])
    if any(not value.is_finite() or value <= 0 for value in values):
        return None

    with localcontext() as context:
        context.prec = 50
        logs = [value.ln() for value in values]
        returns = [
            current - previous
            for previous, current in zip(logs[:-1], logs[1:], strict=True)
        ]
        median_return = _median(returns)
        deviations = [abs(value - median_return) for value in returns]
        mad_scale = _median(deviations) * D("1.4826")
        residual_rms = (
            sum(((value - median_return) ** 2 for value in returns), D("0"))
            / D(len(returns))
        ).sqrt()
        scale = max(
            mad_scale,
            residual_rms * D("0.50"),
            abs(median_return) * D("0.02"),
            _MINIMUM_SCALE,
        )
        variance = scale * scale

        transition = [
            [D("1"), D("1"), D("0.5")],
            [D("0"), D("1"), D("1")],
            [D("0"), D("0"), D("1")],
        ]
        process_scale = variance * D("0.02")
        process_noise = [
            [process_scale / D("36"), process_scale / D("12"), process_scale / D("6")],
            [process_scale / D("12"), process_scale / D("4"), process_scale / D("2")],
            [process_scale / D("6"), process_scale / D("2"), process_scale],
        ]
        observation_variance = variance
        state = [logs[0], median_return, D("0")]
        covariance = [
            [variance * D("4"), D("0"), D("0")],
            [D("0"), variance, D("0")],
            [D("0"), D("0"), variance / D("4")],
        ]
        innovation_scores: list[Decimal] = []
        shock_scores: list[Decimal] = []
        outlier_count = 0

        for observation in logs[1:]:
            predicted_state = [
                state[0] + state[1] + state[2] / D("2"),
                state[1] + state[2],
                state[2],
            ]
            predicted_covariance = _predict_covariance(
                covariance, transition, process_noise
            )
            innovation = observation - predicted_state[0]
            nominal_innovation_variance = (
                predicted_covariance[0][0] + observation_variance
            )
            nominal_std = max(nominal_innovation_variance, _EPSILON).sqrt()
            nominal_z = innovation / nominal_std
            bounded_z = _clamp(nominal_z, D("-20"), D("20"))
            innovation_scores.append(bounded_z)

            absolute_z = abs(nominal_z)
            if absolute_z > _HUBER_Z:
                outlier_count += 1
                robust_weight = _HUBER_Z / absolute_z
            else:
                robust_weight = D("1")
            effective_observation_variance = observation_variance / (
                robust_weight * robust_weight
            )
            innovation_variance = (
                predicted_covariance[0][0] + effective_observation_variance
            )
            gain = [
                predicted_covariance[row][0] / innovation_variance
                for row in range(3)
            ]
            state = [
                predicted_state[row] + gain[row] * innovation
                for row in range(3)
            ]
            covariance = _joseph_update(
                predicted_covariance,
                gain,
                effective_observation_variance,
            )
            shock_scores.append(
                _clamp(
                    (absolute_z - _HUBER_Z) / D("5"),
                    D("0"),
                    D("1"),
                )
            )

        velocity_std = covariance[1][1].sqrt()
        acceleration_std = covariance[2][2].sqrt()
        velocity_z = _clamp(
            state[1] / max(velocity_std, _EPSILON), D("-20"), D("20")
        )
        acceleration_z = _clamp(
            state[2] / max(acceleration_std, _EPSILON), D("-20"), D("20")
        )
        innovation_z = innovation_scores[-1] if innovation_scores else D("0")

        recent_shocks = shock_scores[-5:]
        weights = [D("0.0625"), D("0.0625"), D("0.125"), D("0.25"), D("0.50")]
        active_weights = weights[-len(recent_shocks):]
        weighted_shock = (
            sum(
                (
                    weight * shock
                    for weight, shock in zip(
                        active_weights, recent_shocks, strict=True
                    )
                ),
                D("0"),
            )
            / sum(active_weights, D("0"))
            if recent_shocks
            else D("0")
        )
        shock_score = _clamp(
            max(weighted_shock, recent_shocks[-1] if recent_shocks else D("0")),
            D("0"),
            D("1"),
        )

        significance = _clamp(
            (abs(velocity_z) - D("1")) / D("3"), D("0"), D("1")
        )
        confidence = _clamp(
            significance * (D("1") - shock_score), D("0"), D("1")
        )
        lower_velocity = state[1] - _CREDIBLE_Z * velocity_std
        upper_velocity = state[1] + _CREDIBLE_Z * velocity_std
        if lower_velocity > 0:
            direction = "rising"
        elif upper_velocity < 0:
            direction = "falling"
        else:
            direction = "flat"

        return CausalStateEstimate(
            window=window,
            log_velocity_per_bar=+state[1],
            log_acceleration_per_bar2=+state[2],
            velocity_std=+velocity_std,
            acceleration_std=+acceleration_std,
            velocity_z=+velocity_z,
            acceleration_z=+acceleration_z,
            innovation_z=+innovation_z,
            shock_score=+shock_score,
            confidence=+confidence,
            direction=direction,
            outlier_count=outlier_count,
        )
