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

## Typed source-time replay and separate DB0040 persistence (diagnostic only)

`replay_observation_index_typed_v1` is a separate, pure B3 replay from the
original pinned B1 journal chains. Its `TYPED_POLICY_SHA256` binds the B2a V2
query policy and emits `ctcc.observed_account_execution_window.v3`. Each capture
binds both the recomputed B2a V2 receipt and its independently derived legacy
V1 receipt hash. It refuses a V1 query result, policy substitution, missing
predecessor hash, wrong family time domain, integer-as-boolean coverage, or a
legacy `generation_at` row. Pair comparisons use the family-specific
`source_ts_at` and issue `ctcc.observed_history_query_comparison.v3`; they retain
late, conflicting, missing and disjoint-interval evidence. Actual fill
cashflows still use `fillTime` for occurrence and do not use `ts` as execution
time. A bill `ts` is balance-update completion, never funding accrual.

This V3 window receipt is **not** accepted by the DB0021/DB0037 B3 append path
or any current risk/execution consumer. DB0040 adds a separate typed B3 chain,
with separate normalized fact and coverage tables, its own append-only sequence
and a distinct persistence policy. The new table stores `source_ts_at` and its
family-specific meaning: fills `ts` is fill-record generation; bills `ts` is
balance-update completion. `fillTime` alone is the observed trade occurrence.
No bill timestamp is funding accrual. The DB0040 SQL guard binds the original
B1 terminal, DENY fields, policy hashes, membership and typed time domains;
the repository independently replays original B1 chains and B2a V2 with its
V1 predecessor hash before post-commit readback. DB0040 does not migrate or
rewrite the DB0021 chain. Backfilling any old capture into the typed epoch
requires an original B1 replay and a fresh typed sequence, never a copied old
receipt or a bridge from the old B3 head hash.

The V3 pure-replay policy expressly does not authorize persistence by itself.
DB0040's separate persistence policy wraps the diagnostic receipt and keeps
`account_complete=False`, `source_authenticity_verified=False`,
`execution_authority=False`, and `admission=DENY`. Existing portfolio,
current-history, qualification, risk and execution consumers still use their
prior contracts and acquire no new permission. PostgreSQL migration and
repository acceptance must pass on the exact final source before this new
durable path is considered validated.

## Preregistered bounded policy

`account_observation_index.POLICY_BYTES` and its SHA256 bind policy v3. The
diagnostic execution-window receipt is V2; prior V1 receipts retain their
original bytes and remain historical DENY evidence. A V1 receipt or V2 policy
pin cannot be promoted through the V3 replay:

Persisted DB0021 batches from the prior B3 policy are **not** migrated by this
change. The append path checks the last persisted predecessor's policy hash
before deriving or committing a new batch and rejects a V2→V3 mixed prefix with
`observation_policy_epoch_mismatch`. Old rows and their B1 source journal stay
immutable; current-policy readback and lifecycle replay fail closed on those
rows. Starting a new V3 epoch or migrating a V2 prefix requires a separately
reviewed, versioned design and database validation. Until then, an account
with a V2 prefix cannot use this B3 index for current risk qualification.
DB0036 introduced the database append guard for **new** B3 rows: it requires
the V2 execution-window receipt, the exact reviewed V3 policy hash, and a
same-policy predecessor. Its historical f-string rendered the 64-character
membership-SHA regex as `^[a-f0-9]64$`, so valid new observations were denied.
DB0037 replaces that function under the same writer-exclusion lock; its only
SQL change is `^[a-f0-9]{64}$`. It leaves DB0036 and stored evidence untouched,
retains the policy/epoch checks, and refuses downgrade when any V2 receipt has
been appended. This repair does not solve the old-prefix replay blocker or
grant account authority. Exact-source PostgreSQL validation remains required.

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
- B3's private pair comparison now matches B2a comparison V2 exactly. For the
  immediately preceding capture, shared family/product domains with no common
  **effective measured** generation interval create a bounded
  `effective_generation_overlap_gap` finding with both interval sets. That
  finding blocks an observed subtotal and remains available for durable
  reconciliation. A disjoint nonadjacent pair does not create this finding
  when original middle captures bridge the adjacent intervals.
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

`collect_observed_execution_window_typed_v1` is a separate owned entrypoint for
the DB0040 typed epoch. It checks the one-use controlled Demo session and the
global history plan before I/O, finalizes the original signed B1 page chain,
reads that committed chain in a new database session, and replays B2a V2 from
its original bytes before asking the typed repository to append. DB0040
independently replays the same source and reads its commit back in another
session. The old V1 runtime and DB0021 chain are untouched. This route only
records typed fill-record and bill-balance-update observations. It never turns
an order's creation time into fill time, a bill's `ts` into funding accrual,
or an observed subtotal into a complete loss window. A late failure preserves
committed B1/DB0040 evidence and cannot repeat the consumed signed session.
The return remains `account_complete=false`,
`source_authenticity_verified=false`, `execution_authority=false`,
`admission=DENY`; it does not establish registration region, history finality,
streak seed, HWM, portfolio snapshot, or Demo authority.

## Validation scope

Source replay is bounded separately from comparisons. Within one pure B3 replay,
each of the `n` original B1 chains is verified by B2a once; all `n(n-1)/2`
comparisons then use those internally verified receipts without rereading raw
chains. The private comparison preserves the current B2a V2 comparison bytes and
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
