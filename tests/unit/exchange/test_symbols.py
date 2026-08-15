import pytest

from app.exchange.okx.symbols import (
    LIVE_BOUNDARY_INSTRUMENT_IDS,
    REVIEWED_DEMO_INSTRUMENT_IDS,
    to_canonical_symbol,
    to_instrument_id,
)


EXPECTED_DEMO_UNIVERSE = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "LINK-USDT-SWAP",
    "LTC-USDT-SWAP",
)


def test_symbol_mapping() -> None:
    assert to_instrument_id("BTC/USDT:USDT") == "BTC-USDT-SWAP"
    assert to_instrument_id("eth-usdt-swap") == "ETH-USDT-SWAP"
    assert to_instrument_id("sol/usdt:usdt") == "SOL-USDT-SWAP"
    assert to_instrument_id("xrp-usdt-swap") == "XRP-USDT-SWAP"
    assert to_instrument_id("DOGE/USDT:USDT") == "DOGE-USDT-SWAP"
    assert to_instrument_id("ADA-USDT-SWAP") == "ADA-USDT-SWAP"
    assert to_instrument_id("link/usdt:usdt") == "LINK-USDT-SWAP"
    assert to_instrument_id("LTC-USDT-SWAP") == "LTC-USDT-SWAP"
    assert to_canonical_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"
    assert to_canonical_symbol("SOL-USDT-SWAP") == "SOL/USDT:USDT"
    assert to_canonical_symbol(" xrp-usdt-swap ") == "XRP/USDT:USDT"


def test_reviewed_demo_universe_does_not_expand_live_boundary() -> None:
    assert REVIEWED_DEMO_INSTRUMENT_IDS == EXPECTED_DEMO_UNIVERSE
    assert LIVE_BOUNDARY_INSTRUMENT_IDS == (
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    )
    assert set(LIVE_BOUNDARY_INSTRUMENT_IDS) < set(REVIEWED_DEMO_INSTRUMENT_IDS)


def test_unsupported_symbol() -> None:
    with pytest.raises(ValueError):
        to_instrument_id("BNB/USDT:USDT")
