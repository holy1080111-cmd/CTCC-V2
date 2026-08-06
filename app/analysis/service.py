from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.config.settings import get_settings
from app.domain.analysis import IndicatorSnapshot, MultiTimeframeAnalysis, TimeframeAnalysis
from app.domain.market import Candle, MarketSnapshot
from app.indicators import adx, atr, ema, macd, rsi, volume_ratio, vwap
from app.market.service import MarketDataService
from app.regime import classify_regime
from app.structure import analyze_structure

D = Decimal


def _q(value: Decimal | None, places: str = "0.00000001") -> Decimal | None:
    return None if value is None else value.quantize(D(places), rounding=ROUND_HALF_UP)


def _volatility(atr_pct: Decimal | None) -> str:
    if atr_pct is None:
        return "normal"
    if atr_pct < D("0.20"):
        return "low"
    if atr_pct < D("1.00"):
        return "normal"
    if atr_pct < D("2.50"):
        return "high"
    return "extreme"


def analyze_timeframe(timeframe: str, candles: list[Candle], quality) -> TimeframeAnalysis:
    confirmed = [c for c in candles if c.confirmed]
    if len(confirmed) < 30:
        raise ValueError(f"{timeframe} requires at least 30 confirmed candles")
    closes = [c.close for c in confirmed]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    atr14 = atr(confirmed, 14)
    atr_pct = None if atr14 is None or confirmed[-1].close <= 0 else atr14 / confirmed[-1].close * D("100")
    macd_value, macd_signal, macd_hist = macd(closes)
    indicators = IndicatorSnapshot(
        ema20=_q(e20), ema50=_q(e50), ema200=_q(e200), atr14=_q(atr14), atr_pct=_q(atr_pct),
        rsi14=_q(rsi(closes, 14)), macd=_q(macd_value), macd_signal=_q(macd_signal),
        macd_histogram=_q(macd_hist), adx14=_q(adx(confirmed, 14)), vwap=_q(vwap(confirmed, 20)),
        volume_ratio20=_q(volume_ratio(confirmed, 20)),
    )
    structure = analyze_structure(confirmed, e20, e50, e200)
    evidence: list[str] = []
    counter: list[str] = []
    if structure.trend in {"strong_bullish", "bullish"}: evidence.append("EMA trend supports long bias")
    elif structure.trend in {"strong_bearish", "bearish"}: evidence.append("EMA trend supports short bias")
    if structure.bos: evidence.append(f"confirmed close is beyond latest swing: BOS {structure.bos}")
    if indicators.macd_histogram is not None:
        (evidence if indicators.macd_histogram > 0 else counter).append("MACD histogram is positive" if indicators.macd_histogram > 0 else "MACD histogram is negative")
    if indicators.rsi14 is not None and indicators.rsi14 >= D("70"): counter.append("RSI is overbought")
    if indicators.rsi14 is not None and indicators.rsi14 <= D("30"): counter.append("RSI is oversold")
    if indicators.volume_ratio20 is not None and indicators.volume_ratio20 < D("0.8"): counter.append("latest volume is below its 20-candle baseline")

    if structure.trend in {"strong_bullish", "bullish"} and structure.choch != "down": bias = "long"
    elif structure.trend in {"strong_bearish", "bearish"} and structure.choch != "up": bias = "short"
    else: bias = "neutral"
    issue_strings = [f"{issue.severity}:{issue.code}" for issue in quality.issues]
    return TimeframeAnalysis(
        timeframe=timeframe, candle_count=len(confirmed), last_closed_at=confirmed[-1].timestamp,
        close=confirmed[-1].close, data_quality_ok=quality.ok, data_quality_issues=issue_strings,
        indicators=indicators, structure=structure, volatility=_volatility(atr_pct),
        directional_bias=bias, evidence=evidence, counter_evidence=counter,
    )


class AnalysisService:
    def __init__(self, market_service: MarketDataService | None = None) -> None:
        self.market_service = market_service or MarketDataService()

    async def analyze(self, symbol: str, candle_limit: int = 250) -> MultiTimeframeAnalysis:
        snapshot = await self.market_service.snapshot(symbol, candle_limit)
        return self.analyze_snapshot(snapshot)

    def analyze_snapshot(self, snapshot: MarketSnapshot) -> MultiTimeframeAnalysis:
        views = {bar: analyze_timeframe(bar, snapshot.candles[bar], snapshot.quality[bar]) for bar in snapshot.candles}
        blockers: list[str] = []
        for bar, report in snapshot.quality.items():
            if not report.ok:
                blockers.append(f"{bar}_data_quality")
        ordered = [views[bar].directional_bias for bar in ("4H", "1H", "15m", "5m") if bar in views]
        long_count, short_count = ordered.count("long"), ordered.count("short")
        if long_count >= 3 and ordered[:2] == ["long", "long"]:
            overall, score = "long", long_count * 25
        elif short_count >= 3 and ordered[:2] == ["short", "short"]:
            overall, score = "short", short_count * 25
        else:
            overall, score = "neutral", max(long_count, short_count) * 25
            blockers.append("multi_timeframe_not_aligned")
        trade_ready = not blockers and overall != "neutral"
        return MultiTimeframeAnalysis(
            symbol=snapshot.symbol, instrument_id=snapshot.instrument_id, price=snapshot.ticker.last,
            regime=classify_regime(views), overall_bias=overall, alignment_score=score,
            trade_ready=trade_ready, blockers=sorted(set(blockers)), timeframe_analyses=views,
            generated_at=datetime.now(timezone.utc), version=get_settings().app_version,
        )
