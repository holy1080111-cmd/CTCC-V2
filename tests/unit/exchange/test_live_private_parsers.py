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


def test_live_account_config_preserves_unknown_permissions_for_fail_closed_gates() -> (
    None
):
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


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "null",
        "empty",
        "wrong_type",
        "bad_row",
        "btc_only",
        "blank_ccy",
        "duplicate_usdt",
    ],
)
def test_live_balance_requires_unique_explicit_usdt_settlement_detail(
    defect: str,
) -> None:
    detail = {
        "ccy": "USDT",
        "eq": "1000",
        "cashBal": "1000",
        "availBal": "900",
        "frozenBal": "100",
        "upl": "0",
    }
    row = {
        "totalEq": "1000",
        "isoEq": "0",
        "adjEq": "1000",
        "availEq": "900",
        "uTime": "1724742632153",
        "details": [detail],
    }
    if defect == "missing":
        del row["details"]
    elif defect == "null":
        row["details"] = None
    elif defect == "empty":
        row["details"] = []
    elif defect == "wrong_type":
        row["details"] = {}
    elif defect == "bad_row":
        row["details"] = [None]
    elif defect == "btc_only":
        row["details"] = [{**detail, "ccy": "BTC"}]
    elif defect == "blank_ccy":
        row["details"] = [{**detail, "ccy": ""}]
    elif defect == "duplicate_usdt":
        row["details"] = [detail, dict(detail)]
    with pytest.raises(ValueError):
        parse_live_balance(row)


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
    assert position.size == Decimal(-2)
    assert position.instrument_id == "BTC-USDT-SWAP"


def test_live_position_rejects_missing_exchange_position_id() -> None:
    with pytest.raises(ValidationError):
        parse_live_position(
            {
                "instId": "BTC-USDT-SWAP",
                "posSide": "net",
                "pos": "0",
                "availPos": "0",
                "upl": "0",
            }
        )


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
            "attachAlgoOrds": [],
        }
    )
    algo = parse_live_algo_order(
        {
            "algoId": "live-algo-1",
            "algoClOrdId": "ctcclivealgo1",
            "instType": "SWAP",
            "instId": "BTC-USDT-SWAP",
            "ordType": "conditional",
            "state": "live",
            "side": "sell",
            "posSide": "net",
            "tdMode": "cross",
            "reduceOnly": "true",
            "closeFraction": "0",
            "sz": "0.01",
            "actualSz": "0",
            "tpTriggerPx": "66000",
            "tpTriggerPxType": "mark",
            "tpOrdPx": "-1",
            "slTriggerPx": "62000",
            "slTriggerPxType": "mark",
            "slOrdPx": "-1",
            "amendPxOnTriggerType": "0",
            "failCode": "",
            "triggerTime": "",
        }
    )

    assert balance.total_equity == Decimal("123.45678901")
    assert balance.details[0].currency == "USDT"
    assert order.order_id == "live-order-1"
    assert order.price == Decimal("64000.1")
    assert algo.algo_order_id == "live-algo-1"
    assert algo.instrument_type == "SWAP"
    assert algo.margin_mode == "cross"
    assert algo.reduce_only is True
    assert algo.actual_size == Decimal(0)
    assert algo.take_profit_trigger_price_type == "mark"
    assert algo.take_profit_order_price == Decimal(-1)
    assert algo.stop_loss_trigger_price == Decimal(62000)
    assert algo.stop_loss_trigger_price_type == "mark"
    assert algo.stop_loss_order_price == Decimal(-1)
    assert algo.failure_code is None
    assert algo.trigger_time is None


def _balance_row() -> dict[str, object]:
    return {
        "totalEq": "100",
        "isoEq": "0",
        "adjEq": "100",
        "availEq": "100",
        "uTime": "1724742632153",
        "details": [
            {
                "ccy": "USDT",
                "eq": "100",
                "cashBal": "100",
                "availBal": "100",
                "frozenBal": "0",
                "upl": "0",
            }
        ],
    }


def _position_row() -> dict[str, object]:
    return {
        "posId": "position-1",
        "instId": "BTC-USDT-SWAP",
        "posSide": "net",
        "pos": "0",
        "availPos": "0",
        "upl": "0",
    }


