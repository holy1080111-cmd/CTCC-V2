# Prospective CTCC event cutover custody

This prepared component establishes an immutable historical starting point for
CTCC event-consumption coverage. It does not complete G6 or authorize orders.
It preserves every existing reservation, intent, transition and tombstone.

The implementation moves the genuine same-task native coverage owner already
issued by the original V4 current-account collector, Control owner, B5 audit
and DB0048/49 active-epoch commit/readback. No `passed`, `flat`, identity,
source receipt or complete-history DTO is accepted as authority.

The cutover transaction replays that original source without renewing any
deadline, re-reads the exact current Control/coverage head, repeats the B5
all-currency/legacy audit, and runs the sole existing UID-wide event/transition
validator. The prospective private scan accepts only the existing valid
revision0/null-claims bootstrap state or unchanged valid initialized claims.
It never invents financial claims, equity, available margin or outcome history.

DB0051 adds one immutable marker linked to the existing active coverage
journal. This is coverage custody metadata, not another event/reservation or
intent ledger. Its canonical record retains the actual journal-scan receipt,
post-active-readback stamp, a strictly later measured coverage start, source and
deployment hashes, Control revision and original UID-wide event/chain hashes.
The append commits before a separate session independently reads the exact
marker and repeats current source, Control, B5 and journal checks. Unknown
commit/readback results retain any committed marker and issue no owner.

The returned private owner carries historical cutover custody only. Input
ownership is burned at call time. A discarded unstarted awaitable closes both
fixed coroutines and releases process ownership without deleting durable rows.
Copying, pickling and constructing opaque owners are rejected. Nothing here
restarts Arm, clears EStop, sends exchange requests or enables execution.

The final synchronous fence replays the original source, function/context and
cached Control-owner lease. It is not a fresh DB Control read. DB Control and
coverage were re-read under the second transaction's UID lock, which may release
during the final commit/session-close await. A later Control revision does not
rewrite historical custody. Future G6/R6 must re-read current Control/coverage
and compare exact epoch/revisions under the account UID lock. This component
does not add a detached read that merely moves the same permission race.

Historical V1 state/receipt false authority fields remain unchanged. The existing
public `inspect_consumed_event_journal` still requires initialized claims and
still cannot grant an absence permit. Both paths share the identical existing
row-chain validator; this preparation does not replace it with a second one.

## Reviewed native schema observation; runtime acceptance remains separate

The genuine capture workflow40193690798/artifact12599575430 observed PG17.11
from source`16d3100fa8767036f8423c360e706fe2bf0bb426`/tree`ebf0ea152495389b03c37d7c19f00112e2295591`.
Its retained ZIP is `a8150c13e68c28c9917316cdf5be2a96fd5b36c658a4f46688c54361326f89ff`.
The original source/manifest and all false authority fields remain preserved.
Independent source/DDL/custody review supports only the new0051 deparser seal
`531b56a9680701a6f1bd462c52d0c57edb883194cbf88c75b84bfa7b391b66fd`. Effective0051 DDL remains
`dba77c3af835900e4d49ff9aa17f5d47031aeb2c40cdc4f84566e9c08e020924`.
Native0050 declarations/seal and historical0048/49 coverage policies are unchanged.

The capture uses the schema-owner role and a repeatable-read snapshot. It is
not restricted-writer, native-account, namespace-completeness, G6 or execution
acceptance. The retained JSON fixture tests actual catalog/deparser/fence logic;
it cannot install a missing seal or issue a runtime owner. Synthetic pending
tests explicitly scope None/status faults and restore the real source seal.
Actual exact-source native0051 role/concurrency/recovery, full regression and
fresh post-seal native capture/formal verification remain required. The old
unsealed full-CI refusal is retained and is not attributed to sealed source.

The current0051 empty migration experiment first verifies0051, descends to0050,
checks actual cutover absence, then runs the unchanged historical0050 and older
layers. Current0051 HP retention and migration receipts use separate v2
contracts. Historical0050 child experiments, helper contracts, baseline and
source declarations remain exact. The new0051 downgrade locks every parent and
the cutover table together before catalog inspection, refuses a nonempty marker
table using a PostgreSQL exception, and never deletes durable markers to pass.

The capture-only diagnostic profile is
`prospective-event-cutover-catalog-0051`. Its dedicated workflow exports an
exact Git archive, verifies the copied source and manifest, builds a new image,
migrates a new internal-network PG17 database and captures the actual catalog.
It has zero tests and explicitly cannot establish full PostgreSQL acceptance.
It inherits no host credentials or environment, and order-write flags are OFF.
The new catalog capture pins all eight cutover source operands and the manifest
before and after the measured query, with source graph/database head0051 on
both sides. Capture bytes are saved without clobber and read back independently.

