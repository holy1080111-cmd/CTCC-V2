from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.analysis.mathematical_core import mathematical_core_snapshot
from app.domain.analysis import (
    CausalReturnIntervalSnapshot,
    CausalStateSnapshot,
    CausalTrendSnapshot,
    IndicatorSnapshot,
    MathematicalCoreSnapshot,
    StructureSnapshot,
    TimeframeAnalysis,
)

D = Decimal


def _view(
    timeframe: str,
    *,
    direction: str = "long",
    shock: str = "0",
    quality: bool = True,
    empirical_coverage: str = "0.9",
    coverage_sample_size: int = 40,
) -> TimeframeAnalysis:
    positive = direction == "long"
    sign = D("1") if positive else D("-1")
    return TimeframeAnalysis(
        timeframe=timeframe,
        candle_count=250,
        last_closed_at=datetime.now(timezone.utc),
        close=D("100"),
        data_quality_ok=quality,
        indicators=IndicatorSnapshot(
            adx14=D("35"),
            vwap=D("99") if positive else D("101"),
            rsi14=D("60") if positive else D("40"),
            macd=D("1") * sign,
            macd_signal=D("0.5") * sign,
            macd_histogram=D("0.5") * sign,
            volume_ratio20=D("1.2"),
            causal_trend=CausalTrendSnapshot(
                window=21,
                log_velocity_per_bar=D("0.001") * sign,
                log_acceleration_per_bar2=D("0.0001") * sign,
                log_return_rms_per_bar=D("0.001"),
                velocity_to_volatility=D("1") * sign,
                acceleration_to_volatility=D("0.2") * sign,
                fit_r2=D("0.95"),
                residual_std=D("0.0001"),
                confidence=D("0.9"),
                direction="rising" if positive else "falling",
            ),
            causal_state=CausalStateSnapshot(
                window=34,
                log_velocity_per_bar=D("0.001") * sign,
                log_acceleration_per_bar2=D("0.0001") * sign,
                velocity_std=D("0.0001"),
                acceleration_std=D("0.0001"),
                velocity_z=D("4") * sign,
                acceleration_z=D("1") * sign,
                innovation_z=D("0"),
                shock_score=D(shock),
                confidence=D("0.9"),
                direction="rising" if positive else "falling",
                outlier_count=0,
            ),
            return_interval=CausalReturnIntervalSnapshot(
                confidence_level=D("0.9"),
                predicted_log_return=D("0.001") * sign,
                lower_log_return=(D("0.0005") if positive else D("-0.0015")),
                upper_log_return=(D("0.0015") if positive else D("-0.0005")),
                half_width=D("0.0005"),
                calibration_size=60,
                coverage_sample_size=coverage_sample_size,
                empirical_coverage=D(empirical_coverage),
                direction="rising" if positive else "falling",
            ),
        ),
        structure=StructureSnapshot(
            trend="strong_bullish" if positive else "strong_bearish",
            swing_structure="HH/HL" if positive else "LH/LL",
            bos="up" if positive else "down",
        ),
        volatility="normal",
        directional_bias="long" if positive else "short",
    )


def test_all_analysis_families_fuse_into_high_confidence_long_state() -> None:
    views = {timeframe: _view(timeframe) for timeframe in ("4H", "1H", "15m", "5m")}

    result = mathematical_core_snapshot(views)

    assert result.status == "long"
    assert result.directional_score > D("0.80")
    assert result.confidence > D("0.65")
    assert result.coverage > D("0.75")
    assert [component.code for component in result.components] == [
        "structure",
        "momentum",
        "derivative",
        "state",
        "conformal",
    ]
    assert [component.validation_level for component in result.components] == [
        "auxiliary",
        "auxiliary",
        "analytical",
        "analytical",
        "prequential",
    ]
    assert result.auxiliary_directional_score > D("0.8")
    assert result.auxiliary_confidence > 0


