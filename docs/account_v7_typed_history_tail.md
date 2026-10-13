# Typed-time V7 Demo history tail diagnostic

`join_recorded_account_sources_v2` is a separate, DENY-only ordered V5-history
and V7-current join. Its `ctcc.recorded_account_current_history_join.v4`
policy replays the original B1 history chain through the V2 typed-time query
verifier and pins that query receipt. It retains the recorded history-terminal
database timestamp prerequisite and exact account, session, mode, currency,
registration and local-checkpoint continuity. Its coverage is explicitly
`history_requested_source_ts_coverage`: fill `ts` is record generation time;
bill `ts` is balance-update completion time. The old join v1-v3 entrypoint,
policy hashes and receipt fields remain unchanged.

`verify_v7_history_tail_diagnostic_v2` issues the separate
`ctcc.demo_history_tail_followup_current.v3` receipt. It replays four distinct
original B1 chains, both V2 ordered joins, both V2 history queries and the new
typed V2 comparison. It binds the comparison policy and query receipt hashes,
preserves late/missing/conflicting findings and disjoint empty-query intervals,
and requires the same eight Algo current chains and source chronology as the
older V7 tail. `verify_v7_history_tail_receipt_v2` readbacks only this new
receipt type against the four original chains. The old V7 v2 receipt type and
replay entrypoint cannot accept the new receipt, and the new entrypoint cannot
accept an old one. No database schema or migration is needed for this pure
diagnostic.

Both versions keep `history_tail_closed=false`, `account_complete=false`,
`flat_start_permission=false`, `execution_authority=false`, `snapshot=null`
and `admission=DENY`. Measured bounded query overlap cannot prove an
exchange-wide atomic end of history, future late-arrival finality, funding
accrual chronology, complete loss seeds, measured historical high-water mark,
registration-site authority, or current native/locked account freshness.
Synthetic unit tests verify the new path; they are not authenticated Demo or
PostgreSQL acceptance.
