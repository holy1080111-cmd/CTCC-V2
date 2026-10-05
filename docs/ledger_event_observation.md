# Bounded local event observation and atomic bound-route deduplication

`QualificationLedgerRepository.read_event_observation(scope, event_key)` reads
one exact event from the existing local Demo ledger. It includes all four
states: reserved, consumed, uncertain, and reconciled_flat. The existing
`_active()` list deliberately excludes terminal rows and is unsuitable as a
source of previously used events.

The method validates an exact environment/UID scope and a lowercase 64-character
hex event key, then takes the existing UID transaction lock and requested scope
row lock. The query matches environment, exact UID, and event key across all
settlement currencies. It returns at most two rows and defers the large stored
request JSON. It does not load the account's lifetime event set or paginate a
partial set into an apparent complete set. The existing index may still require
the database to inspect multiple account entries; this is a bounded returned
result, not a claim of constant-time query execution.

An absent requested scope is an error. Zero matching rows means only that this
exact query found no local row at the measured read. One matching row retains
its receipt, actual storage currency, state revision, original event identity,
and actual account/ledger revision pair. If the requested currency differs, the
observation separately retains the requested scope's revision pair. Multiple
matches are a cross-currency collision and fail closed; the reader does not
deduplicate or select a winner. Expiry and reconciliation never erase an event.

The versioned `LedgerEventObservation` binds the requested scope, exact key,
optional matching receipt, UTC request start, observation after lock acquisition,
receipt time, and native monotonic start/end. Types, version, UTC/monotonic order,
receipt identity, and both scope revision pairs are checked. Bounds describe the
queries while the UID transaction is held; they do not assert freshness after
transaction release. The canonical diagnostic document has a 64 KiB size bound
and SHA256 digest.

This DTO is always DENY. It has no source authentication, candidate issuance,
execution or order-retry authority, and is not registered with the legacy ledger
issuers. A caller-created or replayed DTO cannot replace an owned database read.
Unknown local account initialization is not a zero balance or a complete account.

The new `reserve_control_bound` route independently queries the exact UID/event
again under its existing UID -> current control -> qualification transaction.
Any matching row in any state/currency rejects the reservation. An earlier
absence observation, changed report ID, or different currency cannot substitute
for this check. The existing final reservation ID and database unique constraint
remain separate safeguards. The shared UID lock serializes competing bound-route
transactions without changing DDL.

This adds cross-currency deduplication to the repository-owned bound route; it
does not claim a new database-wide unique constraint for arbitrary SQL writers or
legacy bypasses. Old serialization and D0/native-order denials remain unchanged.
No order transport, account acquisition, Arm restoration, hold deletion, new
revision issuer, or migration is added.

The accompanying unit and real-PostgreSQL tests cover all states, terminal
tombstones, exact UID/key/currency selection, scope/clock/version rejection,
bounded query shape, a stale absence observation, renamed/cross-currency event
reuse, and concurrent bound reservations. These tests were authored during the
host thermal-recovery restriction and are **not yet executed acceptance**. Their
eventual results must be attached to the exact final source snapshot; earlier
control-bound or legacy counts cannot be reused for this slice.
