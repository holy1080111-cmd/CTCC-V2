from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.config.settings import Settings
from app.demo_automation.structural_risk import (
    apply_cost_adjusted_reward_risk,
    structural_cost_rate,
)
from app.domain.strategy import TradeCandidate

D = Decimal


@dataclass(frozen=True)
class DemoExecutionQuality:
    price: Decimal
    gross_risk_reward: Decimal
    net_risk_reward: Decimal
    enforced_risk_reward: Decimal
    minimum_risk_reward: Decimal
    estimated_cost_rate: Decimal


@dataclass(frozen=True)
class DemoExecutionBoundary:
    limit_price: Decimal
    quality: DemoExecutionQuality
    max_adverse_slippage_bps: Decimal


def minimum_execution_risk_reward(
    candidate: TradeCandidate,
    settings: Settings,
) -> Decimal:
    if candidate.protection_model == "structure":
        return D(str(settings.okx_demo_structural_min_net_risk_reward))
    return D(str(settings.strategy_min_risk_reward))


def execution_quality_at_price(
    candidate: TradeCandidate,
    settings: Settings,
    *,
    price: Decimal,
) -> DemoExecutionQuality | None:
    if price <= 0:
        return None
    if candidate.direction == "long":
        price_risk = price - candidate.stop_loss
        price_reward = candidate.take_profit - price
    else:
        price_risk = candidate.stop_loss - price
        price_reward = price - candidate.take_profit
    if price_risk <= 0 or price_reward <= 0:
        return None

    gross_risk_reward = price_reward / price_risk
    cost_rate = (
        structural_cost_rate(settings)
        if candidate.protection_model == "structure"
        else D("0")
    )
    risk_rate = price_risk / price
    reward_rate = price_reward / price
    if reward_rate <= cost_rate:
        return None
    net_risk_reward = (reward_rate - cost_rate) / (risk_rate + cost_rate)
    enforced_risk_reward = (
        net_risk_reward
        if candidate.protection_model == "structure"
        else gross_risk_reward
    )
    return DemoExecutionQuality(
        price=price,
        gross_risk_reward=gross_risk_reward,
        net_risk_reward=net_risk_reward,
        enforced_risk_reward=enforced_risk_reward,
        minimum_risk_reward=minimum_execution_risk_reward(candidate, settings),
        estimated_cost_rate=cost_rate,
    )


def candidate_at_execution_price(
    candidate: TradeCandidate,
    settings: Settings,
    *,
    price: Decimal,
) -> tuple[TradeCandidate | None, str | None]:
    quality = execution_quality_at_price(candidate, settings, price=price)
    if quality is None:
        return None, "execution_price_outside_protective_bounds"
    payload = candidate.model_dump()
    payload.update(
        {
            "entry": price,
            "risk_reward": quality.gross_risk_reward,
            "gross_risk_reward": None,
            "net_risk_reward": None,
            "estimated_round_trip_cost_pct": D("0"),
        }
    )
    priced = TradeCandidate.model_validate(payload)
    if candidate.protection_model == "structure":
        return apply_cost_adjusted_reward_risk(priced, settings)
    if quality.enforced_risk_reward < quality.minimum_risk_reward:
        return None, "execution_risk_reward_below_minimum"
    return priced, None


def bounded_fok_execution_price(
    candidate: TradeCandidate,
    settings: Settings,
    *,
    reference_price: Decimal,
    tick_size: Decimal,
) -> tuple[DemoExecutionBoundary | None, str | None]:
    if reference_price <= 0 or tick_size <= 0:
        return None, "execution_price_boundary_inputs_invalid"
    reference_quality = execution_quality_at_price(
        candidate,
        settings,
        price=reference_price,
    )
    if reference_quality is None:
        return None, "execution_reference_outside_protective_bounds"
    if (
        reference_quality.enforced_risk_reward
        < reference_quality.minimum_risk_reward
    ):
        return None, "execution_reference_risk_reward_below_minimum"

    minimum = reference_quality.minimum_risk_reward
    costs = reference_quality.estimated_cost_rate
    weighted_boundary = (
        candidate.take_profit + minimum * candidate.stop_loss
    ) / (D("1") + minimum)
    slippage_bps = D(
        str(settings.okx_demo_execution_max_adverse_slippage_bps)
    )
    slippage_rate = slippage_bps / D("10000")

    if candidate.direction == "long":
        risk_reward_boundary = weighted_boundary / (D("1") + costs)
        slippage_boundary = reference_price * (D("1") + slippage_rate)
        raw_limit = min(risk_reward_boundary, slippage_boundary)
        limit_price = (
            (raw_limit / tick_size).to_integral_value(rounding=ROUND_FLOOR)
            * tick_size
        )
        if limit_price < reference_price:
            return None, "bounded_fok_price_not_marketable_at_reference"
    else:
        if costs >= D("1"):
            return None, "execution_cost_rate_invalid"
        risk_reward_boundary = weighted_boundary / (D("1") - costs)
        slippage_boundary = reference_price * (D("1") - slippage_rate)
        raw_limit = max(risk_reward_boundary, slippage_boundary)
        limit_price = (
            (raw_limit / tick_size).to_integral_value(rounding=ROUND_CEILING)
            * tick_size
        )
        if limit_price > reference_price:
            return None, "bounded_fok_price_not_marketable_at_reference"

    quality = execution_quality_at_price(candidate, settings, price=limit_price)
    if quality is None:
        return None, "bounded_fok_price_outside_protective_bounds"
    if quality.enforced_risk_reward < quality.minimum_risk_reward:
        return None, "bounded_fok_risk_reward_below_minimum"
    return (
        DemoExecutionBoundary(
            limit_price=limit_price,
            quality=quality,
            max_adverse_slippage_bps=slippage_bps,
        ),
        None,
    )


def adverse_fill_slippage_bps(
    *,
    direction: str,
    reference_price: Decimal,
    fill_price: Decimal,
) -> Decimal:
    if reference_price <= 0 or fill_price <= 0:
        raise ValueError("fill slippage prices must be positive")
    if direction == "long":
        return (fill_price - reference_price) / reference_price * D("10000")
    if direction == "short":
        return (reference_price - fill_price) / reference_price * D("10000")
    raise ValueError("fill slippage direction must be long or short")
