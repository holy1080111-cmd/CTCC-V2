from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.demo_automation.risk_profile import (
    configured_score_risk_tiers,
    score_risk_tier,
)
from app.demo_automation.structural_risk import (
    apply_cost_adjusted_reward_risk,
    candidate_with_structural_prices,
    select_structural_leverage,
)
from app.domain.strategy import (
    DerivativeConfirmation,
    MathematicalConfirmation,
    StructuralProtectionGeometry,
    TradeCandidate,
)

D = Decimal


def structural_settings(**updates) -> Settings:
    values = {
        "okx_demo_score_risk_enabled": True,
        "okx_demo_capital_bucket_enabled": True,
        "okx_demo_continuous_session_enabled": True,
        "okx_demo_trade_cooldown_seconds": 0,
        "okx_demo_structural_dynamic_leverage_enabled": True,
        "okx_demo_max_open_positions": 3,
        "okx_demo_max_leverage": 20,
        "okx_demo_portfolio_max_risk_pct": D("0.10"),
        "max_weekly_loss_pct": 0.10,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def structural_candidate(*, high_math: bool = True) -> TradeCandidate:
    geometry = StructuralProtectionGeometry(
        timeframe="15m",
        source_closed_at=datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc),
        reference_entry=D("100"),
        stop_anchor=D("99.95"),
        target_anchor=D("101"),
        volatility_buffer=D("0.05"),
        stop_loss=D("99.90"),
        take_profit=D("101"),
        gross_risk_reward=D("10"),
    )
    return TradeCandidate(
        strategy="trend_pullback",
        direction="long",
        score=99,
        risk_score=99,
        entry=D("100"),
        stop_loss=D("95"),
        take_profit=D("110"),
        risk_reward=D("2"),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        structural_protection=geometry,
        derivative_confirmation=DerivativeConfirmation(
            status="confirmed",
            confidence=D("0.9"),
            alignment_score=D("0.9"),
        ),
        mathematical_confirmation=MathematicalConfirmation(
            status="confirmed",
            risk_grade="high" if high_math else "medium",
            confidence=D("0.9"),
            directional_support=D("0.9"),
            reliability=D("0.9"),
            coverage=D("0.9"),
            consensus=D("0.9"),
            instability=D("0.1"),
            component_codes=["derivative", "state"],
        ),
    )


def finalized_candidate(*, high_math: bool = True) -> TradeCandidate:
    settings = structural_settings()
    candidate = candidate_with_structural_prices(
        structural_candidate(high_math=high_math),
        reference_price=D("100"),
    )
    assert candidate is not None
    finalized, blocker = apply_cost_adjusted_reward_risk(candidate, settings)
    assert blocker is None
    assert finalized is not None
    return finalized


def finalized_candidate_for_rate(
    *, score: int, stop_rate: Decimal
) -> TradeCandidate:
    base = finalized_candidate()
    entry = D("100")
    costs = D("0.0016")
    stop_distance = entry * stop_rate
    stop_loss = entry - stop_distance
    stop_anchor = entry - stop_distance / D("2")
    reward_rate = D("3") * (stop_rate + costs)
    take_profit = entry * (D("1") + reward_rate)
    gross_rr = reward_rate / stop_rate
    net_rr = (reward_rate - costs) / (stop_rate + costs)
    payload = base.model_dump()
    payload.update(
        {
            "score": score,
            "risk_score": score,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_reward": net_rr,
            "gross_risk_reward": gross_rr,
            "net_risk_reward": net_rr,
            "estimated_round_trip_cost_pct": costs,
            "structural_protection": StructuralProtectionGeometry(
                timeframe="15m",
                source_closed_at=datetime(
                    2026, 8, 12, 8, 0, tzinfo=timezone.utc
                ),
                reference_entry=entry,
                stop_anchor=stop_anchor,
                target_anchor=take_profit,
                volatility_buffer=stop_distance / D("2"),
                stop_loss=stop_loss,
                take_profit=take_profit,
                gross_risk_reward=gross_rr,
            ),
        }
    )
    return TradeCandidate.model_validate(payload)


