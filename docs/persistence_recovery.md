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
