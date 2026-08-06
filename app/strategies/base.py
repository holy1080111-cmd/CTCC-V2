from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable

from app.domain.analysis import MultiTimeframeAnalysis, TimeframeAnalysis
from app.domain.market import MarketSnapshot
from app.domain.strategy import ScoreComponent, StrategyEvaluation, TradeCandidate

D = Decimal


@dataclass(frozen=True)
class StrategyContext:
    analysis: MultiTimeframeAnalysis
    market: MarketSnapshot
    minimum_score: int
    minimum_risk_reward: Decimal

    @property
    def price(self) -> Decimal:
        return self.market.ticker.last

    def tf(self, timeframe: str) -> TimeframeAnalysis:
        return self.analysis.timeframe_analyses[timeframe]


@dataclass(frozen=True)
class Condition:
    code: str
    label: str
    maximum: int
    passed: bool
    detail: str
    veto: bool = False


def _q(value: Decimal) -> Decimal:
    return value.quantize(D("0.00000001"), rounding=ROUND_HALF_UP)


def atr_distance(ctx: StrategyContext, timeframe: str = "15m", multiplier: Decimal = D("1.5")) -> Decimal:
    atr = ctx.tf(timeframe).indicators.atr14
    fallback = ctx.price * D("0.005")
    return max(fallback, (atr or fallback) * multiplier)


def build_candidate(
    ctx: StrategyContext,
    strategy: str,
    direction: str,
    score: int,
    reasons: list[str],
    counter_evidence: list[str],
    stop_distance: Decimal | None = None,
) -> TradeCandidate:
    distance = stop_distance or atr_distance(ctx)
    entry = ctx.price
    rr = ctx.minimum_risk_reward
    if direction == "long":
        stop, take = entry - distance, entry + distance * rr
    else:
        stop, take = entry + distance, entry - distance * rr
    return TradeCandidate(
        strategy=strategy,
        direction=direction,
        score=score,
        entry=_q(entry),
        stop_loss=_q(stop),
        take_profit=_q(take),
        risk_reward=rr,
        invalidation=f"{direction} setup invalidates at stop loss",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
        reasons=reasons,
        counter_evidence=counter_evidence,
    )


def evaluate_conditions(
    ctx: StrategyContext,
    strategy: str,
    direction: str,
    conditions: list[Condition],
    extra_vetoes: list[str] | None = None,
) -> StrategyEvaluation:
    total = sum(item.maximum for item in conditions) or 1
    earned = sum(item.maximum for item in conditions if item.passed)
    score = min(100, round(earned / total * 100))
    ratio = D(str(earned)) / D(str(total))
    passed = [item.label for item in conditions if item.passed]
    failed = [item.label for item in conditions if not item.passed]
    vetoes = [item.detail for item in conditions if item.veto and not item.passed]
    vetoes.extend(extra_vetoes or [])
    components = [
        ScoreComponent(
            code=item.code,
            label=item.label,
            points=item.maximum if item.passed else 0,
            maximum=item.maximum,
            passed=item.passed,
            detail=item.detail,
        )
        for item in conditions
    ]
    eligible = score >= ctx.minimum_score and not vetoes and direction in {"long", "short"}
    candidate = None
    if eligible:
        candidate = build_candidate(
            ctx,
            strategy,
            direction,
            score,
            reasons=passed,
            counter_evidence=failed,
        )
    return StrategyEvaluation(
        strategy=strategy,
        direction=direction if direction in {"long", "short"} else "neutral",
        eligible=eligible,
        completion_ratio=ratio.quantize(D("0.0001")),
        score=score,
        passed_conditions=passed,
        failed_conditions=failed,
        vetoes=sorted(set(vetoes)),
        score_components=components,
        candidate=candidate,
    )


def common_vetoes(ctx: StrategyContext, direction: str) -> list[str]:
    vetoes = [item for item in ctx.analysis.blockers if "data_quality" in item]
    spread_pct = ctx.market.ticker.spread_pct
    if spread_pct > D("0.08"):
        vetoes.append("spread_above_0.08_percent")
    if ctx.tf("5m").volatility == "extreme":
        vetoes.append("5m_extreme_volatility")
    funding = ctx.market.funding_rate
    if direction == "long" and funding > D("0.0015"):
        vetoes.append("funding_excessively_positive_for_long")
    if direction == "short" and funding < D("-0.0015"):
        vetoes.append("funding_excessively_negative_for_short")
    return vetoes
