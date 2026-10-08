# Owned G5 event and controlled-journal observation

`observe_owned_g5_event_ledger_v8` calls the unchanged source-owned V7 G5
preflight in the same task. Only after G5 has been replayed does it inspect the
existing PostgreSQL qualification journal twice. Each inspection independently
locks the exact Demo UID scope, checks its retention schema and transition
chains, and returns a bounded receipt. The V8 boundary checks the controlled
credential session and pinned plan before and after source work, binds the exact
UID/environment/settlement scope and event digest, and requires both journal
scans to agree on revision and row-chain fingerprints. The output contains only
hashes, a local ledger revision and fixed codes; it contains no UID, credential, account payload
or order request. V7 receipts and their hashes are unchanged.

An event found in any journal state, including a terminal tombstone, is reported
as `event_seen_in_controlled_journal`. An event absent from two agreeing reads is
reported only as `event_absent_from_controlled_journal_only`. That observation
does not prove the database's earlier history or legacy/exchange event history
was complete, and it cannot prove point-in-time source availability. A changed,
invalid, busy or unavailable journal is denied; cancellation propagates. All
V8 receipts have `admission=DENY`, `event_ledger_authenticated=false`,
`db_journal_complete=false`, `external_event_history_complete=false`,
`g6_evaluated=false`, and all candidate, G12, R7, reservation and execution
authority fields false.

The scoped observation is limited to the configured qualification database;
the DB alone does not authenticate a complete history of all prior routes. The
V8 boundary is not mounted in a scheduler, router or order transport. Its
tests use synthetic G5 receipts and journal inspections; they do not establish
trusted Demo capture, an authenticated all-state consumed-event ledger, G6,
G1–G12, R7 or live execution eligibility. The next authority-bearing step still
requires independent historical availability and account/legacy/exchange
reconciliation before G6 can be evaluated.
