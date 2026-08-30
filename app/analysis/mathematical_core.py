from __future__ import annotations

from decimal import Decimal
from typing import Callable

from app.domain.analysis import (
    CausalReturnIntervalSnapshot,
    MathematicalCoreComponent,
    MathematicalCoreSnapshot,
    TimeframeAnalysis,
)

D = Decimal
_EPSILON = D("1e-30")

TIMEFRAME_WEIGHTS: tuple[tuple[str, Decimal], ...] = (
    ("4H", D("0.35")),
    ("1H", D("0.30")),
    ("15m", D("0.25")),
    ("5m", D("0.10")),
)
# Only analytically or prequentially checked families enter the execution
# contract. Equal priors avoid presenting uncalibrated family preference as an
# optimum; each component's calculated reliability supplies the adaptive
# down-weighting. Qualitative structure and momentum stay auxiliary.
EXECUTION_METHOD_WEIGHTS: dict[str, Decimal] = {
    "derivative": D("1") / D("3"),
    "state": D("1") / D("3"),
    "conformal": D("1") / D("3"),
}
AUXILIARY_METHOD_WEIGHTS: dict[str, Decimal] = {
    "structure": D("1") / D("2"),
    "momentum": D("1") / D("2"),
}
MIN_PREQUENTIAL_COVERAGE_SAMPLES = 30
_WILSON_Z = D("1.959963984540054")

LocalSignal = Callable[[TimeframeAnalysis], tuple[Decimal, Decimal] | None]


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return min(upper, max(lower, value))


def _volatility_reliability(view: TimeframeAnalysis) -> Decimal:
    return {
        "low": D("0.85"),
        "normal": D("1"),
        "high": D("0.70"),
        "extreme": D("0.20"),
    }[view.volatility]


def _quality(view: TimeframeAnalysis) -> Decimal:
    return D("1") if view.data_quality_ok else D("0")


def conformal_coverage_is_valid(
    interval: CausalReturnIntervalSnapshot,
) -> bool:
    """Check whether nominal coverage lies in a 95% Wilson interval.

    This is a causal coverage diagnostic, not a profitability test. The
    minimum sample requirement prevents a tiny prequential window from being
    promoted into the execution contract.
    """

    if interval.coverage_sample_size < MIN_PREQUENTIAL_COVERAGE_SAMPLES:
        return False
    sample_size = D(interval.coverage_sample_size)
    observed = interval.empirical_coverage
    z_squared = _WILSON_Z * _WILSON_Z
    denominator = D("1") + z_squared / sample_size
    center = (
        observed + z_squared / (D("2") * sample_size)
    ) / denominator
    variance = (
        observed * (D("1") - observed) / sample_size
        + z_squared / (D("4") * sample_size * sample_size)
    )
    half_width = _WILSON_Z * variance.sqrt() / denominator
    return (
        center - half_width
        <= interval.confidence_level
        <= center + half_width
    )


def _component_validation(
    code: str,
    views: dict[str, TimeframeAnalysis],
) -> tuple[str, int, Decimal | None]:
    if code in {"derivative", "state"}:
        return "analytical", 0, None
    if code != "conformal":
        return "auxiliary", 0, None

    intervals = [
        view.indicators.return_interval
        for timeframe, _ in TIMEFRAME_WEIGHTS
        if (view := views.get(timeframe)) is not None
        and view.indicators.return_interval is not None
    ]
    if not intervals:
        return "auxiliary", 0, None
    sample_size = min(item.coverage_sample_size for item in intervals)
    mean_coverage = sum(
        (item.empirical_coverage for item in intervals), D("0")
    ) / D(len(intervals))
    if all(conformal_coverage_is_valid(item) for item in intervals):
        return "prequential", sample_size, mean_coverage
    return "auxiliary", sample_size, mean_coverage


def _structure_signal(view: TimeframeAnalysis) -> tuple[Decimal, Decimal]:
    signal = {
        "strong_bullish": D("1"),
        "bullish": D("0.70"),
        "neutral": D("0"),
        "bearish": D("-0.70"),
        "strong_bearish": D("-1"),
    }[view.structure.trend]
    if view.structure.bos == "up":
        signal += D("0.15")
    elif view.structure.bos == "down":
        signal -= D("0.15")
    if view.structure.choch == "up":
        signal += D("0.25")
    elif view.structure.choch == "down":
        signal -= D("0.25")
    vwap = view.indicators.vwap
    if vwap is not None:
        if view.close > vwap:
            signal += D("0.10")
        elif view.close < vwap:
            signal -= D("0.10")
    adx = view.indicators.adx14
    trend_strength = (
        D("0.50")
        if adx is None
        else D("0.50")
        + D("0.50") * _clamp((adx - D("15")) / D("20"), D("0"), D("1"))
    )
    reliability = _quality(view) * trend_strength * _volatility_reliability(view)
    return _clamp(signal, D("-1"), D("1")), reliability


