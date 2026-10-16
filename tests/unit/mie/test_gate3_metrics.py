from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from app.mie.validation.metrics import (
    ReliabilityBin,
    brier_loss_values,
    brier_score,
    constant_prevalence_baseline,
    expected_calibration_error,
    frozen_legacy_score_baseline,
    holm_bonferroni,
    log_loss,
    log_loss_values,
    moving_block_bootstrap_interval,
    no_skill_baseline,
    probability_metrics,
    reliability_bins,
)

D = Decimal


def test_brier_and_log_loss_match_frozen_binary_example() -> None:
    probabilities = (D("0.25"), D("0.75"))
    outcomes = (0, 1)

    assert brier_loss_values(probabilities, outcomes) == (
        D("0.0625"),
        D("0.0625"),
    )
    assert brier_score(probabilities, outcomes) == D("0.0625")
    with localcontext() as context:
        context.prec = 50
        expected_log_loss = -D("0.75").ln()
    assert log_loss(probabilities, outcomes) == expected_log_loss


def test_brier_rejects_nonzero_loss_that_underflows_to_zero() -> None:
    tiny_probability = D("1e-600000")
    with pytest.raises(ValueError, match="underflow"):
        brier_loss_values((tiny_probability,), (0,))


def test_brier_rejects_nonzero_aggregate_that_underflows_to_zero() -> None:
    tiny_probability = D("1e-500024")
    assert brier_loss_values((tiny_probability, D(0)), (0, 0)) == (
        D("1e-1000048"),
        D(0),
    )
    with pytest.raises(ValueError, match="aggregate.*underflow"):
        brier_score((tiny_probability, D(0)), (0, 0))


def test_log_loss_has_a_finite_declared_clipping_rule() -> None:
    losses = log_loss_values((D(0), D(1)), (1, 0))

    assert all(value.is_finite() and value > 0 for value in losses)
    with pytest.raises(ValueError, match="epsilon"):
        log_loss((D("0.5"),), (1,), epsilon=D(0))


def test_reliability_bins_cover_unit_interval_and_keep_empty_bins() -> None:
    bins = reliability_bins(
        (D(0), D("0.2"), D("0.5"), D(1)),
        (0, 1, 1, 1),
        bin_count=4,
    )

    assert tuple(item.index for item in bins) == (0, 1, 2, 3)
    assert bins[0].lower_bound == 0
    assert bins[-1].upper_bound == 1
    assert bins[0].sample_count == 2
    assert bins[0].mean_prediction == D("0.1")
    assert bins[0].observed_frequency == D("0.5")
    assert bins[1].sample_count == 0
    assert bins[1].mean_prediction is None
    assert bins[1].observed_frequency is None
    assert bins[2].sample_count == 1
    assert bins[3].sample_count == 1
    assert expected_calibration_error(
        (D(0), D("0.2"), D("0.5"), D(1)),
        (0, 1, 1, 1),
        bin_count=4,
    ) == D("0.325")


def test_reliability_bin_edges_are_deterministic() -> None:
    bins = reliability_bins(
        (D("0.249999"), D("0.25"), D("1")),
        (0, 1, 1),
        bin_count=4,
    )

    assert bins[0].sample_count == 1
    assert bins[1].sample_count == 1
    assert bins[3].sample_count == 1


def test_reliability_bin_never_rounds_a_probability_across_its_edge() -> None:
    probability = D("0.0" + "9" * 60)
    assert probability < D("0.1")

    bins = reliability_bins((probability,), (0,), bin_count=10)

    assert bins[0].sample_count == 1
    assert bins[1].sample_count == 0


def test_reliability_bin_rejects_hidden_exact_calibration_difference() -> None:
    probability = D("0.5" + "0" * 69 + "1")
    assert probability != D("0.5")

    with pytest.raises(ValueError, match="calibration difference rounded to zero"):
        reliability_bins((probability, D("0.5")), (1, 0), bin_count=2)


