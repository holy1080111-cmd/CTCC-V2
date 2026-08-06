# CTCC V2 v1.1 Release Notes

## Added

- Authenticated OKX Demo REST client with request signing
- Mandatory simulated-trading header
- Read-only account connectivity and reconciliation
- Manual SWAP order placement with attached TP/SL
- Manual cancel, close-position, and leverage operations
- Local symbol, size, position-count, leverage, and protection gates
- CTCC API-token protection for private Demo routes
- PostgreSQL exchange-state mirror and Alembic revision `0004`
- Read-only and restart reconciliation verification scripts

## Deliberately unavailable

- automatic OKX Demo execution
- private WebSocket synchronization
- live execution

## Upgrade database revision

```text
0003 -> 0004
```
