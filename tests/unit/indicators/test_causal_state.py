from decimal import Decimal

from app.indicators import causal_state_estimate

D = Decimal


def test_exact_exponential_state_recovers_velocity_with_high_confidence() -> None:
    closes = [D("100") * D("1.001") ** index for index in range(80)]

    result = causal_state_estimate(closes)

    assert result is not None
    assert abs(result.log_velocity_per_bar - D("1.001").ln()) < D("1e-20")
    assert result.direction == "rising"
    assert result.velocity_z == D("20")
    assert result.confidence > D("0.99")
    assert result.shock_score == D("0")


def test_alternating_noise_does_not_create_false_state_direction() -> None:
    closes = [D("100")]
    for index in range(1, 80):
        closes.append(closes[-1] * (D("1.02") if index % 2 else D("0.98")))

    result = causal_state_estimate(closes)

    assert result is not None
    assert result.direction == "flat"
    assert result.confidence < D("0.10")


def test_endpoint_price_shock_is_robustly_flagged() -> None:
    closes = [D("100") * D("1.001") ** index for index in range(80)]
    closes[-1] = closes[-2] * D("0.80")

    result = causal_state_estimate(closes)

    assert result is not None
    assert result.outlier_count >= 1
    assert result.shock_score > D("0.80")
    assert result.confidence < D("0.10")
    assert result.direction == "flat"


def test_state_estimate_uses_only_latest_causal_window() -> None:
    tail = [D("100") * D("1.002") ** index for index in range(34)]

    first = causal_state_estimate([D("1"), D("50000"), *tail])
    second = causal_state_estimate([D("999"), D("2"), *tail])

    assert first == second


def test_state_estimate_rejects_invalid_input() -> None:
    assert causal_state_estimate([D("100")] * 33) is None
    assert causal_state_estimate([D("100")] * 33 + [D("0")]) is None
    assert causal_state_estimate([D("100")] * 33 + [D("NaN")]) is None
