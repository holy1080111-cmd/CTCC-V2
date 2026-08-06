from datetime import datetime, timezone
from decimal import Decimal

from app.domain.analysis import IndicatorSnapshot, MultiTimeframeAnalysis, StructureSnapshot, TimeframeAnalysis
from app.domain.market import MarketSnapshot, OrderBook, Ticker
from app.strategies.base import StrategyContext
from app.strategies.trend_pullback import evaluate


def _view(tf: str, bias: str) -> TimeframeAnalysis:
    return TimeframeAnalysis(
        timeframe=tf,
        candle_count=250,
        last_closed_at=datetime.now(timezone.utc),
        close=Decimal("100"),
        data_quality_ok=True,
        indicators=IndicatorSnapshot(
            ema20=Decimal("100"), ema50=Decimal("98"), ema200=Decimal("95"),
            atr14=Decimal("1"), atr_pct=Decimal("1"), rsi14=Decimal("55"),
            macd=Decimal("1"), macd_signal=Decimal("0.5"), macd_histogram=Decimal("0.5"),
            adx14=Decimal("25"), vwap=Decimal("99"), volume_ratio20=Decimal("1.2"),
        ),
        structure=StructureSnapshot(trend="bullish", swing_structure="HH/HL", bos="up"),
        volatility="normal",
        directional_bias=bias,
    )


def test_trend_pullback_can_create_candidate() -> None:
    views = {tf: _view(tf, "long") for tf in ("4H", "1H", "15m", "5m")}
    analysis = MultiTimeframeAnalysis(
        symbol="BTC/USDT:USDT", instrument_id="BTC-USDT-SWAP", price=Decimal("100"),
        regime="bull_trend", overall_bias="long", alignment_score=100, trade_ready=True,
        timeframe_analyses=views, generated_at=datetime.now(timezone.utc), version="0.5.0",
    )
    ticker = Ticker(
        instrument_id="BTC-USDT-SWAP", last=Decimal("100"), bid=Decimal("99.99"), ask=Decimal("100.01"),
        bid_size=Decimal("1"), ask_size=Decimal("1"), open_24h=Decimal("98"), high_24h=Decimal("102"),
        low_24h=Decimal("97"), volume_24h=Decimal("1"), volume_quote_24h=Decimal("1"),
        timestamp=datetime.now(timezone.utc),
    )
    market = MarketSnapshot(
        symbol="BTC/USDT:USDT", instrument_id="BTC-USDT-SWAP", ticker=ticker,
        mark_price=Decimal("100"), funding_rate=Decimal("0.0001"), next_funding_time=None,
        open_interest_contracts=Decimal("1"), open_interest_currency=Decimal("1"),
        order_book=OrderBook(instrument_id="BTC-USDT-SWAP", bids=[], asks=[], timestamp=datetime.now(timezone.utc)),
        candles={}, quality={}, received_at=datetime.now(timezone.utc),
    )
    result = evaluate(StrategyContext(analysis, market, 72, Decimal("1.8")))
    assert result.eligible is True
    assert result.candidate is not None
    assert result.candidate.stop_loss < result.candidate.entry < result.candidate.take_profit
