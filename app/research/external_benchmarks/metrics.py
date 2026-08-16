from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from app.research.external_benchmarks.contracts import ReferenceMetricBundle


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("reference returns must not use binary floats")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("reference return is not decimal-compatible") from exc
    if not result.is_finite() or result <= Decimal("-1"):
        raise ValueError("net simple returns must be finite and above -1")
    return result


def calculate_reference_return_metrics(
    net_returns: Sequence[Decimal | int | str],
    *,
    periods_per_year: int,
    risk_free_return_per_period: Decimal | int | str = Decimal("0"),
) -> ReferenceMetricBundle:
    """Deterministic formula-parity metrics for already netted simple returns.

    The result is not an alpha, validation, sizing, leverage, or promotion claim.
    """

    if periods_per_year < 1:
        raise ValueError("periods_per_year must be positive")
    returns = tuple(_decimal(value) for value in net_returns)
    if len(returns) < 2:
        raise ValueError("at least two return observations are required")
    risk_free = _decimal(risk_free_return_per_period)

    with localcontext() as context:
        context.prec = 50
        sample_size = Decimal(len(returns))
        mean = sum(returns, Decimal("0")) / sample_size
        variance = sum(
            ((value - mean) ** 2 for value in returns),
            Decimal("0"),
        ) / Decimal(len(returns) - 1)
        sample_std = variance.sqrt()
        annualizer = Decimal(periods_per_year).sqrt()
        annualized_volatility = sample_std * annualizer
        sharpe = (
            None
            if sample_std == 0
            else ((mean - risk_free) / sample_std) * annualizer
        )

        equity = Decimal("1")
        peak = equity
        max_drawdown = Decimal("0")
        for value in returns:
            equity *= Decimal("1") + value
            peak = max(peak, equity)
            drawdown = Decimal("1") - (equity / peak)
            max_drawdown = max(max_drawdown, drawdown)

        positive_sum = sum(
            (value for value in returns if value > 0),
            Decimal("0"),
        )
        negative_sum = -sum(
            (value for value in returns if value < 0),
            Decimal("0"),
        )
        profit_factor = (
            None if negative_sum == 0 else positive_sum / negative_sum
        )
        hit_rate = Decimal(sum(value > 0 for value in returns)) / sample_size

        return ReferenceMetricBundle(
            sample_size=len(returns),
            periods_per_year=periods_per_year,
            total_return=equity - Decimal("1"),
            mean_return=mean,
            sample_std=sample_std,
            annualized_volatility=annualized_volatility,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            hit_rate=hit_rate,
            profit_factor=profit_factor,
        )
