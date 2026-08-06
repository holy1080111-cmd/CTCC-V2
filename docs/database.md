# Database ownership

## Analysis

- `analysis_runs`
- `timeframe_analyses`
- `strategy_evaluations`
- `trade_candidates`

## Risk and lifecycle

- `risk_decisions`
- `trade_lifecycles`

## Execution and positions

- `orders`
- `fills`
- `positions`
- `protective_orders`
- `trades`

## Operations and governance

- `market_snapshots`
- `account_snapshots`
- `portfolio_snapshots`
- `system_events`
- `audit_logs`
- `safety_incidents`
- `configuration_versions`

Each future module must write only the tables it owns through a repository/service boundary.

## Paper persistence and restart recovery

- `paper_account_state`
- `paper_order_state`
- `paper_position_state`
- `orchestrator_run_state`
- `orchestrator_fingerprint_state`
- `recovery_checkpoints`

## OKX Demo exchange mirror

- `okx_demo_balance_state`
- `okx_demo_order_state`
- `okx_demo_position_state`
- `okx_demo_algo_order_state`
- `okx_demo_sync_checkpoints`

OKX Demo remains authoritative. These tables are a local mirror used for audit,
inspection, and restart reconciliation; they are not used to invent exchange state.