def test_failed_prequential_coverage_demotes_conformal_to_auxiliary() -> None:
    views = {
        timeframe: _view(timeframe, empirical_coverage="0.5")
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = mathematical_core_snapshot(views)
    validated = mathematical_core_snapshot(
        {timeframe: _view(timeframe) for timeframe in ("4H", "1H", "15m", "5m")}
    )
    conformal = next(
        component for component in result.components if component.code == "conformal"
    )

    assert conformal.validation_level == "auxiliary"
    assert conformal.validation_sample_size == 40
    assert conformal.validation_metric == D("0.5")
    assert result.coverage < D("0.75")
    assert result.auxiliary_directional_score == validated.auxiliary_directional_score
    assert result.auxiliary_confidence == validated.auxiliary_confidence
    assert "conformal" not in [
        component.code
        for component in result.components
        if component.validation_level != "auxiliary"
    ]


def test_small_prequential_sample_cannot_enter_execution_core() -> None:
    views = {
        timeframe: _view(timeframe, coverage_sample_size=20)
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = mathematical_core_snapshot(views)
    conformal = next(
        component for component in result.components if component.code == "conformal"
    )

    assert conformal.validation_level == "auxiliary"


def test_auxiliary_structure_and_momentum_cannot_change_execution_core() -> None:
    baseline_views = {timeframe: _view(timeframe) for timeframe in ("4H", "1H", "15m", "5m")}
    opposed_auxiliary_views: dict[str, TimeframeAnalysis] = {}
    for timeframe, view in baseline_views.items():
        opposed_auxiliary_views[timeframe] = view.model_copy(
            update={
                "indicators": view.indicators.model_copy(
                    update={
                        "vwap": D("101"),
                        "rsi14": D("40"),
                        "macd": D("-1"),
                        "macd_signal": D("-0.5"),
                        "macd_histogram": D("-0.5"),
                    }
                ),
                "structure": StructureSnapshot(
                    trend="strong_bearish",
                    swing_structure="LH/LL",
                    bos="down",
                ),
            }
        )

    baseline = mathematical_core_snapshot(baseline_views)
    opposed_auxiliary = mathematical_core_snapshot(opposed_auxiliary_views)

    assert opposed_auxiliary.directional_score == baseline.directional_score
    assert opposed_auxiliary.coverage == baseline.coverage
    assert opposed_auxiliary.confidence == baseline.confidence
    assert baseline.auxiliary_directional_score > 0
    assert opposed_auxiliary.auxiliary_directional_score < 0


def test_opposite_timeframes_cancel_instead_of_creating_false_precision() -> None:
    views = {
        "4H": _view("4H", direction="long"),
        "1H": _view("1H", direction="short"),
        "15m": _view("15m", direction="short"),
        "5m": _view("5m", direction="long"),
    }

    result = mathematical_core_snapshot(views)

    assert result.status == "neutral"
    assert result.confidence < D("0.20")


def test_endpoint_shocks_turn_shared_core_unstable() -> None:
    views = {
        timeframe: _view(timeframe, shock="0.90")
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = mathematical_core_snapshot(views)

    assert result.status == "unstable"
    assert result.instability >= D("0.90")
    assert result.confidence < D("0.10")


def test_failed_data_quality_is_unstable_and_fail_closed() -> None:
    views = {
        timeframe: _view(timeframe, quality=False)
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = mathematical_core_snapshot(views)

    assert result.status == "unstable"
    assert result.coverage == D("0")
    assert result.instability == D("1")


def test_single_timeframe_cannot_claim_full_mathematical_coverage() -> None:
    result = mathematical_core_snapshot({"4H": _view("4H")})

    assert result.status == "insufficient"
    assert result.coverage < D("0.35")


def test_single_shocked_timeframe_is_unstable_not_merely_insufficient() -> None:
    result = mathematical_core_snapshot({"4H": _view("4H", shock="0.9")})

    assert result.status == "unstable"


def test_empty_core_is_insufficient() -> None:
    result = mathematical_core_snapshot({})

    assert result.status == "insufficient"


def test_domain_rejects_forged_directional_status() -> None:
    with pytest.raises(ValidationError):
        MathematicalCoreSnapshot(
            status="long",
            directional_score=D("-0.8"),
            confidence=D("0.8"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=D("0.1"),
        )
