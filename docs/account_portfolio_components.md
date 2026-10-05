# B5 diagnostic portfolio components, version 2

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

A new projection can then verify the prior accepted projection's complete
membership contract plus the newly appended original source and the selected
maximum witness. Missing membership, a wrong predecessor, changed source or
unverified witness is a hard denial. A mere caller hash or an arbitrary recent
suffix cannot replace this proof. Database constraints must prevent modification,
deletion, late membership insertion or a projection detached from the original
source. A readback/rebuild procedure must independently compare incremental
results with full replay over bounded historical partitions, without deleting
the durable source chain. That contract is not implemented or accepted by B5v1;
it requires a reviewed migration and concurrency/crash/rollback tests.

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
