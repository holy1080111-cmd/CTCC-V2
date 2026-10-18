# B5 diagnostic portfolio components, version 2

## B3→B5 same-invocation sampled-HWM bridge (version 3)

`collect_owned_portfolio_components_v3` is an additive, DENY-only diagnostic.
It first initializes the local scope and, under the exact account lock, checks
that the existing B3 and B5 tails are both empty or have the same sequence and
event hash. A nonempty B5 tail receives an independent-session original-source
readback. This happens before any new B1 account GET. A B3-only or misaligned
scope stops here; version 3 never invents a later genesis or silently catches
up historical samples.

Version 3 then uses the original B1 acquisition and guarded B3 append. The
guard rechecks B5 alignment inside B3's write transaction after the intervening
network reads, so a newly created gap prevents this invocation from committing
another B3 row. It then calls the exact DB0044 measured-HWM repository with the
just committed B3 sequence and head. The B5 append commits under the account
lock and performs its own separate-session readback. Version 3 opens one further
separate readback and compares both results to the independently folded complete B3
population. It checks scope, sequence/head, genesis and last sample times,
sampled peak amount/time/source digest, and the recorded native-transport flag.
The v1 and v2 membership digests use different algorithms and are not falsely
compared as equal.

The version 3 receipt pins the B5 projection digest and the matched B3 head.
The caller cannot supply a B5 receipt or measurement; the B5 repository must
share the original session factory. A missing predecessor, changed B3 head,
inconsistent projection, failed DB readback or late failure denies the result.
Previously committed B1/B3/B5 evidence remains for audit after that failure.
Version 2's policy, schema and bytes remain unchanged.

The B3 write guard is opt-in for this version 3 route. Other legacy B3 writers
may still create a gap outside this invocation; the next version 3 preflight
then refuses new acquisition until the separate bounded historical B5 catch-up
described below reaches the exact B3 head. An aligned prefix grants no trading
permit.

This verifies consistency of **sampled** balances through the B3 head. It
does not prove a continuous peak between reads, historical native-clock
attestation, the required drawdown-window origin, complete account history,
current account identity/revision or protection. The result still has
`owner=None`, `snapshot=None`, `account_complete=false`,
`execution_authority=false` and `admission=DENY`. Synthetic unit cases check
the join mechanism; only isolated PostgreSQL tests can check DB0044 durability,
and neither kind of test is authenticated Demo acceptance.

The injected-clock entry now emits `ctcc.portfolio_components_diagnostic.v2`
under a new pinned diagnostic policy. It always returns `owner=None`. Native TLS
transport, injected UTC ordering and native-clock attestation are separate facts.
This corrects the V1 consumption gap without rewriting V1 artifacts or changing
the original B1 journal, measured balance or HWM reducer contracts.

This slice connects actual B1 acquisition, B2 source replay, the immutable B3
observation index, B4 current inventory, captured contract specifications and
read-only local database state. It does not publish `AccountLedgerClaims`, a
`PortfolioRiskSnapshot`, an account revision or an order permission. Those
outputs remain absent while complete lifecycle outcomes, funding attribution
and a valid streak seed are unknown. A verified component can be true without
turning the complete account into a PASS.

`collect_owned_portfolio_components` accepts an unused controlled Demo session,
the three concrete repositories sharing one session factory, a barrier, the
B3 window and expected revision, and a pinned configuration profile. The
profile may contain exact scope and preregistered correlation assignments. Its
instrument evidence, cost evidence, history, peak and ledger fields must be
empty. There is no packet, result DTO, balance, peak or authority input.

The function acquires new B1 source internally. It checks the actual private
journal owner and its first event, terminal, packet, plan and local checkpoint.
It appends the original capture to DB0021 and separately reads every accepted
balance sample through that fixed head. Original journal bytes remain if a
later step fails or times out. Readback alone cannot mint current ownership.
Neither this injected-clock acquisition nor historical readback can register a
current `OwnedAccountComponents` token. Genuine TLS does not attest the caller
clock. The V1 rule checked a stored callback for expiry and lacked task/loop/PID/
thread identity; a callback frozen after acquisition or a borrowed original token
could defeat that current/same-invocation claim. V2 removes that issuer path.