def test_reliability_bin_accepts_exact_match_with_different_ratio_denominators() -> (
    None
):
    probabilities = (D("0.125"),) * 8 + (D("0.1"),) * 10
    outcomes = (1, 1) + (0,) * 16

    bins = reliability_bins(probabilities, outcomes, bin_count=2)

    assert bins[0].mean_prediction == bins[0].observed_frequency
    assert bins[0].sample_count == 18


def test_ece_rejects_weighted_aggregate_underflow_from_represented_bins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bins = (
        ReliabilityBin(0, D(0), D("0.5"), D("1e-1000048"), D(0), 1),
        ReliabilityBin(1, D("0.5"), D(1), D(0), D(0), 1),
    )
    monkeypatch.setattr(
        "app.mie.validation.metrics.reliability_bins", lambda *_, **__: bins
    )

    with pytest.raises(ValueError, match="aggregate calibration error underflow"):
        expected_calibration_error((D(0), D(1)), (0, 1), bin_count=2)


def test_log_loss_rejects_epsilon_whose_complement_rounds_to_one() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        log_loss_values((D(1),), (0,), epsilon=D("1e-60"))


def test_probability_metric_bundle_is_complete_and_deterministic() -> None:
    inputs = ((D("0.1"), D("0.8"), D("0.7")), (0, 1, 1))

    first = probability_metrics(*inputs, bin_count=5)
    second = probability_metrics(*inputs, bin_count=5)

    assert first == second
    assert first.sample_count == 3
    assert first.brier_score == brier_score(*inputs)
    assert first.log_loss == log_loss(*inputs)
    assert len(first.reliability_bins) == 5


def test_gate3_metrics_ignore_caller_decimal_context() -> None:
    probabilities = (D("0.123456789"), D("0.876543211"), D("0.333333333"))
    outcomes = (0, 1, 0)
    values = (D("0.123456789"), D("0.876543211"), D("0.333333333"))
    bootstrap_plan = {
        "block_length": 2,
        "resamples": 1_000,
        "confidence_level": D("0.95"),
        "seed": 42,
    }
    expected = (
        probability_metrics(probabilities, outcomes, bin_count=7),
        constant_prevalence_baseline(outcomes, evaluation_observation_count=3),
        moving_block_bootstrap_interval(values, **bootstrap_plan),
        holm_bonferroni(
            {"a": D("0.0123456789"), "b": D("0.123456789")}, alpha=D("0.05")
        ),
    )
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.Emin = -10
        context.Emax = 10
        context.traps[Inexact] = True
        actual = (
            probability_metrics(probabilities, outcomes, bin_count=7),
            constant_prevalence_baseline(outcomes, evaluation_observation_count=3),
            moving_block_bootstrap_interval(values, **bootstrap_plan),
            holm_bonferroni(
                {"a": D("0.0123456789"), "b": D("0.123456789")}, alpha=D("0.05")
            ),
        )
    assert actual == expected


def test_prevalence_and_no_skill_baselines_are_frozen_out_of_sample() -> None:
    prevalence = constant_prevalence_baseline(
        (1, 0, 1, 1),
        evaluation_observation_count=3,
    )

    assert prevalence == (D("0.75"),) * 3
    assert no_skill_baseline(observation_count=3) == (D("0.5"),) * 3
    legacy = (D("0.1"), D("0.7"), D("0.6"))
    assert (
        frozen_legacy_score_baseline(
            legacy,
            expected_observation_count=3,
        )
        == legacy
    )

    with pytest.raises(ValueError, match="does not match"):
        frozen_legacy_score_baseline(
            legacy,
            expected_observation_count=2,
        )


