from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.risk import AccountRiskState, RiskLimits
from app.domain.strategy import TradeCandidate
from app.risk.engine import evaluate_risk


def candidate(**overrides):
    data = dict(
        strategy="trend_pullback",
        direction="long",
        score=82,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        risk_reward=Decimal("2"),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        reasons=["test"],
        counter_evidence=[],
    )
    data.update(overrides)
    return TradeCandidate(**data)


def test_risk_approves_and_sizes_by_stop_distance():
    result = evaluate_risk(
        candidate(),
        AccountRiskState(equity=Decimal("10000")),
        RiskLimits(risk_per_trade_pct=Decimal("0.005"), max_notional=Decimal("5000")),
    )
    assert result.decision == "approved"
    assert result.approved_quantity == Decimal("10.00000000")
    assert result.max_loss_amount == Decimal("50.00000000")
    assert result.approved_risk_pct == Decimal("0.00500000")


def test_risk_caps_quantity_by_notional():
    result = evaluate_risk(
        candidate(entry=Decimal("100"), stop_loss=Decimal("99"), take_profit=Decimal("102")),
        AccountRiskState(equity=Decimal("10000")),
        RiskLimits(risk_per_trade_pct=Decimal("0.01"), max_notional=Decimal("500")),
    )
    assert result.decision == "approved"
    assert result.approved_quantity == Decimal("5.00000000")
    assert result.notional == Decimal("500.00000000")


def test_risk_rejects_after_daily_limit():
    result = evaluate_risk(
        candidate(),
        AccountRiskState(equity=Decimal("10000"), daily_realized_pnl=Decimal("-250")),
        RiskLimits(max_daily_loss_pct=Decimal("0.02")),
    )
    assert result.decision == "rejected"
    assert "daily_loss_limit_reached" in result.reason_codes
    assert result.approved_quantity == 0


def test_risk_rejects_position_and_loss_streak_limits():
    result = evaluate_risk(
        candidate(),
        AccountRiskState(
            equity=Decimal("10000"),
            consecutive_losses=3,
            open_positions=2,
            same_direction_positions=1,
        ),
        RiskLimits(),
    )
    assert result.decision == "rejected"
    assert set(result.reason_codes) >= {
        "consecutive_loss_limit_reached",
        "open_position_limit_reached",
        "same_direction_limit_reached",
    }


def test_risk_rejects_expired_candidate():
    result = evaluate_risk(
        candidate(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        AccountRiskState(equity=Decimal("10000")),
        RiskLimits(),
    )
    assert result.decision == "rejected"
    assert "candidate_expired" in result.reason_codes


def test_risk_uses_mathematically_capped_score_instead_of_raw_score():
    result = evaluate_risk(
        candidate(score=95, risk_score=70),
        AccountRiskState(equity=Decimal("10000")),
        RiskLimits(minimum_score=72),
    )

    assert result.decision == "rejected"
    assert "score_below_minimum" in result.reason_codes


def test_risk_sizing_includes_round_trip_execution_costs() -> None:
    result = evaluate_risk(
        candidate(
            entry=Decimal("100"),
            stop_loss=Decimal("99"),
            take_profit=Decimal("103"),
            risk_reward=Decimal("2.636363636363636363636363636"),
            estimated_round_trip_cost_pct=Decimal("0.001"),
        ),
        AccountRiskState(equity=Decimal("1000")),
        RiskLimits(
            risk_per_trade_pct=Decimal("0.01"),
            max_notional=Decimal("10000"),
            minimum_risk_reward=Decimal("2"),
        ),
    )

    assert result.decision == "approved"
    assert result.stop_distance == Decimal("1")
    assert result.effective_risk_distance == Decimal("1.100")
    assert result.approved_quantity == Decimal("9.09090909")
    assert result.estimated_cost_amount == Decimal("0.90909091")
    assert result.max_loss_amount == Decimal("10.00000000")
