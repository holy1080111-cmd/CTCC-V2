"""Malformed private rows must never be interpreted as a flat Demo account."""

from decimal import Decimal

import pytest

from app.exchange.okx.private_parsers import (
    parse_account_config,
    parse_algo_order,
    parse_order,
)


def _account_row() -> dict[str, object]:
    return {"uid": "12345", "acctLv": "2", "posMode": "net_mode"}


def _order_row() -> dict[str, object]:
    return {
        "ordId": "7788",
        "instId": "BTC-USDT-SWAP",
        "side": "buy",
        "posSide": "net",
        "ordType": "limit",
        "state": "live",
        "sz": "1",
        "accFillSz": "0",
        "px": "100",
        "avgPx": "",
        "reduceOnly": "false",
        "attachAlgoOrds": [],
        "cTime": "1750000000000",
    }


def _algo_row() -> dict[str, object]:
    return {
        "algoId": "9911",
        "instId": "BTC-USDT-SWAP",
        "ordType": "oco",
        "state": "live",
        "side": "sell",
        "posSide": "net",
        "sz": "1",
        "tpTriggerPx": "120",
        "slTriggerPx": "90",
    }


def test_account_identity_and_position_mode_are_source_values() -> None:
    parsed = parse_account_config(_account_row())
    assert (parsed.uid, parsed.account_level, parsed.position_mode) == (
        "12345",
        "2",
        "net_mode",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", None),
        ("uid", ""),
        ("acctLv", None),
        ("posMode", None),
        ("posMode", ""),
        ("posMode", "unexpected"),
    ],
)
def test_missing_account_identity_or_mode_is_rejected(
    field: str, value: object
) -> None:
    row = _account_row()
    row[field] = value
    with pytest.raises(ValueError):
        parse_account_config(row)


def test_order_keeps_source_zero_and_explicit_empty_attachment_list() -> None:
    parsed = parse_order(_order_row())
    assert parsed.order_id == "7788"
    assert parsed.accumulated_fill_size == Decimal(0)
    assert parsed.reduce_only is False
    assert parsed.attached_algo_orders == []


@pytest.mark.parametrize("field", ["ordId", "instId", "side", "ordType", "state"])
def test_missing_order_identity_cannot_be_parsed(field: str) -> None:
    row = _order_row()
    row.pop(field)
    with pytest.raises(ValueError):
        parse_order(row)


@pytest.mark.parametrize("field", ["sz", "accFillSz"])
@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "no-number", True])
def test_order_size_or_fill_cannot_be_unknown(field: str, value: object) -> None:
    row = _order_row()
    row[field] = value
    with pytest.raises(ValueError):
        parse_order(row)


@pytest.mark.parametrize("value", [None, "", "unknown", 0, 1])
def test_missing_or_invalid_reduce_only_does_not_become_false(value: object) -> None:
    row = _order_row()
    row["reduceOnly"] = value
    with pytest.raises(ValueError):
        parse_order(row)


@pytest.mark.parametrize("value", [None, "", {}, [None], ["algo"]])
def test_missing_or_malformed_attachment_evidence_is_rejected(value: object) -> None:
    row = _order_row()
    row["attachAlgoOrds"] = value
    with pytest.raises(ValueError):
        parse_order(row)


@pytest.mark.parametrize("field", ["px", "avgPx"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", True])
def test_present_invalid_optional_price_is_not_accepted(
    field: str, value: object
) -> None:
    row = _order_row()
    row[field] = value
    with pytest.raises(ValueError):
        parse_order(row)


@pytest.mark.parametrize(
    "value", ["-1", "not-a-time", False, True, "99999999999999999999"]
)
def test_present_invalid_order_timestamp_is_rejected(value: object) -> None:
    row = _order_row()
    row["cTime"] = value
    with pytest.raises(ValueError):
        parse_order(row)


def test_algo_order_keeps_source_size_and_prices() -> None:
    parsed = parse_algo_order(_algo_row())
    assert (parsed.algo_order_id, parsed.size) == ("9911", Decimal(1))
    assert parsed.take_profit_trigger_price == Decimal(120)


@pytest.mark.parametrize("field", ["algoId", "instId", "ordType", "state", "sz"])
def test_missing_algo_identity_or_size_is_rejected(field: str) -> None:
    row = _algo_row()
    row.pop(field)
    with pytest.raises(ValueError):
        parse_algo_order(row)
