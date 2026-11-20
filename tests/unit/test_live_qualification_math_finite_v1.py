"""Declared synthetic Live market/risk composition, never a trading owner."""

from datetime import datetime, timedelta

import pytest
from pydantic import BaseModel

from app.okx_live import finite_portfolio_v1 as risk
from app.okx_live import qualification_math_v1 as subject
from app.trade_qualification.engine import PreEvidencePolicy
from tests.unit import test_live_finite_portfolio_v1 as financial_fixture
from tests.unit.test_live_qualification_math_v1 import (
    initial,
)
from tests.unit.test_live_qualification_math_v1 import (
    live_source as live_source,  # noqa: PLC0414 -- original synthetic transport fixture
)
from tests.unit.test_live_qualification_math_v1 import (
    market as market,  # noqa: PLC0414 -- original declared raw market fixture
)


def _shift_declared_fixture(value, delta):
    """Declare a coherent synthetic clock BEFORE any calculation, never IO."""
    if isinstance(value, BaseModel):
        declared = {
            name: _shift_declared_fixture(getattr(value, name), delta)
            for name in type(value).model_fields
        }
        if type(value) is risk.LiveFinancialOperandsV1:
            # UTC midnight is a calendar boundary, not an elapsed-duration
            # offset. Re-declare that boundary for this synthetic clock.
            declared["daily_window_started_at"] = declared["evaluated_at"].replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return type(value).model_validate(declared, strict=True)
    if type(value) is tuple:
        return tuple(_shift_declared_fixture(item, delta) for item in value)
    if type(value) is datetime:
        return value + delta
    return value


def _declared_finite_options(source, options, *, financial_changes=None):
    now = source.evaluated_at
    declared = _shift_declared_fixture(
        financial_fixture.inputs(), now - financial_fixture.f.NOW
    )
    portfolio = options["policy"].portfolio.model_copy(
        update={
            "max_age_seconds": 5,
            "drawdown_window_started_at": now - timedelta(days=1),
        }
    )
    portfolio = type(portfolio).model_validate(
        portfolio.model_dump(mode="python"), strict=True
    )
    policy = PreEvidencePolicy(
        **(options["policy"].model_dump(mode="python") | {"portfolio": portfolio})
    )
    financial = declared.claims.financial
    if financial_changes:
        financial = risk.LiveFinancialOperandsV1(
            **(financial.model_dump(mode="python") | financial_changes)
        )
    claims = risk.LiveFinitePortfolioClaimsV1(
        **(
            declared.claims.model_dump(mode="python")
            | {"risk_policy": portfolio, "financial": financial}
        )
    )
    inputs = risk.LiveFinitePortfolioInputsV1(
        **(declared.model_dump(mode="python") | {"claims": claims})
    )
    return options | {"policy": policy, "risk_inputs": inputs}


@pytest.mark.asyncio
async def test_live_raw_market_and_finite_financial_data_compose_g1_g11(
    live_source, market
):
    _, source, options, _ = await initial(live_source, market)
    args = _declared_finite_options(source, options)
    result = subject.evaluate_live_qualification_math_v1(source, **args)
    assert result.pre_evidence_complete, [
        (g.gate.value, g.code) for g in result.result.gates
    ]
    assert (
        result.result.gates[-1].measured_values["account_evidence_authenticated"]
        is False
    )
    assert result.execution_authority is result.account_complete is False
    assert result.atomic_risk_reserved is result.source_authenticity_verified is False
    assert subject.verify_live_qualification_math_v1(result) == result
    assert result.risk_inputs.claims.account.original_start_seed is None


@pytest.mark.asyncio
async def test_finite_unknown_cashflow_stays_g11_denied(live_source, market):
    _, source, options, _ = await initial(live_source, market)
    args = _declared_finite_options(
        source, options, financial_changes={"weekly_net_pnl": None}
    )
    result = subject.evaluate_live_qualification_math_v1(source, **args)
    assert result.market_math_complete and not result.pre_evidence_complete
    assert result.result.gates[-1].code == "risk_authority_denied"
    assert (
        result.result.gates[-1].measured_values["account_evidence_authenticated"]
        is False
    )


@pytest.mark.asyncio
async def test_finite_financial_policy_cannot_silently_change_math_policy(
    live_source, market
):
    _, source, options, _ = await initial(live_source, market)
    args = _declared_finite_options(source, options)
    args["policy"] = options["policy"]
    with pytest.raises(subject.LiveQualificationMathError):
        subject.evaluate_live_qualification_math_v1(source, **args)
