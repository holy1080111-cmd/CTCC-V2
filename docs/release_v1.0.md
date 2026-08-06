# CTCC V2 v1.0 Release Notes

## Added

- PostgreSQL persistence for Paper account, orders and positions
- Atomic paper-state snapshot writes with audit/system events
- Memory rollback when persistence fails after a mutation
- Startup recovery before WebSocket and scheduler startup
- Persistent orchestrator history and candidate fingerprints
- Recovery checksum and manual reconciliation API
- Database backup, restore and restart-verification PowerShell scripts
- Test-environment `NullPool` isolation

## Safety

- Exchange `AUTO_TRADE` and `LIVE_TRADING` remain forbidden
- Auto Paper execution requires persistence, WebSocket and automatic ticks
- Reconciliation that changes state is blocked while the orchestrator is running

## Not included

- OKX private authentication
- OKX Demo orders
- OKX Live orders
- Profitability claims
