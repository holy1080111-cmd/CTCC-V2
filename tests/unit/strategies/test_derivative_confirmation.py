from datetime import datetime, timezone
from decimal import Decimal

from app.domain.analysis import (
    CausalTrendSnapshot,
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.strategies.derivative_confirmation import derivative_confirmation

D = Decimal


def _view(
    timeframe: str,
    *,
    velocity_ratio: str,
    acceleration_ratio: str = "0.1",
    confidence: str = "0.8",
    fit_r2: str = "0.9",
) -> TimeframeAnalysis:
    return TimeframeAnalysis(
        timeframe=timeframe,
        candle_count=250,
        last_closed_at=datetime.now(timezone.utc),
        close=D("100"),
        data_quality_ok=True,
        indicators=IndicatorSnapshot(
            causal_trend=CausalTrendSnapshot(
                window=21,
                log_velocity_per_bar=D("0.001"),
                log_acceleration_per_bar2=D("0.0001"),
                log_return_rms_per_bar=D("0.001"),
                velocity_to_volatility=D(velocity_ratio),
                acceleration_to_volatility=D(acceleration_ratio),
                fit_r2=D(fit_r2),
                residual_std=D("0.0001"),
                confidence=D(confidence),
                direction="rising" if D(velocity_ratio) > 0 else "falling",
            )
        ),
        structure=StructureSnapshot(trend="bullish", swing_structure="HH/HL"),
        volatility="normal",
        directional_bias="long",
    )


def _analysis(views: dict[str, TimeframeAnalysis]) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime="bull_trend",
        overall_bias="long",
        alignment_score=100,
        trade_ready=True,
        timeframe_analyses=views,
        generated_at=datetime.now(timezone.utc),
    )


def test_velocity_and_acceleration_confirm_trade_direction() -> None:
    views = {
        timeframe: _view(timeframe, velocity_ratio="0.9")
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = derivative_confirmation(_analysis(views), "long")

    assert result.status == "confirmed"
    assert result.confidence == D("0.8")
    assert result.alignment_score > D("0.70")
    assert result.aligned_timeframes == ["4H", "1H", "15m", "5m"]


def test_opposing_derivatives_fail_closed_for_direction() -> None:
    views = {
        timeframe: _view(
            timeframe,
            velocity_ratio="-0.9",
            acceleration_ratio="-0.2",
        )
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = derivative_confirmation(_analysis(views), "long")

    assert result.status == "opposed"
    assert result.alignment_score < D("-0.70")
    assert result.opposed_timeframes == ["4H", "1H", "15m", "5m"]


def test_low_quality_fit_is_insufficient_instead_of_directional() -> None:
    views = {
        timeframe: _view(
            timeframe,
            velocity_ratio="1",
            confidence="0.1",
            fit_r2="0.2",
        )
        for timeframe in ("4H", "1H", "15m", "5m")
    }

    result = derivative_confirmation(_analysis(views), "long")

    assert result.status == "insufficient"
    assert result.confidence == D("0")
    assert result.qualified_timeframes == []


def test_long_horizon_only_alignment_cannot_unlock_higher_risk() -> None:
    views = {
        "4H": _view("4H", velocity_ratio="1"),
        "1H": _view("1H", velocity_ratio="1"),
        "15m": _view(
            "15m", velocity_ratio="1", confidence="0.1", fit_r2="0.2"
        ),
        "5m": _view(
            "5m", velocity_ratio="1", confidence="0.1", fit_r2="0.2"
        ),
    }

    result = derivative_confirmation(_analysis(views), "long")

    assert result.status == "mixed"
    assert result.aligned_timeframes == ["4H", "1H"]
