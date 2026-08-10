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
- 21-candle causal log-price velocity and acceleration
- log-return RMS normalization, weighted R-squared, residual noise, and
  bounded derivative confidence
- 34-candle robust constant-acceleration state estimate with model-based
  velocity/acceleration uncertainty, Huber outlier weighting, and shock score
- 90% past-only conformal interval calibrated from 60 sequential one-bar
  residuals
- swing structure
- close-confirmed BOS/CHoCH indication
- unfilled FVG zones
- support/resistance from recent pivots

The derivative estimator is a recency-weighted local quadratic evaluated at
the latest closed-candle endpoint. All samples are confirmed and in the past;
the still-forming candle and future-centered filters are excluded.

The multi-timeframe mathematical core labels evidence as analytical,
prequential, or auxiliary. Only checked derivative/state evidence and a
conformal interval that passes its causal coverage diagnostic enter execution
direction, coverage, consensus, instability, and confidence. Uncalibrated
structure/momentum evidence is isolated in an auxiliary score. Missing checked
evidence lowers coverage; conflicting checked evidence lowers consensus.

The multi-timeframe result is descriptive. `trade_ready` means data,
4H/1H/15m/5m alignment, and non-opposed/non-unstable mathematical checks
passed; it is **not** an order authorization. See `mathematical_core.md`.
