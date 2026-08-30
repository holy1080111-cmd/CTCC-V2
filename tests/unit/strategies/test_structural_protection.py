from datetime import datetime, timezone
from decimal import Decimal

from app.domain.analysis import (
    IndicatorSnapshot,
    MultiTimeframeAnalysis,
    StructureSnapshot,
    TimeframeAnalysis,
)
from app.strategies.structural_protection import structural_protection_geometry

D = Decimal


def _view(
    timeframe: str,
    *,
    supports: list[str],
    resistances: list[str],
    atr: str = "1",
) -> TimeframeAnalysis:
    return TimeframeAnalysis(
        timeframe=timeframe,
        candle_count=250,
        last_closed_at=datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc),
        close=D("100"),
        data_quality_ok=True,
        indicators=IndicatorSnapshot(atr14=D(atr)),
        structure=StructureSnapshot(
            trend="neutral",
            swing_structure="HH/HL",
            last_swing_low=D(supports[0]) if supports else None,
            last_swing_high=D(resistances[0]) if resistances else None,
            support_levels=[D(value) for value in supports],
            resistance_levels=[D(value) for value in resistances],
        ),
        volatility="normal",
        directional_bias="neutral",
    )


def _analysis(view: TimeframeAnalysis) -> MultiTimeframeAnalysis:
    return MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT",
        instrument_id="BTC-USDT-SWAP",
        price=D("100"),
        regime="range",
        overall_bias="neutral",
        alignment_score=0,
        trade_ready=True,
        timeframe_analyses={view.timeframe: view},
        generated_at=datetime(2026, 8, 12, 8, 1, tzinfo=timezone.utc),
    )


def test_long_uses_nearest_confirmed_support_and_resistance() -> None:
    plan = structural_protection_geometry(
        _analysis(
            _view(
                "15m",
                supports=["98", "95"],
                resistances=["103", "110"],
            )
        ),
        direction="long",
        entry=D("100"),
    )

    assert plan is not None
    assert plan.timeframe == "15m"
    assert plan.stop_anchor == D("98")
    assert plan.target_anchor == D("103")
    assert plan.volatility_buffer == D("0.25")
    assert plan.stop_loss == D("97.75")
    assert plan.take_profit == D("103")
    assert plan.source_closed_at < datetime(2026, 8, 12, 8, 1, tzinfo=timezone.utc)


def test_short_geometry_is_exact_mirror() -> None:
    plan = structural_protection_geometry(
        _analysis(
            _view(
                "15m",
                supports=["97", "92"],
                resistances=["102", "106"],
            )
        ),
        direction="short",
        entry=D("100"),
    )

    assert plan is not None
    assert plan.stop_anchor == D("102")
    assert plan.target_anchor == D("97")
    assert plan.stop_loss == D("102.25")
    assert plan.take_profit == D("97")


def test_incomplete_structure_fails_closed() -> None:
    plan = structural_protection_geometry(
        _analysis(_view("15m", supports=["98"], resistances=[])),
        direction="long",
        entry=D("100"),
    )

    assert plan is None
