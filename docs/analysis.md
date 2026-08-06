# Analysis Engine v0.4

The analysis layer consumes only validated `MarketSnapshot` data. It does not call OKX directly and cannot create orders.

Implemented outputs per timeframe:
- EMA 20/50/200
- ATR and ATR percentage
- RSI 14
- MACD 12/26/9
- ADX directional-strength approximation
- rolling VWAP
- volume ratio
- swing structure
- close-confirmed BOS/CHoCH indication
- unfilled FVG zones
- support/resistance from recent pivots

The multi-timeframe result is descriptive. `trade_ready` means data and 4H/1H/15m/5m direction alignment passed; it is **not** an order authorization. Risk and execution are not implemented in v0.4.
