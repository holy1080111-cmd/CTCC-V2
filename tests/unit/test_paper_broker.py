from decimal import Decimal

import pytest

from app.domain.paper import PaperOrderRequest
from app.paper.engine import PaperBroker, PaperBrokerError


def broker() -> PaperBroker:
    return PaperBroker(
        starting_balance=Decimal("10000"),
        taker_fee_rate=Decimal("0.0005"),
        maker_fee_rate=Decimal("0.0002"),
        slippage_bps=Decimal("2"),
    )


def market_long() -> PaperOrderRequest:
    return PaperOrderRequest(
        symbol="BTC-USDT-SWAP",
        side="long",
        quantity=Decimal("1"),
        reference_price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("110"),
        order_type="market",
        strategy="trend_pullback",
        score=82,
    )


def test_market_order_fills_and_opens_position() -> None:
    engine = broker()
    order = engine.submit(market_long())
    assert order.status == "filled"
    assert order.average_fill_price == Decimal("100.02000000")
    state = engine.state()
    assert state.account.open_positions == 1
    assert len(state.positions) == 1


def test_rejected_risk_decision_cannot_submit() -> None:
    engine = broker()
    request = market_long().model_copy(update={"risk_decision": "rejected"})
    with pytest.raises(PaperBrokerError, match="risk_decision_rejected"):
        engine.submit(request)


def test_limit_order_waits_then_fills_as_maker() -> None:
    engine = broker()
    request = market_long().model_copy(
        update={"order_type": "limit", "limit_price": Decimal("99"), "reference_price": Decimal("100")}
    )
    order = engine.submit(request)
    assert order.status == "pending"
    engine.tick(symbol="BTC-USDT-SWAP", price=Decimal("99.5"))
    assert engine.get_order(order.id).status == "pending"
    result = engine.tick(symbol="BTC-USDT-SWAP", price=Decimal("99"))
    assert order.id in result.filled_order_ids
    assert engine.get_order(order.id).average_fill_price == Decimal("99.00000000")


def test_take_profit_closes_long_position() -> None:
    engine = broker()
    engine.submit(market_long())
    position = engine.state().positions[0]
    result = engine.tick(symbol="BTC-USDT-SWAP", price=Decimal("110"))
    assert position.id in result.closed_position_ids
    closed = engine.get_position(position.id)
    assert closed.status == "closed"
    assert closed.close_reason == "take_profit"
    assert closed.realized_pnl > 0


def test_stop_loss_closes_short_position() -> None:
    engine = broker()
    request = PaperOrderRequest(
        symbol="ETH-USDT-SWAP",
        side="short",
        quantity=Decimal("2"),
        reference_price=Decimal("100"),
        stop_loss=Decimal("105"),
        take_profit=Decimal("90"),
        strategy="breakout_continuation",
        score=80,
    )
    engine.submit(request)
    position = engine.state().positions[0]
    engine.tick(symbol="ETH-USDT-SWAP", price=Decimal("105"))
    closed = engine.get_position(position.id)
    assert closed.status == "closed"
    assert closed.close_reason == "stop_loss"
    assert closed.realized_pnl < 0


def test_manual_close_updates_account() -> None:
    engine = broker()
    engine.submit(market_long())
    position = engine.state().positions[0]
    closed = engine.close(position.id, price=Decimal("102"), reason="manual_test")
    account = engine.account()
    assert closed.close_reason == "manual_test"
    assert account.open_positions == 0
    assert account.closed_trades == 1
    assert account.realized_pnl == closed.realized_pnl


def test_pending_limit_can_be_cancelled() -> None:
    engine = broker()
    request = market_long().model_copy(update={"order_type": "limit", "limit_price": Decimal("99")})
    order = engine.submit(request)
    cancelled = engine.cancel(order.id)
    assert cancelled.status == "cancelled"
    assert engine.account().pending_orders == 0


def test_reset_restores_starting_balance() -> None:
    engine = broker()
    engine.submit(market_long())
    state = engine.reset()
    assert state.orders == []
    assert state.positions == []
    assert state.account.cash_balance == Decimal("10000.00000000")


def test_restore_rebuilds_orders_positions_and_cash() -> None:
    source = broker()
    source.submit(market_long())
    source.tick(symbol="BTC-USDT-SWAP", price=Decimal("103"))
    snapshot = source.state()

    restored = broker()
    restored.restore(snapshot)

    assert restored.state() == snapshot
    assert restored.account().cash_balance == snapshot.account.cash_balance
    assert restored.state().orders[0].stop_loss == Decimal("95.00000000")
