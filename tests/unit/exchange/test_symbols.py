import pytest

from app.exchange.okx.symbols import to_canonical_symbol, to_instrument_id


def test_symbol_mapping() -> None:
    assert to_instrument_id("BTC/USDT:USDT") == "BTC-USDT-SWAP"
    assert to_instrument_id("eth-usdt-swap") == "ETH-USDT-SWAP"
    assert to_canonical_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"


def test_unsupported_symbol() -> None:
    with pytest.raises(ValueError):
        to_instrument_id("SOL/USDT:USDT")
