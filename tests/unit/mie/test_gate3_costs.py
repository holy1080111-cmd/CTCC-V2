from __future__ import annotations

from decimal import Decimal

import pytest

from app.mie.validation import CostModel
from app.mie.validation.costs import evaluate_costed_return_path

D = Decimal


def declared_cost_model(
    *,
    funding_interval_seconds: int = 900,
    **overrides: Decimal,
) -> CostModel:
    values = {
        "fee_bps": D("1"),
        "funding_bps": D("0.5"),
        "spread_bps": D("1"),
        "slippage_bps": D("1"),
    }
    values.update(overrides)
    return CostModel(
        model_id="cost:model:test",
        version="v1",
        funding_interval_seconds=funding_interval_seconds,
        **values,
    )


def test_costed_return_path_reports_every_declared_cost_and_risk_metric() -> None:
    result = evaluate_costed_return_path(
        (D("0.01"), D("-0.02"), D("0.03")),
        (D("1"), D("1"), D("0")),
        cost_model=declared_cost_model(),
        observation_interval_seconds=900,
        cvar_confidence_level=D("0.67"),
    )

    assert result.sample_count == 3
    assert result.turnover == D("2")
    assert result.fee_cost == D("0.0002")
    assert result.funding_cost == D("0.00010")
    assert result.spread_cost == D("0.0002")
    assert result.slippage_cost == D("0.0002")
    assert result.total_cost == D("0.00070")
    assert result.observations[0].net_return == D("0.00965")
    assert result.observations[1].net_return == D("-0.02005")
    assert result.observations[2].net_return == D("-0.0003")
    assert result.gross_compound_return == (
        (D("1.01") * D("0.98") * D("1")) - D("1")
    )
    assert result.net_compound_return == (
        D("1.00965") * D("0.97995") * D("0.9997") - D("1")
    )
    assert D("0") < result.maximum_drawdown < D("1")
    assert result.cvar_loss == D("0.02005")
    assert result.authority == "offline_descriptive_only"
    assert result.runtime_consumers == 0
    assert result.execution_authority is False


def test_cost_calculation_is_deterministic_and_cost_free_stays_descriptive() -> None:
    parameters = {
        "asset_returns": (D("0.01"), D("0.02")),
        "exposures": (D("0.5"), D("-0.5")),
        "cost_model": declared_cost_model(
            fee_bps=D("0"),
            funding_bps=D("0"),
            spread_bps=D("0"),
            slippage_bps=D("0"),
        ),
        "observation_interval_seconds": 900,
    }

    first = evaluate_costed_return_path(**parameters)
    second = evaluate_costed_return_path(**parameters)

    assert first == second
    assert first.total_cost == 0
    assert first.authority == "offline_descriptive_only"
    assert parameters["cost_model"].cost_free_results_descriptive_only is True


@pytest.mark.parametrize(
    ("returns", "exposures", "message"),
    [
        ((), (), "cannot be empty"),
        ((D("0.1"),), (), "equal length"),
        ((D("-1"),), (D("1"),), "above negative one"),
        ((D("0.1"),), (D("1.01"),), "between negative one and one"),
        ((D("NaN"),), (D("0"),), "finite"),
    ],
)
def test_costed_return_path_fails_closed_on_invalid_inputs(
    returns: tuple[Decimal, ...],
    exposures: tuple[Decimal, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate_costed_return_path(
            returns,
            exposures,
            cost_model=declared_cost_model(),
            observation_interval_seconds=900,
        )


def test_costed_return_path_rejects_invalid_cvar_and_equity_exhaustion() -> None:
    with pytest.raises(ValueError, match="CVaR"):
        evaluate_costed_return_path(
            (D("0.1"),),
            (D("1"),),
            cost_model=declared_cost_model(),
            observation_interval_seconds=900,
            cvar_confidence_level=D("1"),
        )

    with pytest.raises(ValueError, match="exhaust equity"):
        evaluate_costed_return_path(
            (D("-0.9999"),),
            (D("1"),),
            cost_model=declared_cost_model(
                fee_bps=D("10"),
                funding_bps=D("10"),
                spread_bps=D("10"),
                slippage_bps=D("10"),
            ),
            observation_interval_seconds=900,
        )


def test_terminal_flatten_and_funding_cadence_are_applied() -> None:
    result = evaluate_costed_return_path(
        (D("0"), D("0")),
        (D("1"), D("1")),
        cost_model=declared_cost_model(funding_interval_seconds=1_800),
        observation_interval_seconds=900,
    )

    assert result.turnover == D("2")
    assert result.observations[0].turnover == D("1")
    assert result.observations[1].turnover == D("1")
    assert result.funding_cost == D("0.00005")
    assert result.fee_cost == D("0.0002")

    with pytest.raises(ValueError, match="must divide"):
        evaluate_costed_return_path(
            (D("0"),),
            (D("0"),),
            cost_model=declared_cost_model(funding_interval_seconds=1_000),
            observation_interval_seconds=900,
        )
