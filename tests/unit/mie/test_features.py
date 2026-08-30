from __future__ import annotations

import random
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Sequence

import pytest
from pydantic import ValidationError

from app.indicators.causal_trend import causal_log_trend_from_logs
from app.mie.contracts import ForecastHorizon
from app.mie.features import (
    FeatureBar,
    FeatureWindow,
    MathematicalFeatureSnapshot,
    causal_dynamics,
    causal_dynamics_from_logs,
    causal_signal_features,
    confirmed_geometry_features,
    mathematical_feature_snapshot,
    momentum_features,
    statistical_features,
)

D = Decimal
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
HORIZON = ForecastHorizon(label="15m", seconds=900)


def feature_bars(
    closes: Sequence[Decimal],
    *,
    highs: Sequence[Decimal] | None = None,
    lows: Sequence[Decimal] | None = None,
    volumes: Sequence[Decimal] | None = None,
) -> tuple[FeatureBar, ...]:
    result: list[FeatureBar] = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        high = (
            highs[index]
            if highs is not None
            else max(open_price, close) + D("0.1")
        )
        low = (
            lows[index]
            if lows is not None
            else min(open_price, close) - D("0.1")
        )
        result.append(
            FeatureBar(
                closed_at=START + timedelta(minutes=15 * (index + 1)),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=(
                    volumes[index]
                    if volumes is not None
                    else D("100") + D(index)
                ),
            )
        )
    return tuple(result)


def feature_window(closes: Sequence[Decimal]) -> FeatureWindow:
    bars = feature_bars(closes)
    return FeatureWindow(
        instrument_id="BTC-USDT-SWAP",
        horizon=HORIZON,
        as_of=bars[-1].closed_at,
        bars=bars,
    )


def exponential_prices(count: int, step: Decimal) -> list[Decimal]:
    with localcontext() as context:
        context.prec = 50
        return [D("100") * (step * D(index)).exp() for index in range(count)]


def decimal_values(value: object):
    if isinstance(value, Decimal):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from decimal_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from decimal_values(item)


def test_feature_window_requires_closed_confirmed_chronological_bars() -> None:
    bars = list(feature_bars([D("100") + D(index) for index in range(5)]))
    window = FeatureWindow(
        instrument_id="BTC-USDT-SWAP",
        horizon=HORIZON,
        as_of=bars[-1].closed_at,
        bars=tuple(bars),
    )

    assert window.data_cutoff == bars[-1].closed_at
    assert len(window.provenance_sha256) == 64

    with pytest.raises(ValidationError):
        FeatureBar(
            closed_at=START,
            open=D("100"),
            high=D("101"),
            low=D("99"),
            close=D("100"),
            volume=D("1"),
            confirmed=False,
        )

    with pytest.raises(ValidationError):
        FeatureWindow(
            instrument_id="BTC-USDT-SWAP",
            horizon=HORIZON,
            as_of=bars[-1].closed_at,
            bars=tuple([*bars[:-2], bars[-1], bars[-2]]),
        )

    corrupted = bars[0].model_copy(update={"confirmed": False})
    with pytest.raises(ValidationError):
        FeatureWindow(
            instrument_id="BTC-USDT-SWAP",
            horizon=HORIZON,
            as_of=bars[-1].closed_at,
            bars=tuple([corrupted, *bars[1:]]),
        )


def test_feature_window_rejects_future_and_non_utc_data() -> None:
    bars = feature_bars([D("100") + D(index) for index in range(5)])

    with pytest.raises(ValidationError):
        FeatureWindow(
            instrument_id="BTC-USDT-SWAP",
            horizon=HORIZON,
            as_of=bars[-1].closed_at - timedelta(seconds=1),
            bars=bars,
        )

    with pytest.raises(ValidationError):
        FeatureBar(
            closed_at=datetime(2026, 1, 1),
            open=D("100"),
            high=D("101"),
            low=D("99"),
            close=D("100"),
            volume=D("1"),
        )


