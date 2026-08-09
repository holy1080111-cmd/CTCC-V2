# Frozen implementation sequence

- v0.1 Foundation
- v0.2 Transaction Core and database schema
- v0.3 OKX public REST market data and data quality
- v0.4 Indicators, structure, and regime
- v0.5 Strategy evaluation
- v0.6 Risk engine
- v0.7 Paper broker
- v0.8 OKX public WebSocket realtime market
- v0.9 Auto Paper Orchestrator
- v1.0 Persistence and restart recovery (completed)
- v1.1 OKX Demo execution and reconciliation
- v1.2 Position-management hardening and monitoring
- v1.3 Demo soak and observability
- v1.4 Controlled Demo execution soak
- v1.5 Demo reliability and performance validation
- v1.6.8 Isolated OKX Live reads, real-position execution gates, and one-shot automation
- v2.0 Final acceptance

## v1.0 completed

- Persistent Paper account, orders, positions and PnL
- Persistent orchestrator history and candidate fingerprints
- Startup recovery before realtime/scheduled execution
- Checksum reconciliation and audit records
- Backup, restore and restart-verification scripts

## v1.1 completed

- Authenticated OKX Demo REST reads and request signing
- Manual Demo order, cancel, close-position and leverage operations
- Attached TP/SL with strict local safety gates
- Exchange-authoritative PostgreSQL reconciliation
- Automatic OKX Demo execution and live execution remain unavailable


## v1.2 completed

- Explicitly armed OKX Demo automation
- One protected order per scan
- Daily loss, trade-count, consecutive-loss, cooldown, and duplicate locks
- Emergency stop and restart disarm
- Persistent automation state, runs, and fingerprints

## v1.3 completed

- Observation-only long-running soak sessions
- Heartbeat watchdog, metrics, durable events, and restart interruption detection

## v1.4 completed

- Controlled execute-soak preflight
- Flat-start, submission-cap, session-loss, and protection guardrails
- Emergency safety stop and automatic disarm on every execute-soak exit path
- Durable execute-soak equity, drawdown, protection, and exposure telemetry


## v1.5 completed

- Append-only Demo equity and exposure snapshots on successful reconciliation
- Realized PnL, recorded fee/rebate/funding, adverse-slippage, and drawdown analysis
- UTC daily performance reports persisted in PostgreSQL
- Strategy-level sample metrics and explicit review recommendations
- Authenticated operator enable/disable controls for future candidate selection
- Reliability evidence gates for active days, realized trades, slippage, profit factor, and drawdown
- Automatic strategy disabling and live-money execution remain unavailable

## v1.6.8 implementation complete; operator acceptance pending

- Dedicated production REST transport with Demo header structurally absent
- Account capability validation and one-way identity pinning
- Atomic Live PostgreSQL mirror and durable execution-intent idempotency
- Production-only write settings, process-local expiring Arm, one submission, and auto-disarm
- Protected market-order precheck, contract/notional/leverage caps, bounded polling, and reconciliation
- Explicit cancel, close, leverage, Emergency Stop, and clear-stop flows
- One-shot strategy/risk automation that can write only through the Live service
- Remaining acceptance: local PostgreSQL migration/regression and staged real-account read/micro-order evidence
