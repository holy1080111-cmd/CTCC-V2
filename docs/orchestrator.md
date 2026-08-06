# Auto Paper Orchestrator

The orchestrator is a paper-only application service. It connects the analysis
and strategy engine to deterministic risk approval and persistent Paper
execution.

## Pipeline

1. Reject duplicate exposure for the symbol.
2. Fetch a fresh multi-timeframe strategy decision.
3. Require a fresh OKX public WebSocket snapshot for execution.
4. Reject excessive drift from the strategy entry.
5. Recalculate risk/reward at the actual reference price.
6. Build the Paper-account risk state.
7. Require deterministic risk approval.
8. Submit one idempotent Paper market order.
9. Persist the order, position, history and candidate fingerprint.
10. Let realtime ticks manage unrealized PnL, stop loss and take profit.

## Startup recovery

Before scheduled execution starts, v1.0 restores:

- Paper account, orders and positions
- Orchestrator run history
- Unexpired candidate fingerprints
- Peak equity baseline from the recovered account

## Safety boundaries

- `AUTO_TRADE=false` remains mandatory.
- `LIVE_TRADING=false` remains mandatory.
- `PAPER_AUTO_EXECUTION=false` is the default.
- Scheduled Paper execution requires persistence, WebSocket and automatic ticks.
- This release never authenticates to OKX and never submits exchange orders.
