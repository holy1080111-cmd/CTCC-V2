# Observed execution cashflow index (B3)

B3 replays immutable DB0020/B1 captures through the existing B2a source verifier
and appends an independently replayable observation batch in DB0021. It records
the execution-time cashflow rows actually observed, including partial realized
losses. It does not issue a complete risk snapshot, account revision, Arm,
credential capability, reservation, submission or execution authority.

Every output remains `account_complete=False`,
`source_authenticity_verified=False`, `execution_authority=False`,
`admission=DENY`. A valid empty observed fill query can yield a zero *observed
fill subtotal* only when its named query/window policy is satisfied. That zero
is never a complete account net-loss, funding, lifecycle, streak or HWM claim.

## Preregistered bounded policy

`account_observation_index.POLICY_BYTES` and its SHA256 bind policy v2:

- Global Demo; existing pinned B2a v3 SWAP or v4 product query scope, explicitly
  retained in coverage. No region fallback or promotion from SWAP to all products.
- Maximum seven-day inclusive execution window; generation queries must cover
  that start through the latest generation cutoff, which must end at least 300
  seconds after the execution window. Query cutoff age at capture completion is
  at most 120 seconds. The 300 seconds is a chosen measurement policy, not a
  documented guarantee of publication latency or future finality.
- Consecutive observed captures must overlap generation windows; a gap over one
  hour in capture observations is reported as incomplete. All older captures
  remain available for nonadjacent conflict/late/missing comparisons.
- Durable sequence is a positive BIGINT. There is no 32-observation lifetime
  limit. Each proof has a 32-related-capture, 16,384-row, 64 MiB original-source
  and 8 MiB receipt budget. Persistent normalized facts preserve every accepted
  source identity, variant, metadata observation and original locator. Coverage
  intervals and unresolved findings remain append-only across all pages.
  Exceeding the related proof budget appends the current independently verified
  source membership and a named incomplete finding; it never truncates old
  evidence, resets sequence, or produces a subtotal.
- All late, conflict, missing or domain-change findings remain appended and
  invalidate prior observations for dependent current claims. This version has
  no automatic reconciliation/reset procedure to clear those findings.

`fillTime` determines trade occurrence; generation `ts` remains separate. A
new generation interval containing a fill from a prior mature execution interval
invalidates the earlier observation even when B2a correctly calls the generation
row new. Repeated identical identities preserve all original locators but count
once. Conflicting variants remain visible and prevent any aggregate.

For supported settlement-fee-currency SWAP rows with captured linear metadata,
the exact Fraction subtotal is the exchange-reported `fillPnl + fee`; the signed
fee already includes a rebate when positive. No separate rebate is invented.
Missing `fillTime` retains its source row and makes the subtotal unknown.
Missing financial operands, unsupported currency/product or absent metadata
remain explicit incomplete conditions. Metadata is recorded for each source
appearance; a later missing or conflicting instrument definition cannot reuse
the first supported flag. Regressing the generation cutoff is incomplete even
when both cutoffs are individually fresh. The subtotal is not a full lifecycle
calculation, price-derived independent PnL proof or funding-adjusted net outcome.
Funding bill `ts` is never relabeled as accrual time. No streak reset/seed, HWM,
inventory/protection or full net-loss acceptance is issued here.

## Durability and replay

The repository accepts exact source reference pins, scope, explicit execution
window and expected append revision. It does not accept caller cashflows,
coverage, passed/complete flags or supplied verifier results. It reads the
original DB0020 chains, replays B2a and recomputes each requested batch. SQL
searches all historical identity/variant records and coverage intervals to
select related witnesses. Every selected witness is replayed from its original
B1 raw/page chain and its exact normalized membership is checked. A SQL absence
is only a witness-selection hint; the complete original query proves absence.
The continuation anchor binds prior event/anchor, cumulative fact/coverage/
unresolved-finding counts, current membership and verified witness membership.
It is rebuilt on separate-session readback. An opaque prefix hash does not
replace any selected source proof. Persistent unresolved findings cannot be
cleared by changing pages or replay windows. `read(after=..., limit=...)` returns
up to 32 batches and supports continued paging without resetting sequence. It
rechecks the expected chain under the existing UID lock, commits one append,
then opens another session and replays the committed source again.

DB0021 binds each append to its complete DB0020 terminal via a foreign key and
trigger, protects scope/sequence/hash/no-authority metadata, and rejects UPDATE,
DELETE and TRUNCATE on batches and normalized tables. Deferred membership checks
bind each complete contiguous ordinal set to its canonical array hash and count;
missing rows and late additions cannot change an accepted batch. Downgrade
refuses any nonempty observation table. DB protection
is integrity protection, not source authenticity: even a rehashed direct SQL
attempt still cannot become current runtime authority and must survive full
source replay on read. Existing account/ledger revisions and safety latches are
not changed. TTL never releases a reservation or intent.

`collect_observed_execution_window` is a new owned runtime wrapper. It retains
the actual B1 owner through acquisition and finalization, pins readback to the
original plan/session/packet/capture, and appends only source-derived evidence.
Failure after B1 or B3 commit retains that durable evidence and never retries
the used session. A stored B3 receipt cannot be passed as an owned session.
Old B1/B2a wrappers, their wire bytes, materializer v1 and all trading boundaries
are unchanged.

## Validation scope

Source replay is bounded separately from comparisons. Within one pure B3 replay,
each of the `n` original B1 chains is verified by B2a once; all `n(n-1)/2`
comparisons then use those internally verified receipts without rereading raw
chains. The private comparison preserves the old B2a comparison bytes and
scope/coverage/order checks. No caller receipt and no cache across invocations
is accepted. At the 32-capture proof bound this reduces B2a chain-verifier calls
from 1,024 to 32 in that pure replay. Repository source-membership and separate
readback checks still independently reverify their own inputs; this is not a
claim that an entire append uses only 32 calls. For a stable `n`-witness append
plus separate readback the expected B2a call count is `4n+1`, excluding retry or
concurrent-conflict paths. The 37-capture persistence test remains required and
was interrupted by host thermal shutdown before this optimization; its former
partial progress is not a passing result.

Tests distinguish synthetic owned acquisition from native TLS and real account
acceptance. Adversarial cases include generation vs actual execution time,
late new-generation records, partial losses, repeated overlaps, nonadjacent
conflicts, missing rows, changed scope/session/pins, strict row types, missing
operands, unsupported currency, named observation tail, replay after restart,
concurrency, immutable database operations and late readback failure. Unit tests
preserve exact old B1 events under the new wrapper. Genuine credential/account
capture is not performed by these tests.