def test_feature_window_requires_exact_declared_horizon_spacing() -> None:
    bars = list(feature_bars([D("100") + D(index) for index in range(5)]))
    bars[3] = bars[3].model_copy(
        update={"closed_at": bars[3].closed_at + timedelta(seconds=1)}
    )

    with pytest.raises(ValidationError):
        FeatureWindow(
            instrument_id="BTC-USDT-SWAP",
            horizon=HORIZON,
            as_of=bars[-1].closed_at,
            bars=tuple(bars),
        )


def test_feature_bar_rejects_invalid_ohlc_geometry() -> None:
    with pytest.raises(ValidationError):
        FeatureBar(
            closed_at=START,
            open=D("100"),
            high=D("99"),
            low=D("98"),
            close=D("100"),
            volume=D("1"),
        )


def test_public_feature_engines_fail_closed_on_invalid_inputs() -> None:
    assert statistical_features([D("100"), D("0"), D("101")]) is None
    assert causal_signal_features([D("100")] * 6, alpha=D("0")) is None
    assert causal_dynamics([D("100")] * 20, window=21) is None
    assert momentum_features([D("100")] * 20) is None
    assert momentum_features(
        [D("100")] * 21,
        [D("1")] * 20,
    ) is None
    assert momentum_features(
        [D("100")] * 21,
        [D("1")] * 20 + [D("-1")],
    ) is None
    assert confirmed_geometry_features(
        feature_bars([D("100")] * 5), left_bars=0
    ) is None
    bars = list(feature_bars([D("100")] * 5))
    assert confirmed_geometry_features(
        tuple([*bars[:-2], bars[-1], bars[-2]])
    ) is None


def test_statistics_are_exact_for_constant_prices() -> None:
    result = statistical_features([D("100")] * 10)

    assert result is not None
    assert result.sample_size == 9
    assert result.mean_log_return == 0
    assert result.return_std == 0
    assert result.median_log_return == 0
    assert result.mad_scale == 0
    assert result.downside_deviation == 0
    assert result.outlier_fraction == 0


def test_causal_signal_separates_smooth_trend_from_noisy_returns() -> None:
    smooth = causal_signal_features(exponential_prices(40, D("0.002")))
    noisy_prices = [
        D("100")
        * (D("0.002") * D(index)).exp()
        * (D("1.01") if index % 2 else D("0.99"))
        for index in range(40)
    ]
    noisy = causal_signal_features(noisy_prices)

    assert smooth is not None and noisy is not None
    assert smooth.direction == "rising"
    assert noisy.residual_rms > smooth.residual_rms
    assert noisy.noise_ratio > smooth.noise_ratio


def test_dynamics_exactly_characterizes_frozen_legacy_estimator() -> None:
    logs = [
        D("4.5") + D("0.004") * D(index) + D("0.00003") * D(index) ** 2
        for index in range(34)
    ]
    legacy = causal_log_trend_from_logs(logs, window=21)
    extracted = causal_dynamics_from_logs(logs, window=21)

    assert legacy is not None and extracted is not None
    assert extracted.model_dump() == asdict(legacy)


def test_dynamics_matches_legacy_on_seeded_random_log_paths() -> None:
    generator = random.Random(20260813)
    for _ in range(50):
        logs = [D("4.5")]
        for _ in range(39):
            step = D(generator.randint(-5000, 5000)) / D("1000000")
            logs.append(logs[-1] + step)

        legacy = causal_log_trend_from_logs(logs, window=21)
        extracted = causal_dynamics_from_logs(logs, window=21)

        assert legacy is not None and extracted is not None
        assert extracted.model_dump() == asdict(legacy)


