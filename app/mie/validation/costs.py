"""Declared offline cost and return-path calculations for MIE Gate 3.

The module evaluates normalized shadow exposures in ``[-1, 1]``. It has no
exchange, account, order, quantity, leverage, risk-budget, or runtime hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, localcontext
from typing import Literal, Sequence

from app.mie.validation.contracts import CostModel

D = Decimal
BASIS_POINTS = D("10000")
DECIMAL_PRECISION = 50


@dataclass(frozen=True, slots=True)
class CostedObservation:
    index: int
    asset_return: Decimal
    exposure: Decimal
    turnover: Decimal
    gross_return: Decimal
    fee_cost: Decimal
    funding_cost: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    net_return: Decimal


@dataclass(frozen=True, slots=True)
class ReturnPathMetrics:
    sample_count: int
    gross_compound_return: Decimal
    net_compound_return: Decimal
    turnover: Decimal
    maximum_drawdown: Decimal
    cvar_loss: Decimal
    fee_cost: Decimal
    funding_cost: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    total_cost: Decimal
    cvar_confidence_level: Decimal
    observations: tuple[CostedObservation, ...]
    authority: Literal["offline_descriptive_only"] = "offline_descriptive_only"
    runtime_consumers: Literal[0] = 0
    execution_authority: Literal[False] = False


def _finite_decimal(value: Decimal | int, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError(f"{name} must be a Decimal or integer")
    result = D(value)
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _compound(returns: Sequence[Decimal]) -> Decimal:
    equity = D("1")
    for value in returns:
        if value <= D("-1"):
            raise ValueError("period returns must remain above negative one")
        equity *= D("1") + value
    return equity - D("1")


def _maximum_drawdown(returns: Sequence[Decimal]) -> Decimal:
    equity = D("1")
    peak = equity
    maximum = D("0")
    for value in returns:
        if value <= D("-1"):
            raise ValueError("period returns must remain above negative one")
        equity *= D("1") + value
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _cvar_loss(
    returns: Sequence[Decimal], confidence_level: Decimal
) -> Decimal:
    tail_probability = D("1") - confidence_level
    tail_count = int(
        (D(len(returns)) * tail_probability).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    tail_count = max(1, tail_count)
    losses = sorted((-value for value in returns), reverse=True)
    return sum(losses[:tail_count], D("0")) / D(tail_count)


def evaluate_costed_return_path(
    asset_returns: Sequence[Decimal | int],
    exposures: Sequence[Decimal | int],
    *,
    cost_model: CostModel,
    observation_interval_seconds: int,
    cvar_confidence_level: Decimal = D("0.95"),
) -> ReturnPathMetrics:
    """Apply the frozen cost model to a normalized offline exposure path.

    Fee, spread, and slippage costs accrue on absolute exposure turnover.
    Funding is prorated from the frozen funding interval to the observation
    interval. The final observation includes mandatory flattening turnover.
    """

    returns = tuple(
        _finite_decimal(value, "asset return") for value in asset_returns
    )
    shadow_exposures = tuple(
        _finite_decimal(value, "shadow exposure") for value in exposures
    )
    if not returns:
        raise ValueError("return path cannot be empty")
    if len(returns) != len(shadow_exposures):
        raise ValueError("returns and exposures must have equal length")
    if any(value <= D("-1") for value in returns):
        raise ValueError("asset returns must remain above negative one")
    if any(abs(value) > D("1") for value in shadow_exposures):
        raise ValueError("shadow exposures must lie between negative one and one")
    if (
        isinstance(observation_interval_seconds, bool)
        or not isinstance(observation_interval_seconds, int)
        or observation_interval_seconds < 1
    ):
        raise ValueError("observation interval must be a positive integer")
    if cost_model.funding_interval_seconds % observation_interval_seconds:
        raise ValueError(
            "observation interval must divide the frozen funding interval"
        )

    confidence = _finite_decimal(cvar_confidence_level, "CVaR confidence level")
    if confidence <= D("0") or confidence >= D("1"):
        raise ValueError("CVaR confidence level must lie between zero and one")

    observations: list[CostedObservation] = []
    previous_exposure = D("0")
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        for index, (asset_return, exposure) in enumerate(
            zip(returns, shadow_exposures, strict=True)
        ):
            turnover = abs(exposure - previous_exposure)
            if index == len(returns) - 1 and cost_model.flatten_at_end:
                turnover += abs(exposure)
            gross_return = exposure * asset_return
            fee_cost = turnover * cost_model.fee_bps / BASIS_POINTS
            funding_cost = (
                abs(exposure)
                * cost_model.funding_bps
                / BASIS_POINTS
                * D(observation_interval_seconds)
                / D(cost_model.funding_interval_seconds)
            )
            spread_cost = turnover * cost_model.spread_bps / BASIS_POINTS
            slippage_cost = (
                turnover * cost_model.slippage_bps / BASIS_POINTS
            )
            net_return = gross_return - (
                fee_cost + funding_cost + spread_cost + slippage_cost
            )
            if net_return <= D("-1"):
                raise ValueError("costed return path would exhaust equity")
            observations.append(
                CostedObservation(
                    index=index,
                    asset_return=asset_return,
                    exposure=exposure,
                    turnover=turnover,
                    gross_return=gross_return,
                    fee_cost=fee_cost,
                    funding_cost=funding_cost,
                    spread_cost=spread_cost,
                    slippage_cost=slippage_cost,
                    net_return=net_return,
                )
            )
            previous_exposure = exposure

        gross_returns = tuple(item.gross_return for item in observations)
        net_returns = tuple(item.net_return for item in observations)
        fee_cost = sum((item.fee_cost for item in observations), D("0"))
        funding_cost = sum(
            (item.funding_cost for item in observations), D("0")
        )
        spread_cost = sum((item.spread_cost for item in observations), D("0"))
        slippage_cost = sum(
            (item.slippage_cost for item in observations), D("0")
        )
        total_cost = fee_cost + funding_cost + spread_cost + slippage_cost
        return ReturnPathMetrics(
            sample_count=len(observations),
            gross_compound_return=_compound(gross_returns),
            net_compound_return=_compound(net_returns),
            turnover=sum((item.turnover for item in observations), D("0")),
            maximum_drawdown=_maximum_drawdown(net_returns),
            cvar_loss=_cvar_loss(net_returns, confidence),
            fee_cost=fee_cost,
            funding_cost=funding_cost,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            total_cost=total_cost,
            cvar_confidence_level=confidence,
            observations=tuple(observations),
        )
