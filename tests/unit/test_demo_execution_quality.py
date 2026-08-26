from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import Settings
from app.demo_automation.execution_quality import (
    adverse_fill_slippage_bps,
    bounded_fok_execution_price,
    candidate_at_execution_price,
    execution_quality_at_price,
)
from app.domain.strategy import StructuralProtectionGeometry, TradeCandidate

D = Decimal


def settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "strategy_min_risk_reward": 1.8,
        "okx_demo_execution_max_adverse_slippage_bps": D("5"),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def candidate(
    *,
    direction: str = "long",
    entry: str = "100",
    stop_loss: str = "95",
    take_profit: str = "110",
) -> TradeCandidate:
    entry_price = D(entry)
    stop = D(stop_loss)
    take = D(take_profit)
    risk = (
        entry_price - stop
        if direction == "long"
        else stop - entry_price
    )
    reward = (
        take - entry_price
        if direction == "long"
        else entry_price - take
    )
    return TradeCandidate(
        strategy="unit_test",
        direction=direction,
        score=90,
        entry=entry_price,
        stop_loss=stop,
        take_profit=take,
        risk_reward=reward / risk,
        invalidation="stop",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def structural_candidate() -> TradeCandidate:
    now = datetime.now(timezone.utc)
    geometry = StructuralProtectionGeometry(
        timeframe="15m",
        source_closed_at=now - timedelta(minutes=1),
        reference_entry=D("100"),
        stop_anchor=D("99.95"),
        target_anchor=D("101"),
        volatility_buffer=D("0.05"),
        stop_loss=D("99.9"),
        take_profit=D("101"),
        gross_risk_reward=D("10"),
    )
    return TradeCandidate(
        strategy="structural_test",
        direction="long",
        score=99,
        entry=D("100"),
        stop_loss=D("99.9"),
        take_profit=D("101"),
        risk_reward=D("10"),
        invalidation="structure",
        expires_at=now + timedelta(minutes=5),
        protection_model="structure",
        structural_protection=geometry,
    )


def test_eth_incident_fill_would_be_rejected_by_rr_price_bound() -> None:
    value = candidate(
        entry="2490",
        stop_loss="2466.3",
        take_profit="2535.43",
    )
    config = settings()

    boundary, blocker = bounded_fok_execution_price(
        value,
        config,
        reference_price=D("2490"),
        tick_size=D("0.01"),
    )
    incident = execution_quality_at_price(
        value,
        config,
        price=D("2491.39"),
    )

    assert blocker is None
    assert boundary is not None
    assert boundary.limit_price == D("2490.98")
    assert boundary.quality.enforced_risk_reward >= D("1.8")
    assert incident is not None
    assert incident.enforced_risk_reward < D("1.8")
    assert D("2491.39") > boundary.limit_price
    assert adverse_fill_slippage_bps(
        direction="long",
        reference_price=D("2490"),
        fill_price=D("2491.39"),
    ) > D("5")


def test_adverse_slippage_cap_can_be_stricter_than_rr_boundary() -> None:
    value = candidate()

    boundary, blocker = bounded_fok_execution_price(
        value,
        settings(),
        reference_price=D("100"),
        tick_size=D("0.01"),
    )

    assert blocker is None
    assert boundary is not None
    assert boundary.limit_price == D("100.05")
    assert boundary.quality.enforced_risk_reward > D("1.8")


def test_short_fok_floor_caps_adverse_lower_fill() -> None:
    value = candidate(
        direction="short",
        stop_loss="105",
        take_profit="90",
    )

    boundary, blocker = bounded_fok_execution_price(
        value,
        settings(),
        reference_price=D("100"),
        tick_size=D("0.01"),
    )

    assert blocker is None
    assert boundary is not None
    assert boundary.limit_price == D("99.95")
    assert boundary.quality.enforced_risk_reward > D("1.8")


def test_structural_boundary_enforces_cost_adjusted_net_rr() -> None:
    value = structural_candidate()
    config = settings(
        okx_demo_execution_max_adverse_slippage_bps=D("50"),
    )

    boundary, blocker = bounded_fok_execution_price(
        value,
        config,
        reference_price=D("100"),
        tick_size=D("0.01"),
    )
    priced, priced_blocker = candidate_at_execution_price(
        value,
        config,
        price=boundary.limit_price if boundary is not None else D("0"),
    )

    assert blocker is None
    assert boundary is not None
    assert boundary.limit_price == D("100.10")
    assert boundary.quality.gross_risk_reward > D("2")
    assert boundary.quality.net_risk_reward >= D("2")
    assert priced_blocker is None
    assert priced is not None
    assert priced.risk_reward == priced.net_risk_reward
    assert priced.net_risk_reward is not None
    assert priced.net_risk_reward >= D("2")
