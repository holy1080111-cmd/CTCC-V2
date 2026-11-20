# Live financial source and prospective risk epoch

This is engineering source. The epoch schema is an additive SPEC and is not
installed by the current migration merely because these modules exist. Actual
isolated PostgreSQL acceptance and explicit owner migration/model registration
remain required. No venue call, operator Arm or real-money acceptance is implied.

The Live reader keeps the existing production GET lease, exact account UID and
main-UID relationship, native TLS custody, raw bytes, page chains and no retry,
redirect, proxy or simulated-trading header. Its flags remain off. Demo records
and Demo authority are never converted into Live evidence.

The bridge queries complete archive windows of seven to 28 days and a separate
one-day recent overlap. Each endpoint retains its own documented retention and
cursor. The new positions-history source uses `uTime`, including all equal-time
rows in source order. It requires an explicit terminal empty page. Duplicates,
nonprogressing cursors, malformed rows and capacity limits refuse the source.

After the initial account capture, seven fresh fill/bill partitions cover the
tail through a measured cutoff. A second complete current capture must have the
same explicit balance update times and economic fields as the first, and its
update times must not exceed that cutoff. A changed revision refuses this
invocation. It is never repaired by a hidden retry or by replacing timestamps.
Both original current banks and all tail pages remain in the source digest.

Financial arithmetic uses each fill's execution time, signed fee and actual
realized PnL. Trade bills corroborate the same fills and are not added again.
Funding bills represent booked payments at their balance-update time. That time
is never called funding accrual. Actual closed-position `fundingFee` aggregates
corroborate controlled lifecycle outcomes. Unknown fields, mismatched amounts,
unsupported products/currencies, liquidation, unresolved order chronology or an
unmatched lifecycle refuse the window. Principal transfers are retained
separately and are never reported as trading PnL.

There is one immutable prospective controlled epoch per exact Live UID.
Enrollment requires an authentic flat source, stopped/disarmed local Control,
and no previous local Live reservation or intent. Its known-empty prospective
population starts at the actual measured enrollment time. This does not claim
a zero loss streak for the account's earlier lifetime. Daily and rolling-seven-
day cashflow losses before enrollment remain included. Enrollment does not
reset them.

The high-water mark is the maximum of every retained native equity sample in
this epoch. It is explicitly a prospective sampled high-water mark, not an
all-time account peak. Every sample is append-only, chained to the preceding
sample and independently read back after one commit. Unknown commit/readback
preserves the prefix and permits no reset, deletion, Arm or submission retry.
The current bounded prefix reader refuses more than 1,024 samples or 64 MiB;
capacity is a denying condition, never prefix truncation or rollover.

The collector has a distinct restricted PostgreSQL role. Its only grants are
schema usage, SELECT on version metadata/the four Live ledger tables/the five
legacy Live intent/projection/latch tables/the two epoch tables, and INSERT on
the two epoch tables. It receives no UPDATE, DELETE,
TRUNCATE, Arm, stop-clear or execution-function grant. Preflight checks actual
head, owner/role/TLS, effective ACL including MAINTAIN and grant options,
columns, keys, indexes, append-only guard and backend-normalized CHECK bodies.

The original legacy global lock `1680010` is retained as a transaction lock.
Any unresolved or malformed legacy intent, tracked nonflat position, pending or
unknown ordinary/algo order, failed checkpoint or persisted safety latch
refuses enrollment, qualification and final physical dispatch. These legacy
tables lack an exact account UID, so no account identity is guessed. The checks
are global and negative only. Nothing clears or deletes legacy state.

Epoch and sample bytes also carry domain-separated native HMAC seals. Only the
actual held collector transaction and original Live credential lease can issue
the fixed, freshly recomputed documents. The signer accepts no arbitrary
payload or callback, and returns no key material. Every prior seal and original
fill/bill/position/funding operand is replayed before a new sample is adopted.
Rehashing collector-written JSON does not authenticate it. Credential, site,
account or database continuity changes refuse the prefix until an explicit
reviewed continuity procedure preserves it; there is no automatic reset.

The coordinator calls `preflight_live_financial_collector_v1` before acquiring
the short-lived original Live source, then
`observe_owned_live_financial_epoch_v1`. Only its original same-task opaque
owner can be replayed by the private financial-source consumer. Saved DTOs,
JSON, receipts, a constructor key or caller completeness booleans grant nothing.
Public receipts retain `account_complete=false`,
`qualification_verified=false`, `execution_authority=false` and `admission=DENY`.
The canonical Live qualification coordinator must separately combine the
source-owned financial facts with G1–G12, fresh recheck, current risk, durable
intent and explicit operator one-shot Arm.

The balance observation, epoch genesis and HWM sample time are the original
balance-page body-completion time. Availability is separately the final whole
capture completion; neither timestamp is replaced with validation time.
Facts expose the actual availability time, record cutoff and exact timestamped
cashflows so the final gate can recompute UTC-day/rolling-seven-day loss at its
own current time. A new eight-day query window is recommended; the gate must
still check actual coverage and freshness. Current facts are reconstructed from
the original sealed sample on every opaque-owner replay, and outcome payloads
are immutable bytes. Mutating a returned DTO cannot change the owner's source.

Endpoint semantics were checked against the
[official OKX API documentation](https://www.okx.com/docs-v5/en/), especially
[positions history](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history),
[fills](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-days)
and [account bills](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-7-days).
