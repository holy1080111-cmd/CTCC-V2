from decimal import Decimal

import pytest

from app.exchange.okx.parsers import parse_candle, parse_instrument, parse_ticker
from app.exchange.okx.private_parsers import parse_balance, parse_position


def test_parse_instrument_preserves_settlement_currency() -> None:
    instrument = parse_instrument(
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "state": "live",
            "tickSz": "0.1",
            "lotSz": "1",
            "minSz": "1",
            "ctVal": "0.01",
            "ctValCcy": "BTC",
            "settleCcy": "USDT",
        }
    )

    assert instrument.settlement_currency == "USDT"


def test_parse_demo_balance_preserves_currency_available_equity() -> None:
    balance = parse_balance(
        {
            "totalEq": "81511.77052855614",
            "adjEq": "81000",
            "availEq": "6000",
            "isoEq": "0",
            "uTime": "1750000000000",
            "details": [
                {
                    "ccy": "USDT",
                    "eq": "4998.339000436543",
                    "eqUsd": "4997.839166536499",
                    "availEq": "4998.339000436543",
                    "availBal": "4998.339000436543",
                    "cashBal": "4998.339000436543",
                    "frozenBal": "0",
                    "upl": "0",
                }
            ],
        }
    )

    assert balance.available_equity == Decimal(6000)
    assert balance.captured_at.timestamp() == 1750000000
    assert balance.details[0].available_equity == Decimal("4998.339000436543")
    assert balance.details[0].equity_usd == Decimal("4997.839166536499")


@pytest.mark.parametrize(
    "field,value",
    [
        ("totalEq", ""),
        ("adjEq", None),
        ("availEq", ""),
        ("isoEq", None),
        ("uTime", ""),
        ("details", None),
        ("details", []),
    ],
)
def test_missing_balance_risk_inputs_fail_closed(field, value) -> None:
    row = {
        "totalEq": "1000",
        "adjEq": "900",
        "availEq": "800",
        "isoEq": "0",
        "uTime": "1750000000000",
        "details": [
            {
                "ccy": "USDT",
                "eq": "1000",
                "eqUsd": "1000",
                "availEq": "800",
                "availBal": "800",
                "cashBal": "1000",
                "frozenBal": "0",
                "upl": "0",
            }
        ],
    }
    row[field] = value

    with pytest.raises(ValueError):
        parse_balance(row)


@pytest.mark.parametrize("field", ["eq", "availEq", "availBal"])
def test_missing_balance_detail_value_is_not_defaulted_to_zero(field) -> None:
    row = {
        "totalEq": "1000",
        "adjEq": "900",
        "availEq": "800",
        "isoEq": "0",
        "uTime": "1750000000000",
        "details": [
            {
                "ccy": "USDT",
                "eq": "1000",
                "eqUsd": "1000",
                "availEq": "800",
                "availBal": "800",
                "cashBal": "1000",
                "frozenBal": "0",
                "upl": "0",
            }
        ],
    }
    row["details"][0][field] = ""

    with pytest.raises(ValueError):
        parse_balance(row)


def test_missing_position_size_or_identity_is_not_defaulted_to_flat() -> None:
    row = {
        "instId": "BTC-USDT-SWAP",
        "instType": "SWAP",
        "posSide": "net",
        "mgnMode": "cross",
        "pos": "0",
        "availPos": "0",
        "upl": "0",
    }
    assert parse_position(row).size == Decimal(0)

    row.pop("pos")
    with pytest.raises(ValueError):
        parse_position(row)

    row["pos"] = "1"
    row.pop("instId")
    with pytest.raises(ValueError):
        parse_position(row)


def test_parse_candle() -> None:
    candle = parse_candle(
        ["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", "1"]
    )
    assert candle.confirmed is True
    assert candle.close == Decimal(105)
    assert (candle.volume_contracts, candle.volume_currency, candle.volume_quote) == (
        Decimal(12),
        Decimal("1.2"),
        Decimal(1260),
    )


@pytest.mark.parametrize("index", [5, 6, 7])
@pytest.mark.parametrize("bad", ["", None, 0, "NaN", "Infinity", "-1", "1e2"])
def test_parse_candle_missing_or_malformed_volume_fails_closed(index, bad) -> None:
    row = ["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", "1"]
    row[index] = bad
    with pytest.raises(ValueError):
        parse_candle(row)


@pytest.mark.parametrize("bad", ["", "2"])
def test_parse_candle_invalid_confirmation_fails_closed(bad) -> None:
    row = ["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", bad]
    with pytest.raises(ValueError):
        parse_candle(row)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
@pytest.mark.parametrize("bad", ["", "0", "NaN", "Infinity", "-1"])
def test_parse_candle_missing_or_malformed_price_fails_closed(index, bad) -> None:
    row = ["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", "1"]
    row[index] = bad
    with pytest.raises(ValueError):
        parse_candle(row)


@pytest.mark.parametrize("bad", ["", "1", "-1750000000000", "17500000000000"])
def test_parse_candle_invalid_timestamp_fails_closed(bad) -> None:
    row = [bad, "100", "110", "95", "105", "12", "1.2", "1260", "1"]
    with pytest.raises(ValueError):
        parse_candle(row)


@pytest.mark.parametrize("index,value", [(2, "99"), (3, "106")])
def test_parse_candle_invalid_geometry_fails_closed(index, value) -> None:
    row = ["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", "1"]
    row[index] = value
    with pytest.raises(ValueError):
        parse_candle(row)


def test_parse_ticker_spread() -> None:
    ticker = parse_ticker(
        {
            "instType": "SWAP",
            "instId": "BTC-USDT-SWAP",
            "last": "100",
            "bidPx": "99",
            "askPx": "101",
            "bidSz": "2",
            "askSz": "3",
            "open24h": "90",
            "high24h": "110",
            "low24h": "80",
            "vol24h": "1000",
            "volCcy24h": "100000",
            "ts": "1750000000000",
        }
    )
    assert ticker.spread == Decimal(2)
    assert ticker.spread_pct == Decimal(2)
