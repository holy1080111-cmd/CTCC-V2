import random
from decimal import Decimal

import pytest

from app.demo_automation.capital_bucket import (
    build_demo_capital_bucket_plan,
    demo_position_notional_ceiling,
)


D = Decimal


@pytest.mark.parametrize(
    (
        "equity",
        "expected_margin",
        "expected_slots",
        "expected_limit",
    ),
    [
        ("0.01", "0.01", 1, 1),
        ("1999.99", "1999.99", 1, 1),
        ("2000", "2000", 1, 1),
        ("2000.01", "2000", 1, 1),
        ("3999.99", "2000", 1, 1),
        ("4000", "2000", 2, 2),
        ("6000", "2000", 3, 3),
        ("10000", "2000", 5, 3),
    ],
)
def test_capital_bucket_boundaries_are_exact_decimal_partitions(
    equity: str,
    expected_margin: str,
    expected_slots: int,
    expected_limit: int,
) -> None:
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D(equity),
        available_equity_usdt=D(equity),
        configured_bucket_usdt=D("2000"),
        configured_position_limit=3,
    )

    assert plan.target_position_margin_usdt == D(expected_margin)
    assert plan.available_position_margin_cap_usdt == D(expected_margin)
    assert plan.capital_slot_count == expected_slots
    assert plan.effective_position_limit == expected_limit


def test_exchange_availability_can_only_reduce_current_position_cap() -> None:
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D("4998.339000436543"),
        available_equity_usdt=D("750"),
        configured_bucket_usdt=D("2000"),
        configured_position_limit=3,
    )

    assert plan.target_position_margin_usdt == D("2000")
    assert plan.available_position_margin_cap_usdt == D("750")
    assert plan.capital_slot_count == 2
    assert plan.effective_position_limit == 2


def test_available_equity_is_clamped_to_risk_equity() -> None:
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D("1500"),
        available_equity_usdt=D("9000"),
        configured_bucket_usdt=D("2000"),
        configured_position_limit=3,
    )

    assert plan.available_equity_usdt == D("1500")
    assert plan.available_position_margin_cap_usdt == D("1500")


@pytest.mark.parametrize(
    "updates",
    [
        {"risk_equity_usdt": D("0")},
        {"available_equity_usdt": D("-0.01")},
        {"configured_bucket_usdt": D("0")},
        {"configured_position_limit": 0},
    ],
)
def test_invalid_capital_bucket_inputs_fail_closed(updates: dict[str, object]) -> None:
    values: dict[str, object] = {
        "risk_equity_usdt": D("5000"),
        "available_equity_usdt": D("5000"),
        "configured_bucket_usdt": D("2000"),
        "configured_position_limit": 3,
    }
    values.update(updates)

    with pytest.raises(ValueError):
        build_demo_capital_bucket_plan(**values)  # type: ignore[arg-type]


def test_randomized_capital_bucket_invariants() -> None:
    generator = random.Random(20260812)
    bucket = D("2000")

    for _ in range(1000):
        equity = D(generator.randint(1, 2_000_000_000)) / D("100000")
        available = D(generator.randint(0, int(equity * D("100000")))) / D(
            "100000"
        )
        configured_limit = generator.randint(1, 10)
        plan = build_demo_capital_bucket_plan(
            risk_equity_usdt=equity,
            available_equity_usdt=available,
            configured_bucket_usdt=bucket,
            configured_position_limit=configured_limit,
        )

        assert D("0") <= plan.available_position_margin_cap_usdt
        assert plan.available_position_margin_cap_usdt <= available
        assert plan.available_position_margin_cap_usdt <= (
            equity if equity <= bucket else bucket
        )
        assert 1 <= plan.effective_position_limit <= configured_limit
        assert (
            plan.target_position_margin_usdt * plan.effective_position_limit
            <= equity
        )
        if equity <= bucket:
            assert plan.target_position_margin_usdt == equity
            assert plan.capital_slot_count == 1
        else:
            assert plan.target_position_margin_usdt == bucket
            residual = equity - bucket * plan.capital_slot_count
            assert D("0") <= residual < bucket


@pytest.mark.parametrize("leverage", [1, 2, 3, 5])
def test_notional_ceiling_preserves_margin_bucket_at_every_leverage(
    leverage: int,
) -> None:
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D("5000"),
        available_equity_usdt=D("5000"),
        configured_bucket_usdt=D("2000"),
        configured_position_limit=3,
    )
    notional = demo_position_notional_ceiling(
        plan=plan,
        leverage=leverage,
        global_notional_ceiling_usdt=D("100000"),
    )

    assert notional == D("2000") * leverage
    assert notional / D(leverage) == D("2000")


def test_global_notional_cap_can_only_reduce_bucket_buying_power() -> None:
    plan = build_demo_capital_bucket_plan(
        risk_equity_usdt=D("5000"),
        available_equity_usdt=D("5000"),
        configured_bucket_usdt=D("2000"),
        configured_position_limit=3,
    )

    assert demo_position_notional_ceiling(
        plan=plan,
        leverage=3,
        global_notional_ceiling_usdt=D("5000"),
    ) == D("5000")
