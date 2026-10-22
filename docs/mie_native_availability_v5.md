# Native availability V5: owned future-data handoff

V5 connects the previously separate native public source, Stage1 clock sample,
runtime deployment checks and DB0046 raw custody. The successful owned path can
admit the captured rows for a future decision in the same measured native clock
domain. It does not mint the original Gate3 dataset qualification, a historical
availability claim, an OOS result, or any order/execution permission.

The entry is opt-in `DevelopmentValidationAvailabilityOwnerV5.initialize` with
an exact source service, its observation repository, a native custody session
factory, a fixed existing Development/Validation plan, and a fresh empty private
store root. `capture_once(ordinal=..., expected_previous_receipt_sha256s=(...))`
claims one coordinate and flushes/reads its no-clobber claim before the first
source, DB or clock acquisition await. The old V1 store, V3/V4 contracts, flags,
times, expiries and holdout/sealed entrypoints are unchanged.
The original journal object, genesis and journal ID are pinned separately from
the repository. Switching to another genuine journal using the same repository
is rejected, including after source acquisition; saved bundle lineage is also
checked against the original finite store identity.

The owned sequence is:

1. Consume the finite claim lease, pin exact native factories and original
   methods, create private native repositories/factories, acquire the actual
   DB0035/witness0047 catalog owner, and run DB0046 deployment preflight. Fixed
   SQL also verifies that DB0046 uses the same authenticated DB/owner scope as
   the clock repository pair. A third restricted login is permitted; native
   engine object identity is not used as a DB identity substitute.
2. Acquire the real unconsumed V3 source carrier. Call the original Stage1
   `acquire_native_post_read_clock_v4`, then consume its actual one-use internal
   clock carrier promptly. A saved archive, `FrozenGate3Artifact`, timestamp,
   callback or supplied success flag cannot replace either carrier.
3. With private sessions, re-read the complete ordered witness chain and selected
   prefix, binding the raw aware-UTC SQL timestamp to V3. Read/replay the entire
   native public journal before and after copying all original raw files,
   attempts, captures and checkpoint bytes. No sorting/dedup repairs source rows.
4. Freeze a MIE-specific canonical bundle, retain it locally, append it once
   through the original DB0046 repository, commit and read exact bytes in a new
   transaction. Immediately consume the actual five-second custody receipt;
   verify bundle bytes, raw attempt/capture/row replay and proof digest. Recheck
   deployment and the native journal again. Raw bundles are bounded to 64MiB;
   exceeding the bound fails rather than truncating or fetching again.
5. Collect fresh native Windows health and retain its bounded raw payload even
   if rejected. Measure final-ready after the exact durable readback. Preserve
   Stage1's original timestamps and raw native observations. V5 neither changes
   an older DB0035 time nor infers its clock accuracy from the new sample.
   The native producer already returns canonical JSON bytes: V5 requires that
   exact immutable byte type and the existing 512KiB payload bound, retains those
   bytes unchanged, then validates them. It does not encode them a second time.
6. Compare all three retained files against the bundle, evidence and native
   health hashes held by the completed native invocation. A modified local file
   cannot become an expected hash merely by being rehashed into a new receipt.
   Publish/read back the finite outcome and compare those pins again, then issue the process/thread/loop/task
   bound `NativeAvailabilityEligibilityV5`. Read its expected evidence pin using
   `evidence_sha256_v5` and consume once with
   `consume_for_current_future_decision_v5`. The consumer measures its own cutoff
   natively, checks same-domain causal order and current health freshness, and
   rejects cutoffs before final-ready or after its five-second consumer lease.
   Native 100ns bounds are rounded upward if represented as microsecond times.

The returned frozen decision says
`same_domain_future_availability_eligible=true`; its rows retain original content
hashes and use the new conservative final-ready availability time. The returned
record is an audit calculation, not a reusable capability. Replay, read-only
reconciliation or constructing a DTO cannot reissue the runtime carrier.

Failure, cancellation, expiry, drift, late publication or ambiguous commit/
readback leaves the original claim and all successful durable writes. The owner
stops. No retry, refill, second append, writable restart or cleanup erases the
attempt. `CollectionReadOnlyV5.open/read` verifies exact identity, plan, ordinal,
receipt/hash and retained file inventory. Missing root/claim/partial/hash mismatch
denies; it never initializes an empty substitute. A late failure may preserve a
valid raw journal and even a committed DB bundle while issuing no capability.

The overall V5 asynchronous attempt is bounded to 120 seconds. Existing shorter
source/clock/DB policies also apply unchanged, including the deployment owner's
fixed 30 second lifetime, native five-second sample/handoff checks, and 900 second
OS sync-age limit. Consuming an upstream token retains its measured fact; it
does not extend its lease or refresh the deployment owner. A slow path fails.
The still-live catalog owner and its poison/expiry latch are synchronously
rechecked at the final publication, pin read and consumer boundaries.

Actual positive acceptance still requires the supported reviewed native Windows
profile, real authenticated PG17.11/MSVC, the exact DB0035/0047 and DB0046 schemas,
restricted grants/durability settings, and real source/clock/append/readback.
The unit positive uses explicit synthetic native labels, raw TLS/HTTP results,
clock/catalog/SQL substitutions and a DB0046 receipt registry fixture. It runs
the actual Stage1 and V5 ownership/serialization/consumer code and complete raw
attempt/witness replay while barring network/DB connects. It is not native or
production acceptance.

The deployment proof is limited to the restricted application-role boundary.
Hostile arbitrary in-process shared engine/class replacement, a DBA or host
administrator remain outside that boundary. SQL scope/build strings do not
attest the DB host or vendor binary. Native OS health and the 900ns PG quantization
cell do not certify independent absolute UTC or a numerical external UTC error
bound. Cross-host/restart cutoffs need their own earned domain continuity policy.
Independently administered/WORM custody, first-ever evaluator access, historical
bar-close availability, candidate/holdout/OOS, promotion and execution remain
unproved and ungranted. Warmup captured now is available only after this new
receipt; it cannot become evidence for an earlier historical prediction.

Store the local claim/bundle/health/evidence files privately. They include native
host and internal DB scope evidence and are not public report/Notion payloads.
No API credentials, request signatures, private account responses or secrets are
part of this public-market bundle. Windows file fsync/readback protections are
reused; directory power-loss durability and global cross-root exactly-once are
not claimed. Stage1's own failed native-health attempt is not newly durably
retained by this version; its source journal and V5 tombstone remain, and the
new final-health phase retains its own bounded attempt payload.
