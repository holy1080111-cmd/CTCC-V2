from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.okx_demo import OkxDemoCancelRequest, OkxDemoOrderRequest


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValidationError):
        OkxDemoOrderRequest(
            instrument_id="BTC-USDT-SWAP",
            direction="long",
            size=Decimal("0.1"),
            order_type="limit",
            stop_loss=Decimal("90000"),
            take_profit=Decimal("110000"),
            confirmation="OKX_DEMO_ONLY",
        )


def test_fok_order_requires_price() -> None:
    with pytest.raises(ValidationError):
        OkxDemoOrderRequest(
            instrument_id="BTC-USDT-SWAP",
            direction="long",
            size=Decimal("0.1"),
            order_type="fok",
            stop_loss=Decimal("90000"),
            take_profit=Decimal("110000"),
            confirmation="OKX_DEMO_ONLY",
        )


def test_fok_order_accepts_price_bound() -> None:
    order = OkxDemoOrderRequest(
        instrument_id="BTC-USDT-SWAP",
        direction="long",
        size=Decimal("0.1"),
        order_type="fok",
        price=Decimal("100010"),
        stop_loss=Decimal("99000"),
        take_profit=Decimal("102000"),
        confirmation="OKX_DEMO_ONLY",
    )

    assert order.order_type == "fok"
    assert order.price == Decimal("100010")


def test_order_requires_exact_demo_confirmation() -> None:
    with pytest.raises(ValidationError):
        OkxDemoOrderRequest(
            instrument_id="BTC-USDT-SWAP",
            direction="long",
            size=Decimal("0.1"),
            stop_loss=Decimal("90000"),
            take_profit=Decimal("110000"),
            confirmation="YES",
        )


def test_cancel_requires_exactly_one_identifier() -> None:
    with pytest.raises(ValidationError):
        OkxDemoCancelRequest(
            instrument_id="BTC-USDT-SWAP",
            confirmation="OKX_DEMO_ONLY",
        )


def test_market_order_rejects_limit_price() -> None:
    with pytest.raises(ValidationError):
        OkxDemoOrderRequest(
            instrument_id="BTC-USDT-SWAP",
            direction="long",
            size=Decimal("0.1"),
            order_type="market",
            price=Decimal("100000"),
            stop_loss=Decimal("99000"),
            take_profit=Decimal("102000"),
            confirmation="OKX_DEMO_ONLY",
        )
