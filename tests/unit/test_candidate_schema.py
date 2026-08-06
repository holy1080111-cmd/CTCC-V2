from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.enums import Side
from app.domain.schemas import TradeCandidateInput


def test_long_candidate_geometry_and_rr() -> None:
    candidate = TradeCandidateInput(
        symbol="BTC/USDT:USDT",
        side=Side.LONG,
        strategy_name="trend_pullback",
        score=80,
        entry_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
    )
    assert candidate.risk_reward == Decimal("2")


def test_short_candidate_geometry() -> None:
    candidate = TradeCandidateInput(
        symbol="BTC/USDT:USDT",
        side=Side.SHORT,
        strategy_name="breakout",
        score=75,
        entry_price=Decimal("100"),
        stop_loss=Decimal("105"),
        take_profit=Decimal("90"),
    )
    assert candidate.risk_reward == Decimal("2")


def test_invalid_long_geometry_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TradeCandidateInput(
            symbol="BTC/USDT:USDT",
            side=Side.LONG,
            strategy_name="invalid",
            score=70,
            entry_price=Decimal("100"),
            stop_loss=Decimal("105"),
            take_profit=Decimal("110"),
        )
