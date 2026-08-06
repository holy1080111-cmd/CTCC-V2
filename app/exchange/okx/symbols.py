SUPPORTED_SYMBOLS: dict[str, str] = {
    "BTC/USDT:USDT": "BTC-USDT-SWAP",
    "ETH/USDT:USDT": "ETH-USDT-SWAP",
}


def to_instrument_id(symbol: str) -> str:
    normalized = symbol.strip().upper()
    for canonical, instrument_id in SUPPORTED_SYMBOLS.items():
        if normalized in {canonical.upper(), instrument_id.upper()}:
            return instrument_id
    raise ValueError(f"unsupported symbol: {symbol}")


def to_canonical_symbol(instrument_id: str) -> str:
    for symbol, known_id in SUPPORTED_SYMBOLS.items():
        if known_id == instrument_id:
            return symbol
    raise ValueError(f"unsupported instrument: {instrument_id}")
