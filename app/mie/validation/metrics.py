"""Deterministic probability metrics for offline MIE Gate 3 evidence.

All public calculations use finite :class:`~decimal.Decimal` inputs and return
immutable values. There are no model, market-data, runtime, or execution hooks.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from fractions import Fraction
from math import lcm

DECIMAL_PRECISION = 50
LOG_LOSS_EPSILON = Decimal("1e-15")
_MAX_EXACT_DECIMAL_DIGITS = 4_096
_ARITHMETIC_CONTEXT = Context(
    prec=DECIMAL_PRECISION,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


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
        raise ValueError(f"{name} must be a Decimal or integer")  # noqa: TRY004 - Public validation contract consistently rejects malformed values with ValueError.
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _bounded_decimal_tuple(
    value: Decimal, name: str
) -> tuple[int, tuple[int, ...], int]:
    if value.is_zero():
        return (0, (0,), 0)
    sign, digits, exponent = value.as_tuple()
    if (
        not isinstance(exponent, int)
        or len(digits) > _MAX_EXACT_DECIMAL_DIGITS
        or abs(exponent) > _MAX_EXACT_DECIMAL_DIGITS
    ):
        raise ValueError(f"{name} exceeds bounded exact Decimal arithmetic")
    return sign, digits, exponent


def _exact_decimal_sum_equals_integer(values: Sequence[Decimal], total: int) -> bool:
    common_denominator = 1
    for value in values:
        _, denominator = value.as_integer_ratio()
        common_denominator = lcm(common_denominator, denominator)
    scaled_sum = 0
    for value in values:
        numerator, denominator = value.as_integer_ratio()
        scaled_sum += numerator * (common_denominator // denominator)
    return scaled_sum == total * common_denominator


def _validated_probabilities(
    probabilities: Sequence[Decimal | int],
) -> tuple[Decimal, ...]:
    values = tuple(_finite_decimal(value, "probability") for value in probabilities)
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
    with localcontext(_ARITHMETIC_CONTEXT):
        losses: list[Decimal] = []
        for probability, outcome in zip(
            probability_values, outcome_values, strict=True
        ):
            actual = Decimal(outcome)
            loss = (probability - actual) ** 2
            if loss.is_zero() and probability != actual:
                raise ValueError("nonzero Brier loss underflowed to zero")
            losses.append(loss)
        return tuple(losses)


def brier_score(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
) -> Decimal:
    losses = brier_loss_values(probabilities, outcomes)
    with localcontext(_ARITHMETIC_CONTEXT):
        score = sum(losses, Decimal(0)) / Decimal(len(losses))
        if score.is_zero() and any(not loss.is_zero() for loss in losses):
            raise ValueError("nonzero aggregate Brier loss underflowed to zero")
        return score


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
    with localcontext(_ARITHMETIC_CONTEXT):
        upper_clipping = Decimal(1) - clipping
        if upper_clipping == Decimal(1):
            raise ValueError("log-loss epsilon is too small at the pinned precision")
        losses: list[Decimal] = []
        for probability, outcome in zip(
            probability_values, outcome_values, strict=True
        ):
            clipped = min(max(probability, clipping), upper_clipping)
            assigned_probability = clipped if outcome else Decimal(1) - clipped
            if assigned_probability <= 0 or not assigned_probability.is_finite():
                raise ValueError("log-loss epsilon cannot yield a finite loss")
            loss = -assigned_probability.ln()
            if not loss.is_finite():
                raise ValueError("log-loss epsilon cannot yield a finite loss")
            losses.append(loss)
        return tuple(losses)


def log_loss(
    probabilities: Sequence[Decimal | int],
    outcomes: Sequence[int | bool],
    *,
    epsilon: Decimal = LOG_LOSS_EPSILON,
) -> Decimal:
    losses = log_loss_values(probabilities, outcomes, epsilon=epsilon)
    with localcontext(_ARITHMETIC_CONTEXT):
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
        raise ValueError("reliability bin count must be an integer")  # noqa: TRY004 - Public validation contract consistently rejects malformed values with ValueError.
    if bin_count < 2 or bin_count > 100:
        raise ValueError("reliability bin count must be between 2 and 100")

    grouped_probabilities: list[list[Decimal]] = [[] for _ in range(bin_count)]
    grouped_outcomes: list[list[int]] = [[] for _ in range(bin_count)]
    with localcontext(_ARITHMETIC_CONTEXT):
        for probability, outcome in zip(
            probability_values, outcome_values, strict=True
        ):
            _bounded_decimal_tuple(probability, "probability")
            numerator, denominator = probability.as_integer_ratio()
            index = min((numerator * bin_count) // denominator, bin_count - 1)
            grouped_probabilities[index].append(probability)
            grouped_outcomes[index].append(outcome)

    with localcontext(_ARITHMETIC_CONTEXT):
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
                    sum(grouped_probabilities[index], Decimal(0)) / count_decimal
                )
                observed_frequency = (
                    Decimal(sum(grouped_outcomes[index])) / count_decimal
                )
                if mean_prediction == observed_frequency and not (
                    _exact_decimal_sum_equals_integer(
                        grouped_probabilities[index], sum(grouped_outcomes[index])
                    )
                ):
                    raise ValueError("nonzero calibration difference rounded to zero")
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
    with localcontext(_ARITHMETIC_CONTEXT):
        error = sum(
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
            ),
            Decimal(0),
        )
        if error.is_zero() and any(
            item.sample_count
            and item.mean_prediction is not None
            and item.observed_frequency is not None
            and item.mean_prediction != item.observed_frequency
            for item in bins
        ):
            raise ValueError("nonzero aggregate calibration error underflowed to zero")
        return error


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
    with localcontext(_ARITHMETIC_CONTEXT):
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


def _quantile_scaled(sorted_values: Sequence[int], probability: Fraction) -> Fraction:
    position = (len(sorted_values) - 1) * probability
    lower_index = position.numerator // position.denominator
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return (
        sorted_values[lower_index] * (1 - fraction)
        + sorted_values[upper_index] * fraction
    )


def _fraction_decimal(value: Fraction, *, rounding: str) -> Decimal:
    with localcontext(_ARITHMETIC_CONTEXT) as context:
        context.rounding = rounding
        result = Decimal(value.numerator) / Decimal(value.denominator)
        if not result.is_finite():
            raise ValueError("bootstrap result exceeds bounded Decimal arithmetic")
        return result


def _scaled_bootstrap_values(samples: Sequence[Decimal]) -> tuple[tuple[int, ...], int]:
    parts = tuple(_bounded_decimal_tuple(value, "bootstrap value") for value in samples)
    minimum_exponent = min(exponent for _, _, exponent in parts)
    if (
        max(exponent for _, _, exponent in parts) - minimum_exponent
        > _MAX_EXACT_DECIMAL_DIGITS
    ):
        raise ValueError("bootstrap values exceed bounded exact Decimal arithmetic")
    scaled: list[int] = []
    for sign, digits, exponent in parts:
        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit
        scaled.append(
            (-1 if sign else 1) * coefficient * 10 ** (exponent - minimum_exponent)
        )
    return tuple(scaled), minimum_exponent


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
        raise ValueError("bootstrap seed must be an integer")  # noqa: TRY004 - Public validation contract consistently rejects malformed values with ValueError.
    confidence = _finite_decimal(confidence_level, "confidence level")
    if confidence <= 0 or confidence >= 1:
        raise ValueError("confidence level must lie strictly between zero and one")

    _bounded_decimal_tuple(confidence, "bootstrap confidence level")
    scaled_samples, minimum_exponent = _scaled_bootstrap_values(samples)
    sample_count = len(scaled_samples)
    unit = (
        Fraction(10**minimum_exponent, sample_count)
        if minimum_exponent >= 0
        else Fraction(1, sample_count * 10 ** (-minimum_exponent))
    )
    generator = random.Random(seed)
    bootstrap_sums: list[int] = []
    for _ in range(resamples):
        resampled_count = 0
        resampled_sum = 0
        while resampled_count < sample_count:
            start = generator.randrange(sample_count)
            for offset in range(block_length):
                if resampled_count == sample_count:
                    break
                resampled_sum += scaled_samples[(start + offset) % sample_count]
                resampled_count += 1
        bootstrap_sums.append(resampled_sum)
    bootstrap_sums.sort()
    tail = (1 - Fraction(confidence)) / 2
    estimate = _fraction_decimal(sum(scaled_samples) * unit, rounding=ROUND_HALF_EVEN)
    lower = _fraction_decimal(
        _quantile_scaled(bootstrap_sums, tail) * unit, rounding=ROUND_FLOOR
    )
    upper = _fraction_decimal(
        _quantile_scaled(bootstrap_sums, 1 - tail) * unit, rounding=ROUND_CEILING
    )
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
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
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
    with localcontext(_ARITHMETIC_CONTEXT):
        for rank, (test_id, raw_value) in enumerate(ranked):
            multiplier = Decimal(test_count - rank)
            _, digits, _ = _bounded_decimal_tuple(raw_value, "p-value")
            with localcontext(_ARITHMETIC_CONTEXT) as exact_context:
                exact_context.prec = max(
                    DECIMAL_PRECISION,
                    len(digits) + len(str(test_count - rank)),
                )
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
