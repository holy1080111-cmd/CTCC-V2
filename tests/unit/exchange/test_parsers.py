from decimal import Decimal

from app.exchange.okx.parsers import parse_candle, parse_ticker


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