def _order_row() -> dict[str, object]:
    return {
        "ordId": "order-1",
        "instId": "BTC-USDT-SWAP",
        "side": "buy",
        "ordType": "limit",
        "state": "live",
        "sz": "1",
        "accFillSz": "0",
        "reduceOnly": "false",
        "attachAlgoOrds": [],
    }


@pytest.mark.parametrize("attached", ["missing", None, {}, [None]])
def test_live_order_rejects_missing_or_malformed_attachment_evidence(attached):
    row = _order_row()
    if attached == "missing":
        del row["attachAlgoOrds"]
    else:
        row["attachAlgoOrds"] = attached
    with pytest.raises(ValueError, match="attached_algo_evidence_missing_or_invalid"):
        parse_live_order(row)


def _algo_row() -> dict[str, object]:
    return {
        "algoId": "algo-1",
        "instId": "BTC-USDT-SWAP",
        "ordType": "conditional",
        "state": "live",
        "sz": "1",
        "actualSz": "0",
    }


@pytest.mark.parametrize(
    ("parser", "row_factory", "fields"),
    [
        (parse_live_balance, _balance_row, ("totalEq", "isoEq", "adjEq", "availEq")),
        (parse_live_position, _position_row, ("pos", "availPos", "upl")),
        (parse_live_order, _order_row, ("sz", "accFillSz")),
        (parse_live_algo_order, _algo_row, ("sz", "actualSz")),
    ],
)
def test_live_required_numerics_reject_missing_blank_and_nonfinite(
    parser, row_factory, fields
) -> None:
    for field in fields:
        for value in (None, "", "NaN", "Infinity"):
            row = row_factory()
            if value is not None:
                row[field] = value
            else:
                del row[field]
            with pytest.raises(ValueError, match=f"missing_or_invalid:{field}"):
                parser(row)


def test_live_balance_detail_numerics_and_source_time_are_required() -> None:
    for field in ("eq", "cashBal", "availBal", "frozenBal", "upl"):
        row = _balance_row()
        del row["details"][0][field]
        with pytest.raises(ValueError, match=f"missing_or_invalid:{field}"):
            parse_live_balance(row)

    for value in (None, "", "0", "not-a-timestamp"):
        row = _balance_row()
        if value is None:
            del row["uTime"]
        else:
            row["uTime"] = value
        with pytest.raises(
            ValueError, match="okx_live_timestamp_missing_or_invalid:uTime"
        ):
            parse_live_balance(row)


def test_live_capability_and_order_boolean_source_must_be_explicit() -> None:
    config_row = {
        "uid": "42",
        "mainUid": "42",
        "posMode": "net_mode",
        "perm": "",
        "ip": "",
    }
    capability = parse_live_account_config(config_row).capability
    assert capability.read_permission is False
    assert capability.ip_bound is False

    for field in ("perm", "ip"):
        row = dict(config_row)
        del row[field]
        with pytest.raises(ValueError, match="missing_or_invalid"):
            parse_live_account_config(row)

    order = parse_live_order(_order_row())
    assert order.reduce_only is False
    assert order.accumulated_fill_size == Decimal(0)
    assert order.raw == _order_row()
    for value in (None, "", "unknown", "0"):
        row = _order_row()
        if value is None:
            del row["reduceOnly"]
        else:
            row["reduceOnly"] = value
        with pytest.raises(
            ValueError, match="okx_live_boolean_field_missing_or_invalid"
        ):
            parse_live_order(row)


def test_live_optional_boolean_and_numeric_values_never_coerce_invalid_to_false() -> (
    None
):
    row = _algo_row()
    assert parse_live_algo_order(row).reduce_only is None
    assert parse_live_algo_order(row).actual_size == Decimal(0)
    assert parse_live_algo_order(row).raw == row
    row["reduceOnly"] = "unexpected"
    with pytest.raises(ValueError, match="okx_live_boolean_field_missing_or_invalid"):
        parse_live_algo_order(row)
    row = _position_row()
    row["markPx"] = "NaN"
    with pytest.raises(ValueError, match="okx_live_optional_numeric_field_invalid"):
        parse_live_position(row)