def test_probability_metrics_fail_closed_on_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="equal length"):
        brier_score((D("0.5"),), (0, 1))
    with pytest.raises(ValueError, match="unit interval"):
        brier_score((D("1.1"),), (1,))
    with pytest.raises(ValueError, match="Decimal or integer"):
        brier_score((0.5,), (1,))
    with pytest.raises(ValueError, match="zero or one"):
        brier_score((D("0.5"),), (2,))
    with pytest.raises(ValueError, match="between 2 and 100"):
        reliability_bins((D("0.5"),), (1,), bin_count=1)
    with pytest.raises(ValueError, match="cannot be empty"):
        constant_prevalence_baseline((), evaluation_observation_count=1)


def test_moving_block_bootstrap_is_seeded_and_reproducible() -> None:
    values = tuple(D(index) / D(10) for index in range(12))
    parameters = {
        "block_length": 3,
        "resamples": 1_000,
        "confidence_level": D("0.95"),
        "seed": 20260901,
    }

    first = moving_block_bootstrap_interval(values, **parameters)
    second = moving_block_bootstrap_interval(values, **parameters)

    assert first == second
    assert first.confidence_lower <= first.estimate <= first.confidence_upper
    assert first.resamples == 1_000
    assert first.block_length == 3
    assert first.seed == 20260901


def test_bootstrap_lower_bound_never_rounds_up_past_exact_values() -> None:
    value = D("0.0" + "4" + "9" * 58)
    threshold = D("0.05")
    assert value < threshold

    interval = moving_block_bootstrap_interval(
        (value, value),
        block_length=2,
        resamples=1_000,
        confidence_level=D("0.95"),
        seed=7,
    )

    assert interval.confidence_lower <= value
    assert interval.confidence_lower < threshold
    assert interval.confidence_upper >= value


def test_block_bootstrap_fails_closed_on_invalid_plan_or_values() -> None:
    values = (D("0.1"), D("0.2"), D("0.3"))
    common = {
        "block_length": 2,
        "resamples": 1_000,
        "confidence_level": D("0.95"),
        "seed": 7,
    }
    with pytest.raises(ValueError, match="at least 1000"):
        moving_block_bootstrap_interval(values, **{**common, "resamples": 999})
    with pytest.raises(ValueError, match="cannot exceed"):
        moving_block_bootstrap_interval(values, **{**common, "block_length": 4})
    with pytest.raises(ValueError, match="finite"):
        moving_block_bootstrap_interval(
            (D("NaN"), D(1)),
            **common,
        )


def test_holm_bonferroni_adjusts_monotonically_and_returns_canonical_order() -> None:
    adjusted = holm_bonferroni(
        {"trial-c": D("0.03"), "trial-a": D("0.01"), "trial-b": D("0.04")},
        alpha=D("0.05"),
    )

    assert tuple(item.test_id for item in adjusted) == (
        "trial-a",
        "trial-b",
        "trial-c",
    )
    assert adjusted[0].adjusted_p_value == D("0.03")
    assert adjusted[1].adjusted_p_value == D("0.06")
    assert adjusted[2].adjusted_p_value == D("0.06")
    assert tuple(item.rejected for item in adjusted) == (True, False, False)


def test_holm_never_rounds_a_non_significant_value_into_rejection() -> None:
    raw_p_value = D("0.025" + "0" * 56 + "1")
    alpha = D("0.05")
    assert raw_p_value > D("0.025")

    result = holm_bonferroni({"first": raw_p_value, "second": D(1)}, alpha=alpha)

    assert result[0].adjusted_p_value > alpha
    assert result[0].rejected is False


def test_holm_bonferroni_rejects_invalid_registry() -> None:
    with pytest.raises(ValueError, match="at least one test"):
        holm_bonferroni({}, alpha=D("0.05"))
    with pytest.raises(ValueError, match="unit interval"):
        holm_bonferroni({"trial-a": D("1.1")}, alpha=D("0.05"))
    with pytest.raises(ValueError, match="identifiers"):
        holm_bonferroni({" ": D("0.1")}, alpha=D("0.05"))