The private consumption fence removes the owner before checking the digest and
requires a separately registered private native-clock boundary. It burns that
boundary on failure too. A future boundary must bind the exact private invocation,
parent task, loop, PID and thread, reject parent cancellation, independently read
the existing reviewed native OS sampler, and check both original UTC expiry and
monotonic deadline. Constructed handles, callbacks, dictionary attestations,
historical receipts and old owners have no such registry admission. This version
has no native boundary issuer. A hash alone cannot satisfy that missing issuer.

## What is actually measured

For each accepted capture, `observe_balance` replays the original B1/B2 bytes,
checks the exact UID, region, environment, currency and account mode, and reruns
the existing cross-source account consistency verifier. The supported balance
is the original single USDT currency row in global Demo account level 2.
Top-level USD equity is never substituted. Missing equity, available margin,
liability or borrowed-funds evidence does not become zero. The raw row, hash,
page receipt, body hash, measured times and immutable capture reference remain
bound to the measurement.

Original account and currency `uTime` are retained separately. An unchanged
old `uTime` can accompany a newly received response; it is never rewritten as
the body receipt. Missing, malformed or future `uTime` is rejected in this
supported measured-balance policy.

The measured HWM policy is `ctcc.measured_balance_hwm.v1`. Its population is
every accepted measurement from immutable scope sequence 1 through an explicit
pinned head. Genesis is sequence 1's original balance body-completion time.
The maximum uses exact rational amounts. Repeated exchange `uTime` does not
remove a measurement, a later lower balance cannot erase a prior peak, and
missing/reordered prefixes or a wrong final head fail. Each sample contributes
its accepted sequence, predecessor, batch hash and original measurement hash
to the ordered membership digest.

This is the maximum of the accepted samples. Unmeasured intermediate peaks are
unknown. It makes no continuous or all-time exchange high-water claim. An
existing risk policy requesting an earlier or different drawdown window remains
unmatched; this version does not reset genesis or change the risk policy to
make it match. No existing materializer or historical artifact was rewritten.

The unchanged HWM V1 field `all_sources_recorded_native` describes recorded TLS
transport only. The V2 wrapper exposes it as `sampled_hwm_original_tls_recorded`;
`native_sampled_hwm_verified` remains false without each original sample's durable
native-clock companion. Historical injected timestamps cannot be relabelled as
native measured time. Missing historical clock attestation is unknown, even when
every original TLS flag is true.

## Current inventory and local state

B4 verifies the actual current account packet including complete pending page
chains. The first supported branch requires observed empty positions, ordinary
pending and all supported algo inventories. Nonflat inventory requires a
separate source-complete protection and exposure producer and is denied here.
The contract rule helper independently rereads the actual captured instrument
rows, exact units, tick, lot, minimum and multiplier.

`read_portfolio_checkpoint` holds the existing environment/UID advisory lock
and reads every currency scope and every unresolved hold. Consumed intent
transitions remain unresolved until their own reservation is explicitly
reconciled flat; expiry never erases them. Legacy tables lack UID attribution,
so all legacy active or unknown state is retained as an account-blocking
condition. Missing automation or reconciliation initialization is unknown.
Armed, locked, error, unfinished run or tracked exposure state blocks the flat
component. EStop is preserved as recorded and is not cleared by an empty
inventory result.

The legacy reconciliation checkpoint must contain an exact lowercase 64-hex
credential-session binding ID as well as the exact-UID hash. A missing or
malformed binding is `legacy_reconciliation_credential_session_unbound`, so
the local checkpoint cannot report `known_flat`. The controlled Arm preflight
also validates this field before asking the exchange to reconcile. A previous
valid session can remain in the first local read; the fresh pinned read session
must match the exchange reconciliation result and the second persisted local
read exactly. This is only a sampled local flatness prerequisite, not account
completeness or execution authority.

