from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from app.domain.risk import AccountRiskState, RiskDecision, RiskLimits
from app.domain.strategy import TradeCandidate

D = Decimal
QTY_QUANTUM = D("0.00000001")
MONEY_QUANTUM = D("0.00000001")


def _loss_ratio(loss_amount: Decimal, equity: Decimal) -> Decimal:
    return max(D("0"), -loss_amount) / equity


def _drawdown(account: AccountRiskState) -> Decimal:
    peak = account.peak_equity or account.equity
    return max(D("0"), peak - account.equity) / peak


def evaluate_risk(
    candidate: TradeCandidate,
    account: AccountRiskState,
    limits: RiskLimits,
) -> RiskDecision:
    reasons: list[str] = []
    now = datetime.now(timezone.utc)
    stop_distance = abs(candidate.entry - candidate.stop_loss)

    if candidate.expires_at <= now:
        reasons.append("candidate_expired")
    if candidate.score < limits.minimum_score:
        reasons.append("score_below_minimum")
    if candidate.risk_reward < limits.minimum_risk_reward:
        reasons.append("risk_reward_below_minimum")
    if stop_distance <= 0:
        reasons.append("invalid_stop_distance")
    if _loss_ratio(account.daily_realized_pnl, account.equity) >= limits.max_daily_loss_pct:
        reasons.append("daily_loss_limit_reached")
    if _loss_ratio(account.weekly_realized_pnl, account.equity) >= limits.max_weekly_loss_pct:
        reasons.append("weekly_loss_limit_reached")
    if _drawdown(account) >= limits.max_drawdown_pct:
        reasons.append("drawdown_limit_reached")
    if account.consecutive_losses >= limits.max_consecutive_losses:
        reasons.append("consecutive_loss_limit_reached")
    if account.open_positions >= limits.max_open_positions:
        reasons.append("open_position_limit_reached")
    if account.same_direction_positions >= limits.max_same_direction_positions:
        reasons.append("same_direction_limit_reached")
    if account.correlated_positions >= limits.max_correlated_positions:
        reasons.append("correlation_limit_reached")

    requested_risk = limits.risk_per_trade_pct
    risk_budget = account.equity * requested_risk
    quantity_by_risk = D("0") if stop_distance <= 0 else risk_budget / stop_distance
    quantity_by_notional = limits.max_notional / candidate.entry
    quantity = min(quantity_by_risk, quantity_by_notional).quantize(QTY_QUANTUM, rounding=ROUND_DOWN)

    if quantity < limits.minimum_quantity:
        reasons.append("quantity_below_minimum")
        quantity = D("0")

    notional = (quantity * candidate.entry).quantize(MONEY_QUANTUM)
    max_loss = (quantity * stop_distance).quantize(MONEY_QUANTUM)
    approved_risk = (max_loss / account.equity).quantize(D("0.00000001")) if quantity > 0 else D("0")

    approved = not reasons and quantity > 0
    return RiskDecision(
        decision="approved" if approved else "rejected",
        candidate=candidate,
        requested_risk_pct=requested_risk,
        approved_risk_pct=approved_risk if approved else D("0"),
        approved_quantity=quantity if approved else D("0"),
        notional=notional if approved else D("0"),
        max_loss_amount=max_loss if approved else D("0"),
        stop_distance=stop_distance,
        reason_codes=sorted(set(reasons)),
    )
