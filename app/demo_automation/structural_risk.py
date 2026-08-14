from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from app.config.settings import Settings
from app.domain.demo_automation import DemoAutomationRiskTier
from app.domain.strategy import TradeCandidate

D = Decimal
LEVERAGE_LADDER = (3, 5, 8, 10, 20)


@dataclass(frozen=True)
class StructuralLeverageSelection:
    selected_leverage: int
    required_leverage: int
    leverage_cap: int
    twenty_x_eligible: bool
    cap_reasons: tuple[str, ...]


def structural_cost_rate(settings: Settings) -> Decimal:
    total_bps = (
        D(str(settings.okx_demo_structural_round_trip_fee_bps))
        + D(str(settings.okx_demo_structural_round_trip_slippage_bps))
        + D(str(settings.okx_demo_structural_funding_buffer_bps))
    )
    return total_bps / D("10000")


def candidate_with_structural_prices(
    candidate: TradeCandidate,
    *,
    reference_price: Decimal,
) -> TradeCandidate | None:
    geometry = candidate.structural_protection
    if geometry is None:
        return None
    if candidate.direction == "long":
        valid = geometry.stop_loss < reference_price < geometry.take_profit
    else:
        valid = geometry.take_profit < reference_price < geometry.stop_loss
    if not valid:
        return None
    payload = candidate.model_dump()
    payload.update(
        {
            "entry": reference_price,
            "stop_loss": geometry.stop_loss,
            "take_profit": geometry.take_profit,
            "protection_model": "structure",
            "gross_risk_reward": None,
            "net_risk_reward": None,
            "estimated_round_trip_cost_pct": D("0"),
        }
    )
    if candidate.direction == "long":
        payload["risk_reward"] = (
            geometry.take_profit - reference_price
        ) / (reference_price - geometry.stop_loss)
    else:
        payload["risk_reward"] = (
            reference_price - geometry.take_profit
        ) / (geometry.stop_loss - reference_price)
    return TradeCandidate.model_validate(payload)


def apply_cost_adjusted_reward_risk(
    candidate: TradeCandidate,
    settings: Settings,
) -> tuple[TradeCandidate | None, str | None]:
    if candidate.protection_model != "structure":
        return None, "structural_protection_geometry_unavailable"
    entry = candidate.entry
    price_risk = abs(entry - candidate.stop_loss) / entry
    gross_reward = abs(candidate.take_profit - entry) / entry
    costs = structural_cost_rate(settings)
    if price_risk <= 0:
        return None, "structural_stop_distance_invalid"
    if gross_reward <= costs:
        return None, "net_reward_nonpositive_after_costs"
    gross_rr = gross_reward / price_risk
    net_rr = (gross_reward - costs) / (price_risk + costs)
    if net_rr < D(str(settings.okx_demo_structural_min_net_risk_reward)):
        return None, "net_risk_reward_below_minimum"
    payload = candidate.model_dump()
    payload.update(
        {
            "risk_reward": net_rr,
            "gross_risk_reward": gross_rr,
            "net_risk_reward": net_rr,
            "estimated_round_trip_cost_pct": costs,
        }
    )
    return TradeCandidate.model_validate(payload), None


def _twenty_x_reasons(candidate: TradeCandidate, settings: Settings) -> list[str]:
    reasons: list[str] = []
    effective_score = candidate.risk_score or candidate.score
    if effective_score < settings.okx_demo_structural_score_extreme_min:
        reasons.append("effective_score_below_20x_threshold")
    math = candidate.mathematical_confirmation
    if math is None or math.status != "confirmed" or math.risk_grade != "high":
        reasons.append("mathematical_grade_below_20x_threshold")
    else:
        if math.confidence < settings.okx_demo_structural_20x_min_confidence:
            reasons.append("mathematical_confidence_below_20x_threshold")
        if math.reliability < settings.okx_demo_structural_20x_min_reliability:
            reasons.append("mathematical_reliability_below_20x_threshold")
        if math.instability > settings.okx_demo_structural_20x_max_instability:
            reasons.append("mathematical_instability_above_20x_threshold")
    derivative = candidate.derivative_confirmation
    if (
        derivative is None
        or derivative.status != "confirmed"
        or derivative.confidence < settings.okx_demo_structural_20x_min_confidence
    ):
        reasons.append("derivative_confirmation_below_20x_threshold")
    if candidate.protection_model != "structure":
        reasons.append("structural_protection_required_for_20x")
    if (
        candidate.net_risk_reward is None
        or candidate.net_risk_reward
        < D(str(settings.okx_demo_structural_min_net_risk_reward))
    ):
        reasons.append("net_risk_reward_below_20x_threshold")
    return reasons


def select_structural_leverage(
    candidate: TradeCandidate,
    tier: DemoAutomationRiskTier,
    settings: Settings,
    *,
    account_equity: Decimal,
    position_margin_cap: Decimal,
) -> StructuralLeverageSelection:
    if account_equity <= 0:
        raise ValueError("structural_account_equity_must_be_positive")
    if position_margin_cap <= 0:
        raise ValueError("structural_position_margin_cap_must_be_positive")
    total_risk_rate = (
        abs(candidate.entry - candidate.stop_loss) / candidate.entry
        + candidate.estimated_round_trip_cost_pct
    )
    if total_risk_rate <= 0:
        raise ValueError("structural_total_risk_rate_must_be_positive")
    # ``tier.risk_pct`` is an account-equity risk budget, whereas leverage is
    # applied to the margin available to this one position.  Those quantities
    # are equal only while the account is no larger than its capital bucket.
    # Above the 2,000-USDT bucket boundary, omitting this ratio understates the
    # leverage required to deploy the requested risk budget.
    risk_budget_amount = account_equity * D(str(tier.risk_pct))
    required = int(
        (
            risk_budget_amount
            / (position_margin_cap * total_risk_rate)
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    twenty_x_reasons = _twenty_x_reasons(candidate, settings)
    effective_cap = tier.leverage
    cap_reasons: list[str] = []
    if tier.leverage >= 20 and twenty_x_reasons:
        effective_cap = min(10, tier.leverage)
        cap_reasons.extend(twenty_x_reasons)

    allowed = [value for value in LEVERAGE_LADDER if value <= effective_cap]
    if not allowed:
        allowed = [effective_cap]
    selected = next((value for value in allowed if value >= required), allowed[-1])

    if required > 20:
        cap_reasons.append("required_leverage_exceeds_20x_safety_cap")
    elif required > effective_cap:
        cap_reasons.append(
            "required_leverage_exceeds_approved_leverage_cap"
            if effective_cap < tier.leverage
            else "required_leverage_exceeds_score_tier_cap"
        )
    return StructuralLeverageSelection(
        selected_leverage=selected,
        required_leverage=max(1, required),
        leverage_cap=effective_cap,
        twenty_x_eligible=not twenty_x_reasons,
        cap_reasons=tuple(dict.fromkeys(cap_reasons)),
    )
