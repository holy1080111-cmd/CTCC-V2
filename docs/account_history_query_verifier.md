# Observed account history query verifier (B2a)

`collect_bootstrap_query_verified` uses the existing controlled account session,
collector and B1 journal. After acquisition and durable finalization, it reads the
whole journal in another database session and calls
`verify_history_query_chain`. No account request is added or retried by B2a.

The new `ctcc.demo_bootstrap_query_observation.v1` public receipt binds the B1
head, packet, plan, policy and private verifier result by hashes. It contains no
UID, query rows or credential values. Both result objects and receipts retain
`account_complete=False`, `execution_authority=False` and `admission=DENY`.
The private result also keeps source authenticity false. A historical replay,
caller boolean or result object cannot create current acquisition ownership.

## What is actually recomputed

The verifier requires an externally pinned terminal head and packet/plan identity.
It revalidates every B1 readback and its source/DB/readback clocks, exact sequence
and previous hash, byte bounds, stage order and successful finalization. Every
packet observation must join to the original request, validated receipt and
durable raw-finalized bytes. Partial prefix hashes are recomputed against actual
raw bytes. Request/query/page/row identities, EOF/receipt times, secret-scan
completion and packet checkpoint bindings must agree. Missing, withheld, failed
or owner-unknown captures cannot produce a positive query result.

The existing account packet parser independently replays its original raw bytes,
config identity, exact query inventory, endpoint-specific cursor and page chain.
An explicit empty terminal is still required after short pages. No source rows
are sorted, repaired or dropped to disguise malformed input. Equal overlap is
represented by one derived row identity with **all original locators** retained;
different bytes for the same family/bill ID fail. Private raw source stays in B1.

The fixed policy `ctcc.global_observed_history_query_policy.v1` supports Global
Demo v3 SWAP and v4 standard-product fills/bills queries. Its supported archive
lookback is at most **28 days measured at actual capture completion**. This is
an implementation bound wholly inside the documented three-month retention,
not a replacement claim about OKX retention. Each recent fill query contributes
only its three-day interval; recent bills contribute seven days. Archive coverage
is combined with recent coverage separately for each applicable family/product.
A short recent interval therefore does not truncate a valid archive interval.

The API reference currently documents fills as three days, fills-history and
bills-archive as three months, and bills as seven days. The verifier uses `ts`
for these filtered queries and retains actual `fillTime` separately. [OKX API
reference](https://www.okx.com/docs-v5/en/). Timestamp filtering and cursor
pagination are separate operations. [OKX pagination
guide](https://www.okx.com/docs-v5/trick_en/#pagination).

Order pages remain part of exact packet/journal replay, but their retention and
creation-time semantics are not certified by this slice and never substitute
for realized PnL. Unsupported region or a query beyond the implementation window
denies verification. No other region is silently substituted.

## Scope of a positive query result

`retained_query_chain_verified` means that the stated source generation-time
query, as recorded, has been replayed through its terminal and its retained
interval derived. It does not mean that all executions before a cutoff have
already appeared, or that no later correction can arrive. It does not establish
loss windows, lifecycle grouping, the starting loss streak, funding accrual,
high-water marks, complete liabilities/product coverage or account authority.
Those facts remain explicitly unverified. A recorded empty query is not an
account-inception assertion or a zero risk seed.

`compare_history_query_chains` independently replays two pinned journal chains.
It records matching/conflicting overlap, a newly observed row inside a previously
covered interval, and a formerly observed row missing from the current covered
interval. It retains both locator sets and does not overwrite either observation.
Conflicts, late rows and unexplained missing rows require reconciliation of any
dependent current claims; the comparison itself never publishes claims. A v3/v4
comparison also records product domains present in only one observation and
requires reconciliation, even when that domain has no rows. This prevents an
empty v3 SWAP query from being treated as coverage of the other v4 products.
Derived interval union merges only overlapping/touching measured intervals;
fractional retention boundaries cannot bridge an uncovered millisecond. Reading
the same capture twice is an audit comparison, not a fresh observation.

The runtime consumes the internal owner created by the original acquisition,
then finalizes and reads it back. The older B1 public result and bytes are
unchanged. A late verifier/readback failure preserves the successful B1 journal;
it cannot retry the consumed session, publish an account revision, retire a hold,
clear EStop or submit an order. No migration or additional database write was
introduced by this slice.

## Measurement policy remains explicit

The user requires a sourced peak/DD window, a valid loss-streak seed, complete
required loss windows and an account revision. These do not universally require
an unsampled global equity maximum, all account history since inception or an
exchange-wide atomic revision. Old recorded claims remain unknown. A later
version may define a persistent measured-HWM policy and a locally issued
revision over verified bounded observations; it must not relabel those as the
stronger claims, erase losses or change an accepted window on restart.

A source-verified strictly positive **net** completed outcome resets the current
loss streak under existing portfolio rules. It does not prove the old
`loss_streak_at_history_start` when the reset lies inside the required loss
window. Keep an earlier genuine seed or introduce a separate versioned reset
anchor; never fill the old seed with zero or omit daily/7-day outcomes. Bill
generation time remains distinct from funding accrual. B2a implements neither
reset/lifecycle nor HWM issuance.

## Validation

Unit tests use the existing B1 owned MockTransport acquisition and memory
repository fixtures, with source-byte/metadata mutations, unsupported scope and
retention boundaries, actual-time-domain differences, cross-capture discoveries,
separate-read failure and exact old/new byte comparisons. They do not count as
native TLS, real account, PostgreSQL or full account acceptance. The dedicated
PostgreSQL module must run in the isolated migrated test environment; collecting
it or skipping it cannot be reported as execution.