def _momentum_signal(
    view: TimeframeAnalysis,
) -> tuple[Decimal, Decimal] | None:
    histogram = view.indicators.macd_histogram
    rsi = view.indicators.rsi14
    if histogram is None or rsi is None:
        return None
    histogram_signal = (
        D("1") if histogram > 0 else D("-1") if histogram < 0 else D("0")
    )
    rsi_signal = _clamp((rsi - D("50")) / D("22"), D("-1"), D("1"))
    signal = D("0.65") * histogram_signal + D("0.35") * rsi_signal
    volume = view.indicators.volume_ratio20
    volume_reliability = (
        D("0.50")
        if volume is None
        else _clamp(volume, D("0.25"), D("1"))
    )
    reliability = (
        _quality(view) * _volatility_reliability(view) * volume_reliability
    )
    return _clamp(signal, D("-1"), D("1")), reliability


def _derivative_signal(
    view: TimeframeAnalysis,
) -> tuple[Decimal, Decimal] | None:
    trend = view.indicators.causal_trend
    if trend is None:
        return None
    signal = _clamp(
        D("0.80") * _clamp(trend.velocity_to_volatility, D("-1"), D("1"))
        + D("0.20")
        * _clamp(trend.acceleration_to_volatility, D("-1"), D("1")),
        D("-1"),
        D("1"),
    )
    state = view.indicators.causal_state
    stability = D("1") if state is None else D("1") - state.shock_score
    reliability = (
        _quality(view)
        * trend.confidence
        * (D("0.50") + D("0.50") * trend.fit_r2)
        * stability
    )
    return signal, _clamp(reliability, D("0"), D("1"))


def _state_signal(
    view: TimeframeAnalysis,
) -> tuple[Decimal, Decimal] | None:
    state = view.indicators.causal_state
    if state is None:
        return None
    signal = _clamp(
        D("0.80") * _clamp(state.velocity_z / D("3"), D("-1"), D("1"))
        + D("0.20")
        * _clamp(state.acceleration_z / D("3"), D("-1"), D("1")),
        D("-1"),
        D("1"),
    )
    reliability = (
        _quality(view)
        * state.confidence
        * (D("1") - state.shock_score)
        * _volatility_reliability(view)
    )
    return signal, _clamp(reliability, D("0"), D("1"))


def _conformal_signal(
    view: TimeframeAnalysis,
) -> tuple[Decimal, Decimal] | None:
    interval = view.indicators.return_interval
    if interval is None:
        return None
    if interval.lower_log_return > 0:
        signal = D("1")
    elif interval.upper_log_return < 0:
        signal = D("-1")
    else:
        denominator = (
            abs(interval.predicted_log_return) + interval.half_width + _EPSILON
        )
        signal = interval.predicted_log_return / denominator
    strength = abs(interval.predicted_log_return) / (
        abs(interval.predicted_log_return) + interval.half_width + _EPSILON
    )
    reliability = (
        _quality(view)
        * interval.empirical_coverage
        * strength
        * _volatility_reliability(view)
    )
    return (
        _clamp(signal, D("-1"), D("1")),
        _clamp(reliability, D("0"), D("1")),
    )


def _aggregate_component(
    code: str,
    views: dict[str, TimeframeAnalysis],
    extractor: LocalSignal,
) -> MathematicalCoreComponent:
    numerator = D("0")
    effective_weight = D("0")
    for timeframe, timeframe_weight in TIMEFRAME_WEIGHTS:
        view = views.get(timeframe)
        if view is None:
            continue
        extracted = extractor(view)
        if extracted is None:
            continue
        signal, reliability = extracted
        weight = timeframe_weight * reliability
        numerator += weight * signal
        effective_weight += weight
    signal = D("0") if effective_weight == 0 else numerator / effective_weight
    reliability = _clamp(effective_weight, D("0"), D("1"))
    validation_level, validation_sample_size, validation_metric = (
        _component_validation(code, views)
    )
    return MathematicalCoreComponent(
        code=code,
        signal=_clamp(signal, D("-1"), D("1")),
        reliability=reliability,
        validation_level=validation_level,
        validation_sample_size=validation_sample_size,
        validation_metric=validation_metric,
        detail=f"{code}_multi_timeframe_causal_aggregate",
    )


