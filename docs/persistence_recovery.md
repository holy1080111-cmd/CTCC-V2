# Persistence and Recovery Design

## Boundary

`PaperBroker` only handles deterministic matching, fees, PnL and protective exits.
It does not import SQLAlchemy.

`PaperExecutionService` owns mutation serialization, database persistence and
rollback-on-persistence-failure.

`PersistenceRepository` is the only component that reads or writes the v1.0
paper-state, orchestrator-history and fingerprint tables.

## Startup sequence

```text
Alembic upgrade
→ load paper state
→ restore broker
→ load orchestrator history/fingerprints
→ start OKX public WebSocket
→ optionally start Auto Paper Orchestrator
```

Auto Paper execution cannot start before recovery completes.

## Write policy

Immediate persistence:

- submit/fill
- cancel
- stop-loss/take-profit close
- manual close
- reset
- shutdown checkpoint

Throttled persistence:

- mark price and unrealized PnL without an order/position lifecycle change

## Consistency

Every persisted Paper snapshot has a canonical SHA-256 checksum. The recovery
API compares the current memory checksum with the PostgreSQL checksum.

If a state mutation succeeds in memory but PostgreSQL persistence fails, the
service restores the pre-mutation snapshot and reports a persistence error.

## Operator access

Paper, recovery, orchestrator, and realtime API routes require the local
`X-CTCC-Token`, including status reads. This prevents an unauthenticated caller
from changing Paper fills, prices, or recovery state. These routes do not grant
OKX order-write authority.

`scripts/verify.ps1`, `scripts/verify_recovery.ps1`, and `scripts/restore.ps1`
read the token from the current process's `CTCC_API_TOKEN` environment variable
or ask for it securely. `CTCC_API_TOKEN` must match the server's `API_TOKEN`.
They send it only in the request header. If no token is provided, the script
stops before a recovery mutation. Do not put the token in the command line,
source tree, or validation report.