Short SHARE locks give a consistent individual legacy-table read. Matching
before/after checkpoints detect observed changes. They do not prove future
writer exclusion or an atomic exchange revision. The final execution boundary
still has to reread its actual authority and reserve under its own transaction.
The DB observation checkpoint is not a global exchange account revision.

## Limits and incomplete risk history

The diagnostic invocation has a 30-second cooperative deadline. Its recorded
UTC ordering is checked after all replay work, but this remains a declared
injected-clock calculation. The diagnostic expiry is no later than either the
first relevant current response receipt plus 30 seconds or invocation start plus
30 seconds. It grants no current owner. Cancellation or timeout preserves
previously committed source rows. Native time ownership additionally requires
the independent clock proof described in [the native-clock contract](account_native_clock_proof.md).

SQL pages contain at most 32 accepted balance samples and the sequence uses the
existing BIGINT domain. There is no account-lifetime 32-capture cap, new genesis,
history deletion or prefix omission. However, this correct first implementation
replays **all** original accepted B1 captures on each call. Memory is paged;
total replay work is O(number of accepted captures), not bounded by page size.
Once that work exceeds the freshness deadline the current owned result is
denied. No sustained-load or production-latency acceptance is claimed.

B3's observed fill cashflow is not a completed trade outcome. Partial closes,
funding generation versus accrual, fees/rebates, lifecycle closure and streak
reset must be proven before deriving the UTC-day and rolling-seven-day loss
model. `loss_history`, `loss_streak_at_history_start` and `snapshot` remain
`None`. A win inside a window does not invent the window's preceding seed.

## Incremental HWM continuation required for sustained operation

The next additive storage contract should persist one immutable, normalized
balance-measurement fact per accepted capture, referencing the original B1
terminal, packet, raw/page/row locator, measured time, raw update times and exact
equity. The initial extraction must replay its original source. Its immutable
continuation projection must bind the previous projection, contiguous sequence,
genesis, ordered membership, previous maximum and the selected maximum's
original witness. It must retain any invalidation, unknown or policy mismatch.

The isolated `account_measured_hwm_continuation` module now implements the
calculation half of this contract. It uses a new, domain-separated V2 membership
chain; it does not reinterpret or overwrite the V1 SHA256 of concatenated
members. Given a pinned preceding projection, its sequence and source head, the
original genesis and selected-peak measurements, and the newly verified source
measurement, one append uses bounded memory and work. A missing witness,
changed predecessor pin, gap, reversed measured time, or changed peak fails.
The V2 peak agrees with a full V1 replay for the same synthetic population, but
the membership digests intentionally differ. Every V2 output says
`durable_membership_verified=false`, `native_clock_verified=false`,
`account_complete=false`, `execution_authority=false`, and `admission=DENY`.

This pure calculation is not yet connected to B5 runtime. Its new DB0044
diagnostic storage has not passed PostgreSQL or cross-process acceptance; no
source-binding issuer, partition rebuild or native historical clock proof exists.
The current runtime therefore still performs its full V1 historical replay and
may deny at 30 seconds. A caller-supplied hash or arbitrary suffix cannot
substitute for the missing source and persistence evidence.

### DB0044 diagnostic persistence under review

DB0044 adds a separate append-only projection keyed by the exact DB0021 scope
and sequence. The repository reconstructs the current, original sequence-one
genesis and selected peak measurements from their DB0020 B1 chains under the
account UID transaction lock. It commits only one contiguous tail at a time and
performs a separate-session source readback. A missing predecessor or any
source mismatch denies; it never chooses a later starting balance. Its stored
V2 receipt and returned wrapper retain `durable_membership_verified=false`,
`native_clock_verified=false`, `account_complete=false`,
`execution_authority=false` and `admission=DENY`.

The DB0044 insert guard also extracts the balance and account configuration
from the immutable DB0020 packet referenced by the accepted DB0021 sequence.
It checks the exact raw row, source locator, transport inventory, and reduced
fractional equity/available margin before a measurement can enter the chain,
including a new sample that would not become the selected peak. The guard
checks the original sequence-one and selected-peak witness links in bounded
work; it does not scan the lifetime population per append. The downgrade first
requests NOWAIT locks on both foreign-key parent tables and DB0044, then refuses
a nonempty diagnostic table. The hermetic identity gate pins the DB0044 trigger
body hash and demands a 0044 → 0043 → 0042 empty roundtrip, full re-upgrade,
schema check, and rollback readback after the DB0042 refusal.

