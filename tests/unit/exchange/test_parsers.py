from decimal import Decimal

from app.exchange.okx.parsers import parse_candle, parse_instrument, parse_ticker
from app.exchange.okx.private_parsers import parse_balance


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
            "adjEq": "",
            "availEq": "",
            "isoEq": "0",
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

    assert balance.available_equity == Decimal("0")
    assert balance.details[0].available_equity == Decimal(
        "4998.339000436543"
    )
    assert balance.details[0].equity_usd == Decimal("4997.839166536499")


def test_parse_candle() -> None:
    candle = parse_candle(["1750000000000", "100", "110", "95", "105", "12", "1.2", "1260", "1"])
    assert candle.confirmed is True
    assert candle.close == Decimal("105")


def test_parse_ticker_spread() -> None:
    ticker = parse_ticker({
        "instId": "BTC-USDT-SWAP", "last": "100", "bidPx": "99", "askPx": "101",
        "bidSz": "2", "askSz": "3", "open24h": "90", "high24h": "110",
        "low24h": "80", "vol24h": "1000", "volCcy24h": "100000", "ts": "1750000000000"
    })
    assert ticker.spread == Decimal("2")
    assert ticker.spread_pct == Decimal("2")
