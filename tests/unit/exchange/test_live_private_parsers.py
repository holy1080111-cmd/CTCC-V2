from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.exchange.okx.live_private_parsers import (
    parse_live_account_config,
    parse_live_algo_order,
    parse_live_balance,
    parse_live_order,
    parse_live_position,
)


def test_live_account_config_extracts_capabilities_without_retaining_ip() -> None:
    config = parse_live_account_config(
        {
            "uid": "42",
            "mainUid": "42",
            "acctLv": "2",
            "acctStpMode": "cancel_maker",
            "posMode": "long_short_mode",
            "type": "0",
            "perm": "read_only,trade",
            "ip": "203.0.113.10",
        }
    )

    assert config.is_sub_account is False
    assert config.capability.permissions == ["read_only", "trade"]
    assert config.capability.unknown_permissions == []
    assert config.capability.read_permission is True
    assert config.capability.trade_permission is True
    assert config.capability.withdraw_permission is False
    assert config.capability.ip_bound is True
    assert "203.0.113.10" not in config.model_dump_json()


def test_live_account_config_preserves_unknown_permissions_for_fail_closed_gates() -> None:
    config = parse_live_account_config(
        {
            "uid": "sub-42",
            "mainUid": "main-1",
            "posMode": "net_mode",
            "perm": "withdraw, future_permission,read_only,trade",
            "ip": "",
        }
    )

    assert config.is_sub_account is True
    assert config.capability.permissions == [
        "future_permission",
        "read_only",
        "trade",
        "withdraw",
    ]
    assert config.capability.unknown_permissions == ["future_permission"]
    assert config.capability.withdraw_permission is True
    assert config.capability.ip_bound is False


def test_live_account_config_requires_explicit_position_mode() -> None:
    with pytest.raises(ValidationError):
        parse_live_account_config({"perm": "read_only", "ip": ""})


def test_live_position_uses_exchange_position_id() -> None:
    position = parse_live_position(
        {
            "posId": "1752810569801498626",
            "instId": "BTC-USDT-SWAP",
            "posSide": "net",
            "pos": "-2",
            "availPos": "2",
            "avgPx": "64000.2",
            "markPx": "63908.4",
            "upl": "-1.5",
            "lever": "1",
            "mgnMode": "cross",
            "liqPx": "",
            "cTime": "1724740225685",
            "uTime": "1724742632153",
        }
    )

    assert position.position_id == "1752810569801498626"
    assert position.position_key == position.position_id
    assert position.size == Decimal("-2")
    assert position.instrument_id == "BTC-USDT-SWAP"


def test_live_position_rejects_missing_exchange_position_id() -> None:
    with pytest.raises(ValidationError):
        parse_live_position({"instId": "BTC-USDT-SWAP", "posSide": "net"})


def test_live_read_parsers_keep_exchange_identifiers_and_numeric_precision() -> None:
    balance = parse_live_balance(
        {
            "totalEq": "123.45678901",
            "isoEq": "0",
            "adjEq": "120.5",
            "availEq": "119.75",
            "uTime": "1724742632153",
            "details": [
                {
                    "ccy": "USDT",
                    "eq": "123.45678901",
                    "cashBal": "123.4",
                    "availBal": "119.75",
                    "frozenBal": "3.65",
                    "upl": "0.05678901",
                }
            ],
        }
    )
    order = parse_live_order(
        {
            "ordId": "live-order-1",
            "clOrdId": "ctcclive1",
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "posSide": "net",
            "ordType": "limit",
            "state": "live",
            "sz": "0.01",
            "accFillSz": "0",
            "px": "64000.1",
            "reduceOnly": "false",
        }
    )
    algo = parse_live_algo_order(
        {
            "algoId": "live-algo-1",
            "algoClOrdId": "ctcclivealgo1",
            "instId": "BTC-USDT-SWAP",
            "ordType": "conditional",
            "state": "live",
            "side": "sell",
            "posSide": "net",
            "sz": "0.01",
            "tpTriggerPx": "66000",
            "slTriggerPx": "62000",
        }
    )

    assert balance.total_equity == Decimal("123.45678901")
    assert balance.details[0].currency == "USDT"
    assert order.order_id == "live-order-1"
    assert order.price == Decimal("64000.1")
    assert algo.algo_order_id == "live-algo-1"
    assert algo.stop_loss_trigger_price == Decimal("62000")