DB0044 also refuses noncanonical outer measurement/projection JSON text and
extra projection keys before an immutable insert. The shared SQL serializer is
used only after an ASCII check, which matches this Python serializer for the
admitted integer/string/bool/null shapes. A captured row containing non-ASCII
text cannot be indexed by this diagnostic migration and remains DENY; it is
not translated or replaced. The serializer functions retain their existing
restricted EXECUTE ACLs. No broad function grant or account authority is added.
A restricted runtime login without the specific EXECUTE privilege cannot append
DB0044 rows; that role binding needs explicit PostgreSQL validation before use.

The repository is used by the version 3 DENY-only diagnostic path, not by the
live trading runtime. Version 2 still replays V1 history. Existing B3
populations require a separate, source-verified offline backfill from sequence
one before a current tail can use DB0044; no implicit bootstrap or
account-lifetime reset is allowed.

The isolated `catch_up_history` path implements that diagnostic prefix backfill
in pages of at most four B3 batches. It pins the *latest* B3 sequence and
digest under the existing exact-UID PostgreSQL transaction lock, starts only
at B5 sequence one or the immediately missing successor, and replays each
accepted B3 batch from its original immutable B1 capture. Missing B1 packets,
B3 gaps, altered predecessors, older B3 policy epochs, and changed latest
heads deny the page. DB0044's insert guard retains the raw balance/account
configuration checks, exact B3 binding, canonical bytes and predecessor B5
projection. No reset or later genesis exists. After commit, another session
re-acquires the lock, pins the latest B3 head, and independently replays each
new row and its predecessor. It checks the exact B5 page-end projection even
when another worker has subsequently appended a later B5 page; old pages stay
readable under the same pinned B3 head. A late readback failure leaves
committed DENY evidence in place for explicit restart and replay; it never
deletes or rewrites a row.

This path does not assert account-lifetime completeness or historical
point-in-time availability. B5 `db_recorded_at` is the backfill time, not a
historical balance observation time. The wrapper and stored projections retain
false account/native-clock/membership/execution authority and `DENY`. It is not
wired into B5 runtime or order submission. Prior B3 policy epochs need their
own versioned replay and cannot be silently reinterpreted by current policy.
The isolated PostgreSQL tests must pass on an exact merged source before this
path is called validated.

Focused offline tests and PostgreSQL integration cases are authored, but the
new PostgreSQL cases and Docker hermetic path have not been executed against
this isolated source. Fresh upgrade, nonempty downgrade refusal, concurrent
append, direct-SQL nonpeak forgery denial, crash/readback, and schema drift
therefore remain unaccepted. A database receipt proves neither OKX source authenticity nor
native clock ownership. Older uncollected balance samples and unobserved equity
peaks remain unknown. Limited OKX history endpoints and later archive exports
cannot recreate balance responses never captured, nor establish account-wide
genesis or the complete realized-loss window.

## Validation status

New tests are authored for raw source pins, missing values, genesis/prefix
integrity, peak retention, owner forgery/expiry/one-use behavior, synthetic
runtime denial, cross-currency local holds and original-source PostgreSQL
readback. A fresh dedicated B5 PostgreSQL database is required for the legacy
singleton case. Synthetic tests are not native account evidence. Completed
test results are tracked by the final evidence harness; authoring these tests
does not mark their execution PASS.

V2 adds meaningful negative cases for frozen UTC with expired monotonic time,
actual transfer of an original token to another task/thread/event loop, context
identity mismatches, cancellation, digest failure burning both registries,
untrusted native-clock handles and legacy TLS flags being unable to upgrade an
injected clock. Private registry fixtures exercise the fence mechanism only;
they do not prove native acquisition, a durable clock-proof issuer or PostgreSQL
acceptance. Test execution is recorded separately by the root validation harness.
