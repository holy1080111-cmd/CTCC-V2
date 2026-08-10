from decimal import Decimal, localcontext

from app.indicators import causal_return_interval

D = Decimal


def test_exact_exponential_series_has_rising_causal_interval() -> None:
    closes = [D("100") * D("1.001") ** index for index in range(100)]

    result = causal_return_interval(closes)

    assert result is not None
    assert abs(result.predicted_log_return - D("1.001").ln()) < D("1e-20")
    assert result.direction == "rising"
    assert result.lower_log_return > 0
    assert result.calibration_size == 60
    assert result.coverage_sample_size == 40
    assert result.empirical_coverage >= D("0.90")


def test_noisy_series_produces_interval_that_crosses_zero() -> None:
    closes = [D("100")]
    for index in range(1, 100):
        closes.append(closes[-1] * (D("1.02") if index % 2 else D("0.98")))

    result = causal_return_interval(closes)

    assert result is not None
    assert result.direction == "uncertain"
    assert result.lower_log_return < 0 < result.upper_log_return


def test_conformal_interval_is_causal_at_each_calibration_point() -> None:
    stable = [D("100") * D("1.001") ** index for index in range(100)]
    changed_past = stable.copy()
    changed_past[0] = D("999999")

    first = causal_return_interval(stable)
    second = causal_return_interval(changed_past)

    assert first == second


def test_conformal_interval_rejects_insufficient_or_invalid_input() -> None:
    assert causal_return_interval([D("100")] * 80) is None
    assert causal_return_interval([D("100")] * 80 + [D("0")]) is None
    assert causal_return_interval([D("100")] * 80 + [D("NaN")]) is None


def test_endpoint_prediction_matches_shared_causal_derivative() -> None:
    from app.indicators import causal_log_trend

    closes = [
        D("100") * (D("1") + D((index * 17) % 11 - 5) / D("1000"))
        for index in range(100)
    ]

    interval = causal_return_interval(closes)
    trend = causal_log_trend(closes)

    assert interval is not None
    assert trend is not None
    with localcontext() as context:
        context.prec = 50
        expected = (
            trend.log_velocity_per_bar
            + trend.log_acceleration_per_bar2 / D("2")
        )
    assert abs(interval.predicted_log_return - expected) < D("1e-40")