def _fuse_components(
    components: list[MathematicalCoreComponent],
    *,
    weights: dict[str, Decimal],
    auxiliary: bool,
    coverage_scale: Decimal = D("1"),
) -> tuple[Decimal, Decimal, Decimal]:
    selected = [
        component
        for component in components
        if component.code in weights
        and (
            component.validation_level == "auxiliary"
            if auxiliary
            else component.validation_level != "auxiliary"
        )
    ]
    effective_weights = [
        weights[component.code] * component.reliability
        for component in selected
    ]
    denominator = sum(effective_weights, D("0"))
    if denominator == 0:
        return D("0"), D("0"), D("0")
    directional_score = sum(
        (
            weight * component.signal
            for weight, component in zip(
                effective_weights, selected, strict=True
            )
        ),
        D("0"),
    ) / denominator
    disagreement = sum(
        (
            weight * abs(component.signal - directional_score)
            for weight, component in zip(
                effective_weights, selected, strict=True
            )
        ),
        D("0"),
    ) / (D("2") * denominator)
    coverage = _clamp(denominator / coverage_scale, D("0"), D("1"))
    consensus = _clamp(D("1") - disagreement, D("0"), D("1"))
    return (
        _clamp(directional_score, D("-1"), D("1")),
        coverage,
        consensus,
    )


def _instability(views: dict[str, TimeframeAnalysis]) -> Decimal:
    numerator = D("0")
    denominator = D("0")
    for timeframe, weight in TIMEFRAME_WEIGHTS:
        view = views.get(timeframe)
        if view is None:
            continue
        state = view.indicators.causal_state
        shock = state.shock_score if state is not None else D("0")
        volatility_risk = {
            "low": D("0"),
            "normal": D("0"),
            "high": D("0.25"),
            "extreme": D("0.80"),
        }[view.volatility]
        data_risk = D("0") if view.data_quality_ok else D("1")
        numerator += weight * max(shock, volatility_risk, data_risk)
        denominator += weight
    if denominator == 0:
        return D("1")
    return _clamp(numerator / denominator, D("0"), D("1"))


def mathematical_core_snapshot(
    views: dict[str, TimeframeAnalysis],
) -> MathematicalCoreSnapshot:
    """Fuse every analysis family into one causal, auditable contract."""

    extractors: dict[str, LocalSignal] = {
        "structure": _structure_signal,
        "momentum": _momentum_signal,
        "derivative": _derivative_signal,
        "state": _state_signal,
        "conformal": _conformal_signal,
    }
    components = [
        _aggregate_component(code, views, extractor)
        for code, extractor in extractors.items()
    ]
    directional_score, coverage, consensus = _fuse_components(
        components,
        weights=EXECUTION_METHOD_WEIGHTS,
        auxiliary=False,
    )
    (
        auxiliary_directional_score,
        auxiliary_coverage,
        auxiliary_consensus,
    ) = _fuse_components(
        components,
        weights=AUXILIARY_METHOD_WEIGHTS,
        auxiliary=True,
    )
    instability = _instability(views)
    confidence = _clamp(
        coverage
        * consensus
        * abs(directional_score)
        * (D("1") - instability),
        D("0"),
        D("1"),
    )
    auxiliary_confidence = _clamp(
        auxiliary_coverage
        * auxiliary_consensus
        * abs(auxiliary_directional_score)
        * (D("1") - instability),
        D("0"),
        D("1"),
    )

    has_configured_view = any(timeframe in views for timeframe, _ in TIMEFRAME_WEIGHTS)
    if not has_configured_view:
        status = "insufficient"
    elif instability >= D("0.65"):
        status = "unstable"
    elif coverage < D("0.35"):
        status = "insufficient"
    elif directional_score >= D("0.25") and confidence >= D("0.20"):
        status = "long"
    elif directional_score <= D("-0.25") and confidence >= D("0.20"):
        status = "short"
    else:
        status = "neutral"

    return MathematicalCoreSnapshot(
        status=status,
        directional_score=_clamp(directional_score, D("-1"), D("1")),
        confidence=confidence,
        coverage=coverage,
        consensus=consensus,
        instability=instability,
        auxiliary_directional_score=auxiliary_directional_score,
        auxiliary_confidence=auxiliary_confidence,
        components=components,
    )
