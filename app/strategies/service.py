from datetime import UTC, datetime
from decimal import Decimal

from app.analysis import AnalysisService
from app.config.settings import get_settings
from app.domain.strategy import (
    RegimeRoutingEvidence,
    StrategyDecision,
    StrategyEvaluation,
)
from app.market.service import MarketDataService
from app.strategies.base import StrategyContext
from app.strategies.mathematical_confirmation import mathematical_score_cap
from app.strategies.regime import RouteDecision, route_regime
from app.strategies.registry import STRATEGY_EVALUATORS


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
        route = route_regime(analysis)
        allowed = (
            set(route.allowed_strategies)
            if route.decision == RouteDecision.ALLOW_SCORING
            else set()
        )
        evaluations = []
        for name, evaluator in STRATEGY_EVALUATORS.items():
            if name not in allowed:
                # Zero is a non-scored audit placeholder, not a computed score.
                evaluations.append(
                    StrategyEvaluation(
                        strategy=name,
                        direction="neutral",
                        eligible=False,
                        completion_ratio=Decimal(0),
                        score=0,
                        scoring_performed=False,
                        vetoes=["regime_not_permitted"],
                    )
                )
                continue
            evaluation = evaluator(context)
            if evaluation.strategy != name or (
                evaluation.candidate is not None
                and (
                    evaluation.candidate.strategy != name
                    or evaluation.candidate.direction != evaluation.direction
                )
            ):
                evaluation = evaluation.model_copy(
                    update={
                        "strategy": name,
                        "eligible": False,
                        "candidate": None,
                        "vetoes": sorted(
                            set([*evaluation.vetoes, "strategy_identity_mismatch"])
                        ),
                    }
                )
            evaluations.append(evaluation)
        # Evaluator/model_copy output cannot override a failed setup gate.
        # Rebuild codes from components as well as the declared failure list.
        for index, evaluation in enumerate(evaluations):
            required_failures = sorted(
                set(evaluation.required_failures)
                | {
                    component.code
                    for component in evaluation.score_components
                    if component.required and not component.passed
                }
            )
            if required_failures:
                evaluations[index] = evaluation.model_copy(
                    update={
                        "eligible": False,
                        "candidate": None,
                        "required_failures": required_failures,
                    }
                )
        disabled = disabled_strategies or set()
        if disabled:
            evaluations = [
                item.model_copy(
                    update={
                        "eligible": False,
                        "candidate": None,
                        "vetoes": sorted(
                            set([*item.vetoes, "strategy_disabled_by_operator"])
                        ),
                    }
                )
                if item.strategy in disabled
                else item
                for item in evaluations
            ]
        evaluations = [
            item.model_copy(
                update={
                    "eligible": False,
                    "vetoes": sorted(
                        set(
                            [
                                *item.vetoes,
                                (
                                    "mathematical_core_regime_instability"
                                    if item.candidate.mathematical_confirmation.status
                                    == "unstable"
                                    else "mathematical_core_opposes_trade_direction"
                                ),
                            ]
                        )
                    ),
                }
            )
            if (
                item.candidate is not None
                and item.candidate.mathematical_confirmation is not None
                and item.candidate.mathematical_confirmation.status
                in {"opposed", "unstable"}
            )
            else item
            for item in evaluations
        ]
        eligible = [
            item
            for item in evaluations
            if item.eligible
            and item.scoring_performed
            and item.strategy in allowed
            and not item.vetoes
            and not item.required_failures
            and item.candidate is not None
        ]

        def selection_key(item):
            candidate = item.candidate
            if candidate is None:
                return (-1, item.score, Decimal("0"), 0)
            confirmation = candidate.mathematical_confirmation
            effective_score = item.score
            confidence = Decimal("0")
            auxiliary_bonus = 0
            if confirmation is not None:
                effective_score = min(
                    item.score,
                    mathematical_score_cap(
                        confirmation,
                        medium_minimum=settings.okx_demo_score_medium_min,
                        high_minimum=settings.okx_demo_score_high_min,
                    ),
                )
                confidence = confirmation.confidence
                auxiliary_bonus = confirmation.auxiliary_bonus
            # Auxiliary evidence is deliberately last: it can break a true
            # tie, but cannot lift eligibility, execution score, or risk.
            return (effective_score, item.score, confidence, auxiliary_bonus)

        selected = max(
            eligible,
            key=selection_key,
            default=None,
        )
        blockers = list(analysis.blockers)
        if route.decision == RouteDecision.NO_TRADE:
            blockers.extend(route.fail_codes)
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
            regime_route=RegimeRoutingEvidence(
                regime=route.regime,
                decision=route.decision.value,
                allowed_strategies=route.allowed_strategies,
                reasons=route.reasons,
                fail_codes=route.fail_codes,
                snapshot_basis=route.snapshot_basis,
                snapshot_sha256=route.snapshot_sha256,
            ),
            generated_at=datetime.now(UTC),
            version=settings.app_version,
        )
