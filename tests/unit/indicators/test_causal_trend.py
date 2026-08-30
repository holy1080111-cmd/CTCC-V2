from decimal import Decimal, localcontext

from app.indicators import causal_log_trend

D = Decimal


def test_exact_exponential_trend_recovers_log_velocity() -> None:
    closes = [D("100") * D("1.01") ** index for index in range(21)]

    result = causal_log_trend(closes)

    assert result is not None
    assert abs(result.log_velocity_per_bar - D("1.01").ln()) < D("1e-24")
    assert abs(result.log_acceleration_per_bar2) < D("1e-24")
    assert result.fit_r2 == D("1")
    assert result.confidence > D("0.99")
    assert result.direction == "rising"


def test_quadratic_log_price_recovers_endpoint_acceleration() -> None:
    velocity = D("0.002")
    acceleration = D("0.0002")
    with localcontext() as context:
        context.prec = 50
        closes = [
            (
                D("4")
                + velocity * D(index)
                + acceleration * D(index * index) / D("2")
            ).exp()
            for index in range(21)
        ]

    result = causal_log_trend(closes)

    assert result is not None
    assert abs(
        result.log_velocity_per_bar - (velocity + acceleration * D("20"))
    ) < D("1e-24")
    assert abs(result.log_acceleration_per_bar2 - acceleration) < D("1e-24")
    assert result.fit_r2 == D("1")


def test_noise_dominated_series_has_low_confidence() -> None:
    closes = [D("100")]
    for index in range(1, 21):
        multiplier = D("1.03") if index % 2 else D("0.97")
        closes.append(closes[-1] * multiplier)

    result = causal_log_trend(closes)

    assert result is not None
    assert result.fit_r2 < D("0.10")
    assert result.confidence < D("0.05")
    assert result.direction == "flat"


def test_estimate_uses_only_latest_causal_window() -> None:
    tail = [D("100") * D("1.005") ** index for index in range(21)]
    first = causal_log_trend([D("1"), D("50000"), *tail])
    second = causal_log_trend([D("999"), D("2"), *tail])

    assert first == second


def test_insufficient_or_non_positive_input_is_rejected() -> None:
    assert causal_log_trend([D("100")] * 20) is None
    assert causal_log_trend([D("100")] * 20 + [D("0")]) is None
    assert causal_log_trend([D("100")] * 20 + [D("NaN")]) is None
