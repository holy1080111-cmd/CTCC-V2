# Same-frame original G5 and controlled journal observation

`inspect_owned_original_event_journal_v10` is an opt-in read-only diagnostic for
the four base strategies. It calls the private original-source owner once. The
owner retains the exact raw public and account packets, verified precursor and
fixed G5 event in one task while it performs two bounded, locked PostgreSQL
qualification-journal reads. After the reads it replays the same raw precursor
and G5 event, checks the original source lifetime and returns only canonical
hashes and fixed status codes. No raw account identity, credential or packet is
returned. Existing V7 and V8 entry points and receipt bytes are unchanged.

The status distinguishes a known event in any journal state, including a
`reconciled_flat` tombstone, from an event absent only in the inspected
controlled journal. A changed, invalid, busy or unavailable journal cannot be
reported as absence. Cancellation propagates. The source may still be denied
before I/O because real Demo registration-region provenance has not been
proved; synthetic tests do not supply that proof.

This join **does not** authenticate all-state consumed-event history. The DB
inspection reports `db_journal_complete=false` and
`external_event_history_complete=false`: older erased history, legacy submit
routes and exchange orders are not proven by this local read. Historical
first-availability, complete account/portfolio risk and protection also remain
unverified. Every V10 receipt therefore has `admission=DENY`,
`event_ledger_authenticated=false`, `g6_evaluated=false`, and all candidate,
G12, reservation, execution and order authority flags false. It is not mounted
in a scheduler, router or order transport.

Before a positive G6 can exist, a separately reviewed source owner must prove
the complete event-consumption epoch and reconcile legacy and exchange order
history to the exact Demo UID, then recheck the account revision atomically
with reservation. An empty V10 journal result is never that proof.
