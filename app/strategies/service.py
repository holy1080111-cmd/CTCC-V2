from datetime import datetime, timezone
from decimal import Decimal

from app.analysis import AnalysisService
from app.config.settings import get_settings
from app.domain.strategy import StrategyDecision
from app.market.service import MarketDataService
from app.strategies.base import StrategyContext
from app.strategies.registry import STRATEGIES


class StrategyService:
    def __init__(
        self,
        market_service: MarketDataService | None = None,
        analysis_service: AnalysisService | None = None,
    ) -> None:
        self.market_service = market_service or MarketDataService()
        self.analysis_service = analysis_service or AnalysisService(self.market_service)

    async def evaluate(
        self,
        symbol: str,
        candle_limit: int = 250,
        *,
        disabled_strategies: set[str] | None = None,
    ) -> StrategyDecision:
        snapshot = await self.market_service.snapshot(symbol, candle_limit)
        analysis = self.analysis_service.analyze_snapshot(snapshot)
        settings = get_settings()
        context = StrategyContext(
            analysis=analysis,
            market=snapshot,
            minimum_score=settings.strategy_min_score,
            minimum_risk_reward=Decimal(str(settings.strategy_min_risk_reward)),
        )
        evaluations = [evaluator(context) for evaluator in STRATEGIES]
        disabled = disabled_strategies or set()
        if disabled:
            evaluations = [
                item.model_copy(
                    update={
                        "eligible": False,
                        "candidate": None,
                        "vetoes": sorted(set([*item.vetoes, "strategy_disabled_by_operator"])),
                    }
                )
                if item.strategy in disabled
                else item
                for item in evaluations
            ]
        eligible = [item for item in evaluations if item.eligible and item.candidate is not None]
        selected = max(eligible, key=lambda item: item.score, default=None)
        blockers = list(analysis.blockers)
        if disabled:
            blockers.extend(f"strategy_disabled:{name}" for name in sorted(disabled))
        if selected is None:
            blockers.append("no_strategy_met_minimum_score_and_veto_rules")
        return StrategyDecision(
            symbol=analysis.symbol,
            instrument_id=analysis.instrument_id,
            decision=selected.direction if selected else "no_trade",
            selected_strategy=selected.strategy if selected else None,
            selected_candidate=selected.candidate if selected else None,
            minimum_score=settings.strategy_min_score,
            evaluations=evaluations,
            blockers=sorted(set(blockers)),
            generated_at=datetime.now(timezone.utc),
            version=settings.app_version,
        )
