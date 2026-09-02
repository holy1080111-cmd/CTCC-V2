"""Deterministic probability metrics for offline MIE Gate 3 evidence.

All public calculations use finite :class:`~decimal.Decimal` inputs and return
immutable values. There are no model, market-data, runtime, or execution hooks.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext


DECIMAL_PRECISION = 50
LOG_LOSS_EPSILON = Decimal("1e-15")


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    """One equal-width calibration bin, including explicit empty bins."""

    index: int
    lower_bound: Decimal
    upper_bound: Decimal
    mean_prediction: Decimal | None
    observed_frequency: Decimal | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class ProbabilityMetrics:
    brier_score: Decimal
    log_loss: Decimal
    expected_calibration_error: Decimal
    sample_count: int
    reliability_bins: tuple[ReliabilityBin, ...]


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    estimate: Decimal
    confidence_lower: Decimal
    confidence_upper: Decimal
    confidence_level: Decimal
    resamples: int
    block_length: int
    seed: int


@dataclass(frozen=True, slots=True)
class AdjustedPValue:
    test_id: str
    raw_p_value: Decimal
    adjusted_p_value: Decimal
    rejected: bool


def _finite_decimal(value: Decimal | int, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError(f"{name} must be a Decimal or integer")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _validated_probabilities(
    probabilities: Sequence[Decimal | int],
) -> tuple[Decimal, ...]:
    values = tuple(
        _finite_decimal(value, "probability") for value in probabilities
    )
    if not values:
        raise ValueError("probabilities cannot be empty")
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("probabilities must lie inside the unit interval")
    return values


def _validated_outcomes(outcomes: Sequence[int | bool]) -> tuple[int, ...]:
    values = tuple(outcomes)
    if not values:
        raise ValueError("binary outcomes cannot be empty")
    if any(
        not isinstance(value, (int, bool)) or int(value) not in (0, 1)
        for value in values
    ):
        raise ValueError("binary outcomes must contain only zero or one")
    return tuple(int(value) for value in values)


def _validated_observations(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
) -> tuple[tuple[Decimal, ...], tuple[int, ...]]:
    probability_values = _validated_probabilities(probabilities)
    outcome_values = _validated_outcomes(outcomes)
    if len(probability_values) != len(outcome_values):
        raise ValueError("probabilities and outcomes must have equal length")
    return probability_values, outcome_values


def brier_loss_values(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
) -> tuple[Decimal, ...]:
    """Return deterministic per-observation Brier losses."""

    probability_values, outcome_values = _validated_observations(
        probabilities, outcomes
    )
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return tuple(
            (probability - Decimal(outcome)) ** 2
            for probability, outcome in zip(
                probability_values, outcome_values, strict=True
            )
        )


def brier_score(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
) -> Decimal:
    losses = brier_loss_values(probabilities, outcomes)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return sum(losses, Decimal(0)) / Decimal(len(losses))


def log_loss_values(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    epsilon: Decimal = LOG_LOSS_EPSILON,
) -> tuple[Decimal, ...]:
    """Return binary log losses with an explicit deterministic clipping rule."""

    probability_values, outcome_values = _validated_observations(
        probabilities, outcomes
    )
    clipping = _finite_decimal(epsilon, "log-loss epsilon")
    if clipping <= 0 or clipping >= Decimal("0.5"):
        raise ValueError("log-loss epsilon must be between zero and one half")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        losses: list[Decimal] = []
        for probability, outcome in zip(
            probability_values, outcome_values, strict=True
        ):
            clipped = min(max(probability, clipping), Decimal(1) - clipping)
            assigned_probability = clipped if outcome else Decimal(1) - clipped
            losses.append(-assigned_probability.ln())
        return tuple(losses)


def log_loss(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    epsilon: Decimal = LOG_LOSS_EPSILON,
) -> Decimal:
    losses = log_loss_values(probabilities, outcomes, epsilon=epsilon)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return sum(losses, Decimal(0)) / Decimal(len(losses))


def reliability_bins(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    bin_count: int,
) -> tuple[ReliabilityBin, ...]:
    """Return canonical equal-width bins covering the complete unit interval."""

    probability_values, outcome_values = _validated_observations(
        probabilities, outcomes
    )
    if isinstance(bin_count, bool) or not isinstance(bin_count, int):
        raise ValueError("reliability bin count must be an integer")
    if bin_count < 2 or bin_count > 100:
        raise ValueError("reliability bin count must be between 2 and 100")

    grouped_probabilities: list[list[Decimal]] = [
        [] for _ in range(bin_count)
    ]
    grouped_outcomes: list[list[int]] = [[] for _ in range(bin_count)]
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for probability, outcome in zip(
            probability_values, outcome_values, strict=True
        ):
            index = min(int(probability * bin_count), bin_count - 1)
            grouped_probabilities[index].append(probability)
            grouped_outcomes[index].append(outcome)

    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        bins: list[ReliabilityBin] = []
        denominator = Decimal(bin_count)
        for index in range(bin_count):
            lower = Decimal(index) / denominator
            upper = (
                Decimal(1)
                if index == bin_count - 1
                else Decimal(index + 1) / denominator
            )
            count = len(grouped_probabilities[index])
            if count:
                count_decimal = Decimal(count)
                mean_prediction = (
                    sum(grouped_probabilities[index], Decimal(0))
                    / count_decimal
                )
                observed_frequency = (
                    Decimal(sum(grouped_outcomes[index])) / count_decimal
                )
            else:
                mean_prediction = None
                observed_frequency = None
            bins.append(
                ReliabilityBin(
                    index=index,
                    lower_bound=lower,
                    upper_bound=upper,
                    mean_prediction=mean_prediction,
                    observed_frequency=observed_frequency,
                    sample_count=count,
                )
            )
        return tuple(bins)


def expected_calibration_error(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    bin_count: int,
) -> Decimal:
    bins = reliability_bins(probabilities, outcomes, bin_count=bin_count)
    sample_count = sum(item.sample_count for item in bins)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return sum(
            (
                (
                    Decimal(item.sample_count)
                    / Decimal(sample_count)
                    * abs(item.mean_prediction - item.observed_frequency)
                )
                for item in bins
                if item.sample_count
                and item.mean_prediction is not None
                and item.observed_frequency is not None
            )
            ,
            Decimal(0),
        )


def probability_metrics(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    bin_count: int,
    log_loss_epsilon: Decimal = LOG_LOSS_EPSILON,
) -> ProbabilityMetrics:
    probability_values, outcome_values = _validated_observations(
        probabilities, outcomes
    )
    bins = reliability_bins(
        probability_values,
        outcome_values,
        bin_count=bin_count,
    )
    return ProbabilityMetrics(
        brier_score=brier_score(probability_values, outcome_values),
        log_loss=log_loss(
            probability_values,
            outcome_values,
            epsilon=log_loss_epsilon,
        ),
        expected_calibration_error=expected_calibration_error(
            probability_values,
            outcome_values,
            bin_count=bin_count,
        ),
        sample_count=len(probability_values),
        reliability_bins=bins,
    )


def constant_probability_baseline(
    probability: Decimal | int,
    *,
    observation_count: int,
) -> tuple[Decimal, ...]:
    """Freeze one declared probability for an evaluation partition."""

    value = _validated_probabilities((probability,))[0]
    if (
        isinstance(observation_count, bool)
        or not isinstance(observation_count, int)
        or observation_count < 1
    ):
        raise ValueError("baseline observation count must be a positive integer")
    return (value,) * observation_count


def constant_prevalence_baseline(
    development_outcomes: Sequence[int | bool],
    *,
    evaluation_observation_count: int,
) -> tuple[Decimal, ...]:
    """Fit prevalence on development labels only, then freeze it for evaluation."""

    outcomes = _validated_outcomes(development_outcomes)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        prevalence = Decimal(sum(outcomes)) / Decimal(len(outcomes))
    return constant_probability_baseline(
        prevalence,
        observation_count=evaluation_observation_count,
    )


def no_skill_baseline(*, observation_count: int) -> tuple[Decimal, ...]:
    return constant_probability_baseline(
        Decimal("0.5"),
        observation_count=observation_count,
    )


def frozen_legacy_score_baseline(
    probabilities: Sequence[Decimal | int],
    *,
    expected_observation_count: int,
) -> tuple[Decimal, ...]:
    """Validate an externally frozen legacy-score probability vector."""

    values = _validated_probabilities(probabilities)
    if (
        isinstance(expected_observation_count, bool)
        or not isinstance(expected_observation_count, int)
        or expected_observation_count < 1
    ):
        raise ValueError("legacy baseline count must be a positive integer")
    if len(values) != expected_observation_count:
        raise ValueError("legacy baseline count does not match its frozen vector")
    return values


def _quantile(sorted_values: Sequence[Decimal], probability: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        position = Decimal(len(sorted_values) - 1) * probability
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(sorted_values) - 1)
        fraction = position - Decimal(lower_index)
        return (
            sorted_values[lower_index] * (Decimal(1) - fraction)
            + sorted_values[upper_index] * fraction
        )


def moving_block_bootstrap_interval(
    values: Sequence[Decimal | int],
    *,
    block_length: int,
    resamples: int,
    confidence_level: Decimal,
    seed: int,
) -> BootstrapInterval:
    """Estimate a deterministic circular moving-block bootstrap interval.

    ``values`` can be per-row losses or paired loss differences. A fixed seed is
    mandatory so the evidence artifact can reproduce the exact interval.
    """

    samples = tuple(_finite_decimal(value, "bootstrap value") for value in values)
    if len(samples) < 2:
        raise ValueError("block bootstrap requires at least two observations")
    _validate_bootstrap_integer("block_length", block_length, minimum=2)
    _validate_bootstrap_integer("resamples", resamples, minimum=1_000)
    if block_length > len(samples):
        raise ValueError("bootstrap block length cannot exceed the sample count")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("bootstrap seed must be an integer")
    confidence = _finite_decimal(confidence_level, "confidence level")
    if confidence <= 0 or confidence >= 1:
        raise ValueError("confidence level must lie strictly between zero and one")

    generator = random.Random(seed)
    sample_count = len(samples)
    bootstrap_means: list[Decimal] = []
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        estimate = sum(samples, Decimal(0)) / Decimal(sample_count)
        for _ in range(resamples):
            resampled: list[Decimal] = []
            while len(resampled) < sample_count:
                start = generator.randrange(sample_count)
                resampled.extend(
                    samples[(start + offset) % sample_count]
                    for offset in range(block_length)
                )
            bootstrap_means.append(
                sum(resampled[:sample_count], Decimal(0))
                / Decimal(sample_count)
            )
        bootstrap_means.sort()
        tail = (Decimal(1) - confidence) / Decimal(2)
        lower = _quantile(bootstrap_means, tail)
        upper = _quantile(bootstrap_means, Decimal(1) - tail)
    return BootstrapInterval(
        estimate=estimate,
        confidence_lower=lower,
        confidence_upper=upper,
        confidence_level=confidence,
        resamples=resamples,
        block_length=block_length,
        seed=seed,
    )


def _validate_bootstrap_integer(name: str, value: int, *, minimum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise ValueError(f"{name} must be an integer of at least {minimum}")


def holm_bonferroni(
    p_values: Mapping[str, Decimal | int],
    *,
    alpha: Decimal,
) -> tuple[AdjustedPValue, ...]:
    """Return canonical Holm-Bonferroni adjusted p-values by test identifier."""

    if not p_values:
        raise ValueError("multiple-testing correction requires at least one test")
    significance = _finite_decimal(alpha, "multiple-testing alpha")
    if significance <= 0 or significance >= 1:
        raise ValueError("multiple-testing alpha must lie between zero and one")

    validated: list[tuple[str, Decimal]] = []
    for test_id, raw_value in p_values.items():
        if not isinstance(test_id, str) or not test_id.strip():
            raise ValueError("multiple-testing identifiers cannot be blank")
        value = _finite_decimal(raw_value, "p-value")
        if value < 0 or value > 1:
            raise ValueError("p-values must lie inside the unit interval")
        validated.append((test_id, value))

    ranked = sorted(validated, key=lambda item: (item[1], item[0]))
    adjusted_by_id: dict[str, Decimal] = {}
    running_maximum = Decimal(0)
    test_count = len(ranked)
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for rank, (test_id, raw_value) in enumerate(ranked):
            multiplier = Decimal(test_count - rank)
            adjusted = min(Decimal(1), raw_value * multiplier)
            running_maximum = max(running_maximum, adjusted)
            adjusted_by_id[test_id] = running_maximum

    return tuple(
        AdjustedPValue(
            test_id=test_id,
            raw_p_value=raw_value,
            adjusted_p_value=adjusted_by_id[test_id],
            rejected=adjusted_by_id[test_id] <= significance,
        )
        for test_id, raw_value in sorted(validated, key=lambda item: item[0])
    )