def test_momentum_is_directional_but_never_a_probability() -> None:
    prices = exponential_prices(40, D("0.003"))
    volumes = [D("100")] * 39 + [D("150")]
    result = momentum_features(prices, volumes)

    assert result is not None
    assert result.direction == "rising"
    assert result.directional_persistence == 1
    assert result.volume_ratio == D("1.5")
    assert D("0") < result.strength <= D("1")
    assert "probability" not in type(result).model_fields


def test_geometry_waits_for_right_side_confirmation() -> None:
    closes = [D("9"), D("10"), D("12"), D("10"), D("9")]
    highs = [D("10"), D("11"), D("15"), D("12"), D("11")]
    lows = [D("8"), D("9"), D("10"), D("9"), D("8")]
    bars = feature_bars(closes, highs=highs, lows=lows)

    assert confirmed_geometry_features(bars[:-1]) is None
    confirmed = confirmed_geometry_features(bars)

    assert confirmed is not None
    assert confirmed.confirmed_pivot_count == 1
    assert confirmed.last_swing_high is not None
    assert confirmed.last_swing_high.index == 2
    assert confirmed.last_swing_high.price == D("15")
    assert confirmed.last_swing_high.confirmed_at == bars[4].closed_at


def test_geometry_rejects_ambiguous_outside_bar_pivot() -> None:
    closes = [D("10"), D("10"), D("10"), D("10"), D("10")]
    highs = [D("11"), D("12"), D("20"), D("12"), D("11")]
    lows = [D("9"), D("8"), D("1"), D("8"), D("9")]
    bars = feature_bars(closes, highs=highs, lows=lows)

    result = confirmed_geometry_features(bars)

    assert result is not None
    assert result.confirmed_pivot_count == 0
    assert result.last_swing_high is None
    assert result.last_swing_low is None


def test_atomic_feature_snapshot_is_replayable_and_shadow_only() -> None:
    window = feature_window(exponential_prices(64, D("0.001")))
    first = mathematical_feature_snapshot(window)
    second = mathematical_feature_snapshot(window)

    assert first is not None and second is not None
    assert first == second
    assert first.replay_sha256 == second.replay_sha256
    assert first.source_provenance_sha256 == window.provenance_sha256
    assert first.authority == "shadow_only"
    assert first.execution_authority is False
    assert first.data_cutoff == window.bars[-1].closed_at

    payload = first.model_dump()
    payload["execution_authority"] = True
    with pytest.raises(ValidationError):
        MathematicalFeatureSnapshot.model_validate(payload)


def test_randomized_feature_paths_remain_finite_bounded_and_deterministic() -> None:
    generator = random.Random(20260813)
    for _ in range(100):
        prices = [D("100")]
        for _ in range(79):
            basis_points = D(generator.randint(-80, 80)) / D("10000")
            prices.append(prices[-1] * (D("1") + basis_points))

        window = feature_window(prices)
        first = mathematical_feature_snapshot(window)
        second = mathematical_feature_snapshot(window)

        assert first is not None and second is not None
        assert first.replay_sha256 == second.replay_sha256
        assert all(
            value.is_finite()
            for value in decimal_values(first.model_dump())
        )
        assert D("0") <= first.statistics.outlier_fraction <= D("1")
        assert D("0") <= first.signal.noise_ratio <= D("1")
        assert D("0") <= first.signal.strength <= D("1")
        assert D("0") <= first.dynamics.confidence <= D("1")
        assert D("0") <= first.momentum.strength <= D("1")


def test_source_provenance_changes_when_a_historical_input_changes() -> None:
    prices = [D("100") + D(index) / D("10") for index in range(64)]
    first = feature_window(prices)
    changed_prices = list(prices)
    changed_prices[10] += D("0.01")
    second = feature_window(changed_prices)

    assert first.provenance_sha256 != second.provenance_sha256


def test_feature_core_fails_closed_when_required_history_is_missing() -> None:
    window = feature_window([D("100") + D(index) for index in range(20)])

    assert mathematical_feature_snapshot(window) is None