Structural verification may retain a genuine capture while a baseline is
None (as in the retained16d310 observation). A separate actual catalog probe must then observe the formal
`prospective_cutover_native_baseline_pending` refusal; it does not convert that
refusal into native admission. The profile emits no synthetic JUnit or test
counts. Its artifact metadata keeps native admission, G6 and execution false.
The full hermetic producer and union require the formal native0051 verifier and
therefore remain closed until the independent seal is completed.

The capture-only profile must settle every exact owned container/network and
independently verify final absence before reading or hashing writable captures
for export. Removal acknowledgement alone is insufficient. Cleanup and both
final absence queries share one90-second deadline, checked before and after
each command; errors or late returns cannot create another budget. Unknown
cleanup leaves the run failed, preserves source TAR and fixed failure metadata,
and issues no trusted capture artifact inventory.
Any still-visible resource with this exact run's owned prefix also blocks the
barrier, including an orphan missing from the tracked list; unknown resources
are recorded without being deleted.

After that barrier, ordinary single-link stable file reads revalidate all three
catalogs, source/manifest/migration/deparser pins, formal-probe output and exact
capture-stage byte receipts. Inputs are rechecked after staging. Only verified
bytes are published under `export/native-catalog-capture/`; any failure retains
private raw/staging evidence and an explicit unaccepted path list. The receipt
separates safe metadata artifacts from accepted capture observations. None of
these records gives native admission, G6 authority or order permission.


For independent sealing, review the capture's exact source archive, Git/tree,
manifest, migration identity, PG17 image/session/owner/ACL facts, all deparsed
constraints/indexes/functions/triggers and effective DDL hash. Preserve the
original unsealed capture and review record. Only after that review may a new
source commit replace the new policy's None/status with the actual reviewed
deparser digest and `SEALED_FROM_REVIEWED_NATIVE_PG17`. Never modify0050's seal.
Then capture again from that exact new commit, run formal verification and all
current0051 tests/regressions. Unsealed-source test results cannot be attributed
to the later sealed source. Tests that explicitly assert None must receive an
explicit versioned update for that genuine seal; they are not a permanent
production requirement that a native baseline remain missing.

The coroutine wrapper also releases ownership on terminal send/throw results,
including throw-before-first-send and task cancellation before the first send.
Prepared tests retain strong wrapper/binding references and assert immediate
cleanup, without garbage collection. Independent synthetic tests exercise the
actual native repository body and both session exits using fake SQL IO, not a
replacement `_append_native`; they remain separate from native PG acceptance.

## Exact next missing issuer

No G6 continuation consumer is added in this component. The missing native
namespace-history issuer must retain and replay complete measured order/fill
page chains from the durable coverage start through a fixed observed cutoff,
for the exact Demo UID and controlled credential session. It must reconcile
`CTQ` entry and `CTA` protection client IDs to exact persisted reservation and
consumed-intent lineage. Client IDs contain only29 hexadecimal characters of
the reservation ID, so suffix matching alone is insufficient. Detect collisions
and verify full immutable request/intent mappings.

Legacy `CTCC`, `CTCCA` and `AUT` writer namespaces must remain denied after
cutover or receive explicit lineage handling. Unknown CTCC namespace orders,
unresolved intents/uncertainty, missing pages, conflicting cursors/duplicates,
retention gaps and late conflicting observations deny coverage. Retain source
order-create time, fill execution time and fill-record generation time separately.
Existing history-query receipts explicitly leave order retention and late-arrival
finality unverified; they cannot be relabeled complete by this component.

The next private G6 issuer must MOVE the retained original G1–G5 prefix/frame,
replay the original detection and its actual source candle, and require its
opening time strictly after the durable coverage start. A candle that merely
closes after start is insufficient. Preserve original candidate/event/zone and
all original expiries. Read ALL retained UID event keys, including pre-cutover
terminal tombstones, and run the existing timing evaluator. Check original
source/context/deadlines and current Control again after the final await.

G6 uniqueness concerns CTCC event lineage. It does not require universal absence
of unrelated external manual orders or complete pre-cutover PnL/funding/HWM.
Those unknowns remain unknown and block their respective portfolio/reconciliation
gates. A positive G6 observation is not a reservation: final R6 must independently
recheck exact event membership and current coverage/control revision under the
existing account UID lock and UID/event unique constraint.

`CTCC_V2_ENGINEERING_COMPLETE=0`

`MICRO_LIVE_READY_AWAITING_OPERATOR_ARM=0`

`CTCC_V2_FINAL_COMPLETE=0`
