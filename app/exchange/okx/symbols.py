REVIEWED_DEMO_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("BTC/USDT:USDT", "BTC-USDT-SWAP"),
    ("ETH/USDT:USDT", "ETH-USDT-SWAP"),
    ("SOL/USDT:USDT", "SOL-USDT-SWAP"),
    ("XRP/USDT:USDT", "XRP-USDT-SWAP"),
    ("DOGE/USDT:USDT", "DOGE-USDT-SWAP"),
    ("ADA/USDT:USDT", "ADA-USDT-SWAP"),
    ("LINK/USDT:USDT", "LINK-USDT-SWAP"),
    ("LTC/USDT:USDT", "LTC-USDT-SWAP"),
)

REVIEWED_DEMO_INSTRUMENT_IDS: tuple[str, ...] = tuple(
    instrument_id for _, instrument_id in REVIEWED_DEMO_SYMBOLS
)

# The production boundary is deliberately narrower than the reviewed Demo and
# public-data universe. Expanding Demo coverage must never expand Live scope.
LIVE_BOUNDARY_INSTRUMENT_IDS: tuple[str, ...] = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
)

SUPPORTED_SYMBOLS: dict[str, str] = dict(REVIEWED_DEMO_SYMBOLS)


def to_instrument_id(symbol: str) -> str:
    normalized = symbol.strip().upper()
    for canonical, instrument_id in SUPPORTED_SYMBOLS.items():
        if normalized in {canonical.upper(), instrument_id.upper()}:
            return instrument_id
    raise ValueError(f"unsupported symbol: {symbol}")


def to_canonical_symbol(instrument_id: str) -> str:
    normalized = instrument_id.strip().upper()
    for symbol, known_id in SUPPORTED_SYMBOLS.items():
        if known_id == normalized:
            return symbol
    raise ValueError(f"unsupported instrument: {instrument_id}")
