from decimal import Decimal

from app.exchange.okx.ws_parser import parse_public_message


def test_parse_ticker() -> None:
    events = parse_public_message({
        "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
        "data": [{"last": "100", "bidPx": "99.9", "askPx": "100.1", "ts": "1700000000000"}],
    })
    assert events[0]["last"] == Decimal("100")
    assert events[0]["bid"] == Decimal("99.9")
    assert events[0]["ask"] == Decimal("100.1")


def test_parse_book_and_funding() -> None:
    book = parse_public_message({
        "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"},
        "data": [{"bids": [["99", "2", "0", "3"]], "asks": [["101", "1", "0", "2"]], "ts": "1700000000000"}],
    })[0]
    assert book["bids"][0]["price"] == Decimal("99")
    assert book["asks"][0]["order_count"] == 2
    funding = parse_public_message({
        "arg": {"channel": "funding-rate", "instId": "BTC-USDT-SWAP"},
        "data": [{"fundingRate": "0.0001", "nextFundingTime": "1700003600000", "ts": "1700000000000"}],
    })[0]
    assert funding["funding_rate"] == Decimal("0.0001")


def test_subscription_ack_is_ignored() -> None:
    assert parse_public_message({"event": "subscribe", "arg": {"channel": "tickers"}}) == []
