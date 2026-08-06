# Realtime market data v0.8

The API process owns one OKX public WebSocket task. It subscribes to configured symbols and merges channel updates into one snapshot per instrument.

Data flow:

OKX public WS -> parser -> RealtimeMarketHub -> snapshot API -> PaperBroker tick

The stream never authenticates and cannot place exchange orders. If the socket disconnects, the client reconnects with capped exponential backoff. Paper auto ticks can be disabled with `PAPER_AUTO_TICKS=false`.