def test_structural_tiers_form_the_agreed_five_band_matrix() -> None:
    tiers = configured_score_risk_tiers(structural_settings())

    assert [item.name for item in tiers] == [
        "low",
        "medium",
        "high",
        "elite",
        "extreme",
    ]
    assert [item.risk_pct for item in tiers] == [
        D("0.015"),
        D("0.025"),
        D("0.03"),
        D("0.04"),
        D("0.06"),
    ]
    assert [item.leverage for item in tiers] == [3, 5, 8, 10, 20]


def test_cost_adjusted_rr_deducts_fee_slippage_and_funding() -> None:
    candidate = finalized_candidate()

    assert candidate.protection_model == "structure"
    assert candidate.estimated_round_trip_cost_pct == D("0.0016")
    assert candidate.gross_risk_reward == D("10")
    assert candidate.net_risk_reward == D("0.0084") / D("0.0026")
    assert candidate.risk_reward == candidate.net_risk_reward


def test_high_quality_extreme_geometry_can_select_20x() -> None:
    settings = structural_settings()
    candidate = finalized_candidate()
    selection = select_structural_leverage(
        candidate,
        score_risk_tier(99, settings),
        settings,
    )

    assert selection.required_leverage == 24
    assert selection.selected_leverage == 20
    assert selection.twenty_x_eligible is True
    assert selection.cap_reasons == ()


def test_20x_is_capped_to_10x_when_mathematics_is_not_high_grade() -> None:
    settings = structural_settings()
    candidate = finalized_candidate(high_math=False)
    selection = select_structural_leverage(
        candidate,
        score_risk_tier(99, settings),
        settings,
    )

    assert selection.selected_leverage == 10
    assert selection.twenty_x_eligible is False
    assert "mathematical_grade_below_20x_threshold" in selection.cap_reasons


def test_net_reward_below_configured_minimum_fails_closed() -> None:
    settings = structural_settings(okx_demo_structural_min_net_risk_reward=D("4"))
    candidate = candidate_with_structural_prices(
        structural_candidate(), reference_price=D("100")
    )
    assert candidate is not None

    finalized, blocker = apply_cost_adjusted_reward_risk(candidate, settings)

    assert finalized is None
    assert blocker == "net_risk_reward_below_minimum"


def test_leverage_matrix_preserves_risk_and_ladder_invariants() -> None:
    settings = structural_settings()

    for score in (75, 85, 92, 96, 99):
        tier = score_risk_tier(score, settings)
        allowed = [value for value in (3, 5, 8, 10, 20) if value <= tier.leverage]
        for stop_rate in map(
            D,
            ("0.0005", "0.001", "0.002", "0.004", "0.01", "0.03"),
        ):
            candidate = finalized_candidate_for_rate(
                score=score,
                stop_rate=stop_rate,
            )
            selection = select_structural_leverage(candidate, tier, settings)
            total_risk_rate = (
                stop_rate + candidate.estimated_round_trip_cost_pct
            )
            required = selection.required_leverage

            assert selection.selected_leverage in allowed
            assert selection.selected_leverage <= tier.leverage
            if required <= tier.leverage:
                assert selection.selected_leverage == min(
                    value for value in allowed if value >= required
                )
                required_margin_fraction = (
                    tier.risk_pct
                    / total_risk_rate
                    / D(selection.selected_leverage)
                )
                assert D("0") < required_margin_fraction <= D("1")
            else:
                assert selection.selected_leverage == tier.leverage
                assert (
                    D(selection.selected_leverage) * total_risk_rate
                    < tier.risk_pct
                )

            if selection.selected_leverage == 20:
                assert score >= 98
                assert selection.twenty_x_eligible is True
                # 20x is selected only when 10x cannot fund the requested
                # risk. The modeled stop-plus-cost distance is consequently
                # below 0.6%, far inside 20x nominal initial margin. This is
                # not a liquidation guarantee under gaps or maintenance fees.
                assert total_risk_rate < D("0.006")


def test_candidate_cannot_move_stop_inside_structural_anchor() -> None:
    candidate = finalized_candidate()
    payload = candidate.model_dump()
    payload["stop_loss"] = D("99.96")

    with pytest.raises(ValueError, match="long structural anchors"):
        TradeCandidate.model_validate(payload)
