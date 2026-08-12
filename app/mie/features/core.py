from __future__ import annotations

from decimal import Decimal

from app.mie.features.dynamics import causal_dynamics
from app.mie.features.geometry import confirmed_geometry_features
from app.mie.features.models import FeatureWindow, MathematicalFeatureSnapshot
from app.mie.features.momentum import momentum_features
from app.mie.features.signal import causal_signal_features
from app.mie.features.statistics import statistical_features

D = Decimal


def mathematical_feature_snapshot(
    window: FeatureWindow,
    *,
    signal_alpha: Decimal = D("0.25"),
    dynamics_window: int = 21,
    momentum_fast_bars: int = 5,
    momentum_slow_bars: int = 20,
    pivot_left_bars: int = 2,
    pivot_right_bars: int = 2,
) -> MathematicalFeatureSnapshot | None:
    """Build all Gate 2 families atomically or fail closed."""

    closes = [bar.close for bar in window.bars]
    volumes = [bar.volume for bar in window.bars]
    statistics = statistical_features(closes)
    signal = causal_signal_features(closes, alpha=signal_alpha)
    dynamics = causal_dynamics(closes, window=dynamics_window)
    momentum = momentum_features(
        closes,
        volumes,
        fast_bars=momentum_fast_bars,
        slow_bars=momentum_slow_bars,
    )
    geometry = confirmed_geometry_features(
        window.bars,
        left_bars=pivot_left_bars,
        right_bars=pivot_right_bars,
    )
    if any(
        item is None
        for item in (statistics, signal, dynamics, momentum, geometry)
    ):
        return None

    assert statistics is not None
    assert signal is not None
    assert dynamics is not None
    assert momentum is not None
    assert geometry is not None
    return MathematicalFeatureSnapshot(
        instrument_id=window.instrument_id,
        horizon=window.horizon,
        as_of=window.as_of,
        data_cutoff=window.data_cutoff,
        feature_version=window.feature_version,
        source_provenance_sha256=window.provenance_sha256,
        statistics=statistics,
        signal=signal,
        dynamics=dynamics,
        momentum=momentum,
        geometry=geometry,
    )
