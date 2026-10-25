# Durable native financial operands V3

V3 adds the latest same-invocation history registration to the existing native
financial assembly. It does not publish an account revision, clear a stop, Arm,
qualify an order, or make account risk complete. Every portable result has
`admission=DENY` and all authority fields remain false.

The acquisition entry point accepts exactly the V1 acquisition parameters. It
pins the union of the original source manifest, existing financial producers,
CORE0050 model/migration/repository/catalog policy and the V3 implementation
before awaiting V1 acquisition in the same task. Runtime checks include exact
function, code, globals, defaults, class methods, aliases and ordered source
policy/SQL values; source file hashes alone are not enough. The old V1 and V2
function/class/policy/pin declarations remain unchanged.

After V1 returns, V3 synchronously removes its owner from the original private
registry before any other await or caller exposure. Only the new nonconstructible
V3 owner reaches the caller. Consumption is an ordinary function that removes
the V3 owner synchronously, before checking guards or constructing its fixed
awaitable. Closing an unawaited consumption releases both held source bindings.
A foreign task, cancellation, source replacement or failed consumption cannot
restore the owner. Persisted rows, reports and DTOs never mint an owner.

The producer reuses the original full B1/B3 population, raw and companion file
inventories, separate DB0046 retained full bundle/proof readback, current V4
source, active coverage journal and B5/Control replay. It retains the original
main and custody factories and their private clones. It never renews a clock,
lease, current source, epoch or stop state.

Only the latest original B3 event is registered. The linkage preserves its
original invocation, bundle/proof hashes, original timestamps, exact five-field
source reference, source manifest and original raw/companion locators. It cannot
register an old unregistered member by applying a newer member's proof. The
current B3 head and exact Control/account/ledger revisions are compared with a
formal CORE readback and checked again inside the CORE append transaction.
Currency state comes from the replayed coverage audit and exact checked
`LedgerScopeState`; missing or conflicting currency/revision data is rejected.

The producer uses only formal CORE deployment/read/append methods. A pending
`PG17_DEPARSED_BASELINE` is a denial. It never supplies
`require_native_baseline=False`, installs an observed baseline, calls direct SQL
as an alternate admission, retries an uncertain write or adopts existing rows.

At sequence 32, the actual original 32 B3 documents, existing registrations and
explicit `None` gaps form the checkpoint. Previous checkpoints are replayed;
missing native provenance is not backfilled. Global sequence is never reset.
This slice does not implement capture 33. The separately required versioned
history source, bundle and full-genesis streaming consumer remain outstanding.
All existing 32-active-capture, 64 MiB, 16,384-row, 8 MiB receipt and original
30/60/5-second limits remain unchanged.

Immediately before append, V3 replays original sources and separate custody
again. CORE burns its repository attempt at call time, commits and closes the
main write session, then opens a distinct main session/transaction for exact
readback. V3 rechecks source and factory identity, replays the original custody
and source again after main commit, and performs another exact main readback.
These are separate databases, without a cross-database foreign key or distributed
atomicity claim. A changed/unknown commit, custody, head, fence, inventory or
readback burns the invocation, retains any committed rows and grants nothing.
There is no rollback/delete compensation, automatic retry or reminting.

All waits remain within the shortest original monotonic lifetime and original
UTC expiry/Control lease. SQL expiry can only shorten those bounds. Original
UTC conversion, causal ordering and drift checks remain in the original source
owners. These checks do not claim an atomic deadline at the final COMMIT byte.

After durable source registration, V3 calls the unchanged V2 reducer, windows,
portfolio consumer and financial operands implementation. It preserves unknown
funding accrual/attribution, history tail/finality, loss-streak seed, historical
native high-water mark/drawdown and incomplete protection/operational coverage.
Observed fill cashflows and reconciled close-run arithmetic cannot be relabelled
as complete daily/weekly net PnL or funding accrual.

## Validation status

The new unit sources use explicitly declared synthetic I/O. The original history
acquisition, B1/B3/native-bundle/companion parsing and financial math are real
code; coverage/partition/custody transport is synthetic and is not native/TLS/PG
acceptance. The new PostgreSQL sources exercise real restricted roles, unique
latest registration, concurrency and independent transactions using labelled
synthetic linkage. Positive formal tests explicitly skip while the actual native
catalog seal is pending. No baseline/role/catalog/SQL readback is patched in
those tests, and persisted records are tested as non-authority.

This prepared source has only been parsed and compiled without importing the
application. Formatting, lint, unit, PostgreSQL, native acquisition, matching CI
and source integration have not run. Current catalogue sealing, real native
producer/coverage/custody integration, capture 33 and full acceptance remain
required. Trading and outbox stay disabled; engineering/ready/final flags stay 0.


## Revision 3: wrapper lifetime and final main snapshot

The await iterator now retains a fixed asynchronous wrapper that owns the
consumption object through completion, suspension and cancellation. A temporary
`await consume_owned_native_account_financial_operands_v3(owner)` therefore
retains the same wrapper as the held-variable form. The original synchronous
owner burn and destructor remain; closing or dropping an unawaited consumption
releases the original source bindings. The new wrapper is included in the
finite pre-acquisition callable inventory.

After the final full custody/history/current/coverage replay, V3 performs one
last main transaction through the additive formal
`AccountHistoryPartitionRepository.read_final_source_fence` method. The existing
DB0050 read acquires the same actual account UID advisory key as B1 and B3
writers: the signed first eight SHA256 bytes of the original
`ctcc-qualification-account-v1`, NUL, environment, NUL, exact account UID byte
sequence. Currency is excluded by the existing policy. That transaction retains
the UID and parent locks while the existing `_read_chain_locked` implementation
reads every original history B1 chain and the current original B1 chain, and
then rereads B3/DB0050/Control/account/ledger together in the same main session.
Separate B1 and partition reads after releasing either transaction would not
provide this fence.

V3 compares those original bytes, database recording timestamps, current head,
durable registration/checkpoint and original revisions. It performs no later
uncoupled database await before the final synchronous checks and inert receipt.
Formal native baseline and runtime-role validation remain unchanged. No new
migration, raw-SQL admission path, write, callback or owner issuer is added.

The returned value describes the snapshot observed while that main transaction
held its lock. Coupled commit/session-close awaits can follow the observation;
another legitimate writer may advance after release. Neither the receipt nor
its observed timestamp renews original source clocks or account revisions, or
claims the database is unchanged at the instant of return. It remains `DENY`.
Any future risk reservation/intent authority still needs its own fresh revision
reread and cannot adopt this DTO. Cross-database atomicity remains unclaimed.

New prepared regressions cover temporary and retained await with actual event
loop yields, unawaited drop/close, reuse/cancellation, a last-coverage-await head
change being refused by the last main fence, and post-snapshot growth remaining
an inert bounded observation. Additional real PostgreSQL mechanism sources
exercise a legitimate B3 append and actual advisory-lock wait while the final
fence reads B1 in the same session. Their positive branches remain explicitly
unaccepted/skipped while the native catalog baseline is pending. No newly
authored test has been executed or counted as acceptance.
