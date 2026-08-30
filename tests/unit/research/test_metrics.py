from __future__ import annotations

from decimal import Decimal

import pytest

from app.research.external_benchmarks import calculate_reference_return_metrics


D = Decimal


def test_reference_return_metrics_use_explicit_net_simple_return_definitions() -> None:
    result = calculate_reference_return_metrics(
        (D("0.10"), D("-0.05"), D("0.02")),
        periods_per_year=365,
    )

    assert result.sample_size == 3
    assert result.total_return == D("0.06590")
    assert result.max_drawdown == D("0.05")
    assert abs(result.hit_rate - (D(2) / D(3))) < D("1e-27")
    assert result.profit_factor == D("2.4")
    assert result.sample_std > 0
    assert result.sharpe_ratio is not None
    assert result.reference_only is True
    assert result.promotion_eligible is False
    assert result.execution_authority is False


def test_reference_metrics_reject_binary_float_short_and_total_loss_inputs() -> None:
    with pytest.raises(ValueError, match="binary floats"):
        calculate_reference_return_metrics(
            (0.1, D("0.2")),
            periods_per_year=365,
        )
    with pytest.raises(ValueError, match="at least two"):
        calculate_reference_return_metrics((D("0.1"),), periods_per_year=365)
    with pytest.raises(ValueError, match="above -1"):
        calculate_reference_return_metrics(
            (D("-1"), D("0.1")),
            periods_per_year=365,
        )


def test_constant_positive_returns_have_no_fabricated_sharpe_or_profit_factor() -> None:
    result = calculate_reference_return_metrics(
        (D("0.01"), D("0.01")),
        periods_per_year=365,
    )

    assert result.sample_std == 0
    assert result.sharpe_ratio is None
    assert result.max_drawdown == 0
    assert result.profit_factor is None
