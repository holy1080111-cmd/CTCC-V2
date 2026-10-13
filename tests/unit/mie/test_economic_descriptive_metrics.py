from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Context, Decimal, Inexact, localcontext

import pytest

from app.mie.validation.economic_metrics import (
    ClosedTradeOutcome,
    summarize_closed_trade_outcomes,
)

D = Decimal
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def closed_trade(
    index: int,
    pnl: Decimal,
    *,
    direction: str,
    symbol: str,
    regime: str,
) -> ClosedTradeOutcome:
    closed = T0 + timedelta(hours=index)
    return ClosedTradeOutcome(
        trade_id=f"trade-{index}",
        closed_at=closed,
        available_at=closed + timedelta(minutes=1),
        net_pnl=pnl,
        currency="USDT",
        direction=direction,  # type: ignore[arg-type]
        symbol=symbol,
        regime=regime,
    )


def outcomes() -> tuple[ClosedTradeOutcome, ...]:
    return (
        closed_trade(
            1, D("12"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
        ),
        closed_trade(
            2, D("-5"), direction="short", symbol="BTC-USDT-SWAP", regime="bear"
        ),
        closed_trade(
            3, D("3"), direction="long", symbol="ETH-USDT-SWAP", regime="range"
        ),
        closed_trade(
            4, D("0"), direction="short", symbol="ETH-USDT-SWAP", regime="range"
        ),
    )


def test_closed_trade_metrics_are_exact_deterministic_and_broken_down() -> None:
    as_of = T0 + timedelta(hours=5)
    result = summarize_closed_trade_outcomes(outcomes(), as_of=as_of)
    assert result == summarize_closed_trade_outcomes(outcomes(), as_of=as_of)
    assert result.all_trades.sample_count == 4
    assert (
        result.all_trades.win_count,
        result.all_trades.loss_count,
        result.all_trades.flat_count,
    ) == (2, 1, 1)
    assert result.all_trades.gross_profit == D("15")
    assert result.all_trades.gross_loss_magnitude == D("5")
    assert result.all_trades.net_profit == D("10")
    assert result.all_trades.net_expectancy_per_trade == D("2.5")
    assert result.all_trades.profit_factor == D("3")
    assert result.all_trades.average_win == D("7.5")
    assert result.all_trades.average_loss_magnitude == D("5")
    assert {key: item.net_profit for key, item in result.by_direction} == {
        "long": D("15"),
        "short": D("-5"),
    }
    assert {key: item.net_profit for key, item in result.by_symbol} == {
        "BTC-USDT-SWAP": D("7"),
        "ETH-USDT-SWAP": D("3"),
    }
    assert {key: item.net_profit for key, item in result.by_regime} == {
        "bear": D("-5"),
        "bull": D("12"),
        "range": D("3"),
    }
    assert result.input_scope == "caller_supplied_closed_trades_only"
    assert result.predictive_oos_eligible is False
    assert result.runtime_consumers == 0
    assert result.execution_authority is False


def test_closed_trade_metrics_ignore_caller_decimal_context() -> None:
    rows = (
        closed_trade(
            1, D("1"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
        ),
        closed_trade(
            2, D("0"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
        ),
        closed_trade(
            3, D("0"), direction="long", symbol="BTC-USDT-SWAP", regime="bull"
        ),
    )
    as_of = T0 + timedelta(hours=4)
    with localcontext(Context(prec=4, rounding=ROUND_DOWN)):
        rounded_down = summarize_closed_trade_outcomes(rows, as_of=as_of)
    with localcontext(Context(prec=4, rounding=ROUND_UP)):
        rounded_up = summarize_closed_trade_outcomes(rows, as_of=as_of)
    with localcontext(Context(prec=4, rounding=ROUND_DOWN, traps=[Inexact])):
        caller_trap = summarize_closed_trade_outcomes(rows, as_of=as_of)
    assert rounded_down == rounded_up
    assert rounded_down == caller_trap
    assert rounded_down.all_trades.net_expectancy_per_trade == D(
        "0.33333333333333333333333333333333333333333333333333"
    )
    assert rounded_down.predictive_oos_eligible is False
    assert rounded_down.execution_authority is False


def test_zero_loss_and_zero_wins_do_not_invent_profit_factor_or_average() -> None:
    positive = summarize_closed_trade_outcomes(
        (outcomes()[0], outcomes()[2]), as_of=T0 + timedelta(hours=5)
    ).all_trades
    assert positive.profit_factor is None
    assert positive.average_loss_magnitude is None
    negative = summarize_closed_trade_outcomes(
        (outcomes()[1],), as_of=T0 + timedelta(hours=5)
    ).all_trades
    assert negative.profit_factor == 0
    assert negative.average_win is None
    assert negative.average_loss_magnitude == D("5")


def test_as_of_prefix_rejects_unavailable_outcome_without_lookahead() -> None:
    rows = outcomes()
    prefix = summarize_closed_trade_outcomes(
        rows[:2], as_of=T0 + timedelta(hours=2, minutes=1)
    )
    assert prefix.all_trades.net_profit == D("7")
    with pytest.raises(ValueError, match="unavailable"):
        summarize_closed_trade_outcomes(
            rows[:3], as_of=T0 + timedelta(hours=2, minutes=1)
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"net_pnl": D("NaN")}, "finite"),
        ({"net_pnl": D("Infinity")}, "finite"),
        ({"net_pnl": None}, "Decimal or integer"),
        ({"net_pnl": 1.0}, "Decimal or integer"),
        ({"currency": "USD"}, "currency"),
        ({"direction": "sideways"}, "direction"),
        ({"available_at": T0}, "unavailable"),
        ({"closed_at": T0 + timedelta(hours=10)}, "unavailable"),
        ({"symbol": " BTC-USDT-SWAP"}, "canonical"),
    ],
)
def test_closed_trade_metrics_fail_closed_on_malformed_or_unknown_rows(
    change: dict[str, object], message: str
) -> None:
    first, second, *_ = outcomes()
    with pytest.raises(ValueError, match=message):
        summarize_closed_trade_outcomes(
            (first, replace(second, **change)),  # type: ignore[arg-type]
            as_of=T0 + timedelta(hours=5),
        )


def test_trade_identity_order_utc_and_currency_are_not_repaired() -> None:
    first, second, *_ = outcomes()
    with pytest.raises(ValueError, match="cannot be empty"):
        summarize_closed_trade_outcomes((), as_of=T0)
    with pytest.raises(ValueError, match="UTC"):
        summarize_closed_trade_outcomes((first,), as_of=T0.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        summarize_closed_trade_outcomes(
            (replace(first, available_at=first.available_at.replace(tzinfo=None)),),
            as_of=T0 + timedelta(hours=5),
        )
    with pytest.raises(ValueError, match="unique"):
        summarize_closed_trade_outcomes(
            (first, replace(second, trade_id=first.trade_id)),
            as_of=T0 + timedelta(hours=5),
        )
    with pytest.raises(ValueError, match="canonical close-time order"):
        summarize_closed_trade_outcomes((second, first), as_of=T0 + timedelta(hours=5))
