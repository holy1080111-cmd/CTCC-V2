from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.strategy import TradeCandidate


def test_long_candidate_geometry() -> None:
    item = TradeCandidate(
        strategy="test",
        direction="long",
        score=80,
        entry=Decimal("100"),
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        risk_reward=Decimal("2"),
        invalidation="stop",
        expires_at=datetime.now(timezone.utc),
    )
    assert item.direction == "long"


def test_invalid_long_geometry_rejected() -> None:
    with pytest.raises(ValueError):
        TradeCandidate(
            strategy="test",
            direction="long",
            score=80,
            entry=Decimal("100"),
            stop_loss=Decimal("101"),
            take_profit=Decimal("104"),
            risk_reward=Decimal("2"),
            invalidation="stop",
            expires_at=datetime.now(timezone.utc),
        )
