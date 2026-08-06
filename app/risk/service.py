from decimal import Decimal

from app.config.settings import get_settings
from app.domain.risk import AccountRiskState, RiskDecision, RiskLimits
from app.domain.strategy import TradeCandidate
from app.risk.engine import evaluate_risk


class RiskService:
    def default_limits(self) -> RiskLimits:
        settings = get_settings()
        return RiskLimits(
            risk_per_trade_pct=Decimal(str(settings.risk_per_trade_pct)),
            max_daily_loss_pct=Decimal(str(settings.max_daily_loss_pct)),
            max_weekly_loss_pct=Decimal(str(settings.max_weekly_loss_pct)),
            max_drawdown_pct=Decimal(str(settings.max_drawdown_pct)),
            max_consecutive_losses=settings.max_consecutive_losses,
            max_open_positions=settings.max_open_positions,
            max_same_direction_positions=settings.max_same_direction_positions,
            max_correlated_positions=settings.max_correlated_positions,
            max_notional=Decimal(str(settings.order_size_cap_usdt)),
            minimum_score=settings.strategy_min_score,
            minimum_risk_reward=Decimal(str(settings.strategy_min_risk_reward)),
        )

    def evaluate(
        self,
        candidate: TradeCandidate,
        account: AccountRiskState,
        limits: RiskLimits | None = None,
    ) -> RiskDecision:
        return evaluate_risk(candidate, account, limits or self.default_limits())
