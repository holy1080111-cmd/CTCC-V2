# Final completion validation state

The 2026-09-19 final-completion branch begins at development commit
`016a3587476e83bb267558cf46998c501626ca3b`, tree
`0bcd60c53faf1ab8b0f290a7585fbeeb170540dc`. The separate deployment at
`C:\CTCC-V2` and its uncommitted changes are preserved. Neither a deployment
package date nor an old successful test count selects the source.

The initial source checkpoint contains both repositories' exact tracked working
files, unstaged/staged patches, Git bundles, refs/reflogs and hashes. Credential
files are excluded; only environment key names are recorded. The stopped Docker
Desktop disk has since been inspected read-only. Filesystem journal recovery ran
only on a COW clone, and the actual CTCC PostgreSQL/Redis volumes were backed up.
The PostgreSQL backup restored successfully in a network-disabled container at
migration `0017`; private backups also passed Windows CurrentUser DPAPI round-trip
verification. See [preservation and rollback](deployment_checkpoint.md).
Authenticated current account/exposure reconciliation remains pending. Offline
database state cannot authorize deployment or a claim of current flat exposure.

## Corrected durable intent identity

The old PostgreSQL intent tests exposed a real symbol mismatch: the reservation
stores `BTC-USDT-SWAP`, while the domain candidate displays `BTC/USDT:USDT`.
Intent construction now compares the reservation with the exact instrument ID
already bound by the original prefix and contract specification. It does not
perform an alias substitution, accept another contract, or change the candidate.
The unit fixture now uses the same identity as PostgreSQL, and a display-symbol
receipt is explicitly rejected. Intent records still do not grant an order retry.

The original source reproduced six intent failures in a fresh, dedicated
PostgreSQL database. With the identity correction, all 29 reservation/intent
integration cases passed, including independent-session readback, concurrency,
rollback and uncertain-state retention. This is an intermediate working-source
result; final acceptance requires rerunning the committed source.

## Exact source Docker verification

Export the chosen commit with explicit LF conversion disabled:

```powershell
git -c core.autocrlf=false -c core.eol=lf archive --format=tar --output=source.tar <commit>
```

`scripts/verify_final_hermetic.py` reconstructs the actual Git tree from every
archived file and mode before extraction/building. An archive transformed to
CRLF is rejected even when a line-ending-normalized manifest would pass.
The archive must have the expected SHA256 and Git commit header. It rejects
credential paths, runtime reports, duplicates, traversal and links.

The Docker image contains the full manifest source set and validates it before
installing dependencies. Build products are excluded from the source manifest.
The runtime regression verifies the image's own COPY output; it no longer mounts
a host source directory and `.env` as a substitute. Every hermetic run creates
its own PostgreSQL, Redis and internal network, with no exchange credentials,
no host `.env` and no external network egress from the test containers.

The runner checks supported migration upgrade, downgrade/re-upgrade in the empty
test database, schema drift, PostgreSQL reservation/intent integration, and the
complete Linux suite. Its PASS is narrowly hermetic regression, not a statement
of statistical, account, Demo or Micro Live production acceptance. It also checks
API readiness and service restart with execution disabled; this is not a Live
restart/protection test. CI keeps the existing required job name and runs this
same exact-archive harness, retaining commit, tree, raw COPY map and step logs.
Those require independent evidence at the same final source identity.
Targeted PostgreSQL and full Linux runs retain JUnit XML and explicit skip reasons.
The PostgreSQL acceptance verifier requires executed cases from every declared
integration module with zero skips; an empty/skipped green pytest exit cannot
satisfy it. Full-suite platform skips are retained separately from passing counts.
The evidence hash manifest includes these XML files and the durable crash marker.

## Conservative risk and preservation

Structural per-trade risk is capped at 0.5%, portfolio stop risk at 1%, with a
300-USDT default margin bucket. The 3/5/8/10/20x ladder remains a ceiling;
20x requires the existing mathematical, derivative, isolated-margin and cost
checks. The 60% aggregate margin gate counts held and reserved amounts and
rechecks exact reconciled exposure after leverage IO and at the final boundary.
Continuous scheduling cannot bypass daily loss or the persistent loss streak.

The final Demo HTTP entry transport currently refuses new entries because
trusted runtime qualification authority is not yet established. Manual, direct,
market, limit and FOK paths cannot bypass this containment with a caller PASS
or by mislabelling a POST as a read. Cancellation and close operations retain
their own existing safety checks. This containment is not completed Demo
acceptance or an enabled qualification pipeline.

Source formatting and Ruff diagnostics are being reconciled across the branch.
Local lint dispositions retain deliberate naive-time rejection fixtures,
immutable Decimal defaults, established ValueError validation contracts and
fail-closed IO exception handling. Previously silent auxiliary IO failures now
log exception class only, without account data, credentials or request bodies.

## Identified intermediate regression

Commit `89843e6da3eb19677ee429f5b4d5d4a4e6adcf29`, tree
`047512575ec6ecd9e8ddfa60c493f20054d4aac6`, passed its Windows full suite with
9,763 passed, 30 skipped and no failures. Twenty-eight skips are explicit POSIX
or Windows symlink privilege limitations; two are unavailable IANA ZoneInfo
data and are being removed through a pinned validation dependency. Its exact
Docker COPY/manifest/dependency checks, migration upgrade/downgrade/re-upgrade,
schema drift checks and 32 PostgreSQL intent cases passed. Its Linux full suite
passed with 9,755 passed and 38 platform-specific skips. The subsequent actual
crash/restart stage failed before seeding durable state: the probe called an
`asyncio.run` fixture inside its running event loop. The original failed run is
preserved and its overall hermetic result remains FAIL. A corrected probe must
complete actual interruption, database/cache restart and independent readback;
unit-suite success cannot substitute for that stage.
The corrected durability probe has since passed a separate actual SIGKILL,
PostgreSQL/Redis restart, durable-intent/uncertain-state readback and duplicate
consume rejection. That diagnostic used a targeted overlay; it does not turn
the original immutable run into PASS.

The second checkpoint `ecda5214e5de1a1e0fecceab2472958646b2ecb6`, tree
`ba865a1c66f63e11394e22914a330c8cf2d23c4a`, completed Windows with 9,989 passed,
14 failed and 28 explicit platform skips. Linux completed with 9,992 passed,
1 failed and 38 platform skips; its preceding 36 PostgreSQL intent cases and
empty-database migration/drift cycle passed. The exact image digest was
`sha256:25fef1ee65815d4295dec4b0f11e6d8303aec3fac710420d1df3de072ef141b7`.
Both full runs remain FAIL. The Linux failure is the expected literal `.env*`
rule missing beside the stronger case-insensitive exclusions; Windows also
exposed native temporary filenames exceeding MAX_PATH. The working repair
restores that exclusion and uses validated extended-length local API paths while
retaining ancestor pins/no-clobber publication. All 14 originally failed Windows
nodes subsequently passed in a separate working-source rerun. No assertion or
permission check was relaxed. The pinned `tzdata` validation dependency removes
the earlier two IANA-data skips; it does not change source timestamps.

Later working-source changes require their own exact-source full rerun; these
counts cannot be reused as their acceptance. The third checkpoint includes the
Windows repair, all-standard-product account packet v4, captured metadata,
source-derived reversal V3, versioned history-intent replay, forensic cost
decomposition and DB0018 reporting/unknown-result containment.

The working branch adds explicit versioned expansion history dispatch through
G12 and recorded Recheck, an owned regional account capture bound to the recorded ledger revision,
and an independent default-off Notion reporting worker. Account admission still
denies incomplete history/peak/accrual/product provenance. Live new-entry
transport is also contained until genuine qualified one-shot authority exists.
Neither a computational test nor an empty reporting queue proves Demo, Live,
source completeness or actual Notion delivery.

## 2026-09-23 continuation and validation identity

The third preserved engineering checkpoint is
`037fca431657fd8c692b0ffa43e1539790d4ba60`, tree
`569eb79f0d254f0ace2444fa24a9843b5513a967`. Its source archive SHA256 is
`43ffa11c8545a5d24f7cc58ed7fc347081b7a33b343170ab2e3994e32ec831bc`.
Its exact Windows full-suite XML records **10,437 passed, 28 platform skips,
zero failures and zero errors**. All 67 required PostgreSQL cases ran and passed.
The original driver nevertheless recorded FAIL because its result verifier
rejected unrelated platform skips when PostgreSQL modules were required. That
original identity and every result file remain unchanged. The corrected verifier
requires every specified PostgreSQL module to run without skips, while explicitly
allowing unrelated platform skips only for full-suite callers. An independent
sidecar reassessment verifies the original artifact hashes and records the
Windows suite outcome; it is not validation of later working-source changes.

The exact third Linux/Docker run was launched, but its final result is currently
unavailable. The resumed restricted environment denies WSL access; the old process
handle is no longer present. A timed-out database port does not prove that its
server stopped. No old image, earlier test count, original deployment or raw WSL
disk has been substituted. The current source has no new Linux/Docker acceptance.

The fourth-checkpoint continuation adds the separate unknown-state account bootstrap,
explicit range-anchor V4 restrictions, a durable Demo control journal through
migration 0019, and measured public-minute receipt work. See
[bootstrap capture](account_bootstrap_capture.md) and
[range anchor V4](original_range_anchor_v4.md),
[durable Demo controls](durable_demo_control.md), and
[measured public availability](measured_public_availability.md). These remain engineering work,
with no production account revision issuer or execution permit.

Independent review reproduced a bootstrap cross-session clock regression,
foreign scalar callbacks, and a queued Arm intent surviving local revocation.
The working fixes reject these paths and retain the original failing artifacts.
The bootstrap/account compatibility set passes 219 cases; both ordinary account
runtime v3/v4 receipt bytes remain identical to the frozen third source. The
migration identity module passes 47 cases after moving its temporary output into
the writable workspace; its initial 15 temporary-directory setup errors remain
recorded. Native safety-publisher checks still fail when the ancestor handle for
`C:/Users/holy1` is denied. No ACL or native handle protection was relaxed.

The hermetic harness now explicitly requires the new bootstrap/control
PostgreSQL modules, and the full Linux report must also show those required
modules executed. The process-kill probe now seeds three synthetic control
scenarios: current Arm intention, EStop, and EStop before first ownership. After
actual process/database/cache restart it must verify unchanged persisted state,
new ownership without restored Arm, and preserved EStop. The seed keeps actual
runtime owners alive until interruption, checks current Arm immediately before
publication, and binds the Arm account to the qualification exact UID. It now
requires the v2 exact FOK request and verifies its version and request SHA256
after restart; legacy geometry-only intents cannot satisfy this probe. The
pre-publication Arm observation does not claim instantaneous state at the later
external process kill. This uses explicitly
synthetic clocks and identities; it cannot establish real host-clock, account,
Demo or Live acceptance. The new migration, PostgreSQL cases and expanded crash
probe have not yet been executed against PostgreSQL in this resumed environment.

The continuation also fixes destructive downgrade races in DB0017--DB0019.
Each downgrade acquires non-waiting exclusive table locks before checking that
all tables to be removed are empty, and holds them through transactional DDL.
DB0018 locks its referenced parent tables first. A busy or populated database
refuses rollback. Upgrade semantics remain unchanged, but migration source hashes
changed and require fresh validation. The control/migration helper group passes
121 cases. The 21 new real PostgreSQL downgrade cases have only been collected;
one async fixture-construction defect found during review was fixed before sealing.

The expanded harness tests pass 64 cases, including 26 targeted crash/readiness
cases; these groups overlap. The range compatibility work passes 478 related
cases and retains byte-identical ordinary/V2/V3 records against the third source.
The measured-minute producer passes 105 pure cases and a separate 406-case
compatibility run; those groups also overlap. Its two native filesystem tests
fail at the existing ancestor handle access restriction. No failed native result
has been converted into a skip or a passing memory-double result.

The bounded source-pattern security review covers canonical Git history and
working source. Its five non-fixture groups resolve to existing public examples,
an HTTP scheme prefix and empty CI settings. The actual deployment credential
input is unavailable in this environment, so exact-secret comparison and the
final release secret audit remain incomplete. No credential was copied into
source or diagnostics for this review.

## Preserved fourth checkpoint and collection repair

The canonical source now resides in the independent `canonical-final` repository
inside the final-completion workspace. The prior linked `canonical` worktree is
preserved. Its Git metadata was outside the writable workspace; no reset,
overwrite or remote-main checkout was used. A full-history bundle and 697-file
working-source snapshot were verified before the move. Independent object and
reflog review found no missing commits; Git archive line endings follow the
existing attributes and are not claimed byte-identical to the raw working copy.

Fourth checkpoint `79d5172d48045ab44b5c45516d58fb09eef60a21`, tree
`2a0533d368eb6861658f66e517137743fb8d0e17`, preserves DB0019 and the reviewed
bootstrap, range and measured-source changes. Its Windows regression failed
collection because unit and integration tests had the same module basename.
Zero tests executed in that failed run; its artifacts remain unchanged.

The collection-only repair is `fbe0729e64c8e1904873a64f4d6ce6f170f01d02`, tree
`81d7fd86adb208df0ad0a3a84a58cbd4a56fe0e6`. It renames the unit module and updates
its manifest entry without changing runtime behavior. Its exact archive SHA256
is `d20a9802390701d3b7c3ca405692c7c0bba0b3a5da44d657559910b6bfc2ca19` and
manifest SHA256 is
`76bd4a7386a179a87a5011b5c248996736111afb66e337f38baae7dda339b24f`.
The isolated Windows run passed source/dependency/lint/format checks, then
completed with 10,624 passes, 89 failures, 104 setup errors and 137 skips from
10,954 cases. Its result is FAIL; XML SHA256 is
`db140541a476174d5876fc5153cea01762c4995b50c7fc7a542d65000fe5234f`.
Two failures expose public acquisition code placed across the passive research
boundary. Independent trace review and 13 observation-only reruns confirm 76 native
permission failures plus 104 setup errors, 11 database DNS failures, 108 missing
PostgreSQL skips and 29 platform/privilege skips. The ancestor access failure
persists with both current and narrower documented access requests; an
attribute-only handle cannot prevent directory replacement, so it is not used
to bypass the required lock. PostgreSQL, schema-drift, Linux and Docker acceptance for this
checkpoint remain unavailable. Required PostgreSQL skips
cannot be counted as acceptance and no historical passing counts are substituted.

Further working changes, including the DB0020 private account ingestion journal,
precise native clock observation and process-private dispatch ownership, are
outside both sealed archives and require a new exact-source validation. The
account crash harness now distinguishes finalized safe raw bytes from an
unfinished RAM-only prefix. Its restart verifier preserves the old journal and
records unknown missing bytes without inventing a completion time. Offline
memory tests do not establish actual process/database restart acceptance.

The Notion destination was independently fetched through the connector on
2026-09-23. Setup documentation now selects title field `報告名稱`; the old
`報告編號` selection was a text field and would be rejected by the existing
adapter. Opaque REST property IDs and a runtime REST token remain unavailable;
this correction neither establishes a real binding nor delivers a report.

## DB0020 and native clock working continuation

The [private account journal](account_capture_journal.md) records request/page
progress under the existing qualification account lock. Its independent commit
readback preserves exact bytes, sequence, predecessor and DB receipt time without
issuing complete-history claims, changing risk revisions or enabling execution.
Before signing stops, arbitrary source identities and queries are represented by
hashes. Safe raw pages are finalized only after scanning against every signature
issued by that invocation. A page containing a later signature is withheld;
earlier immutable metadata cannot retain that plaintext.

Independent review reproduced both a late-signature metadata leak and a temporary
body-sample clock regression that had allowed a complete packet. The fixes retain
the anomalous observed time and byte/hash evidence, stop acquisition and refuse
the packet. The final account compatibility group passes 966 cases, including
42 journal cases and the account crash-helper cases. A separate independent
journal/bootstrap group passes 56 cases; these counts overlap and are not added.
Both original reproductions and intermediate failing runs remain preserved.

The crash probe separately retains a safely finalized failed page and an
unfinished RAM prefix. Verification checks the recovery event again in a new
session, including its DB receipt time and unknown tail. The synthetic database
URL must have the exact isolated host/user/database/port and no query overrides.
Neither memory tests nor disposing an engine count as an actual process restart.

DB0020 downgrade locks its qualification parent before the journal and refuses
any retained event. The shared PostgreSQL downgrade matrix now has 27 collected
cases, including capture-start/raw retention, parent/child contention, and empty
downgrade/re-upgrade. Schema comparisons include indexes, trigger definitions and
functions as well as columns and constraints. Offline SQL generation passes;
the new PostgreSQL cases and actual schema comparisons have not executed here.
The hermetic acceptance verifier requires all ten relevant PostgreSQL modules to
execute without skips. The source-head test now expects the actual DB0020 graph;
its initial stale DB0019 assertion failure is retained separately. After fixing
that assertion, all 151 root harness/migration checks pass with unchanged source
pins during the run; this is still not PostgreSQL or process-restart acceptance.

The [native clock v2 observer](public_clock_v2.md) uses the precise Windows UTC
clock and a measured monotonic domain, preserving raw status output and pinned
formatter semantics. It retains the existing time tolerances. Its 109 focused
cases pass, while native observations still report W32Time stopped/manual.
The public-clock/capture compatibility run has 214 passes, 17 native ancestor
handle failures and one platform skip. Host synchronization and trusted public
capture remain unaccepted. Previous clock wire bytes remain unchanged.

The full regression boundary failures were repaired by moving the five public
acquisition/clock/journal modules into `app.public_market_source`. The passive
research boundary tests remain byte-identical. Additional checks constrain the
new package to read-only acquisition and native clock/storage evidence, with
`app.mie.validation.measured_public_replay` as its only application consumer.
Module ASTs differ only in import namespaces; complete synthetic V1/V2 receipt
and raw-byte bundles remain byte-identical. The relocated group has 226 passes,
17 native ancestor-handle failures and one platform skip, with no source changes
during the run. The failures remain FAIL; this is not native storage acceptance.
A 712-file exact working archive precedes the relocation and preserves the
unsealed clock/account work separately from the previous Git checkpoints.

[D0 ownership](dispatch_ownership_d0.md) retains a bounded invocation's original
candidate and separately reads back its committed v2 intent. Cancellation before
task startup and cancellation swallowed by a database dependency both terminate
the attempt; neither can restore ownership. This module has no READY issuer or
order-writing transport. Source-owned account/public authority and the final
guarded transport remain missing; these working changes do not establish Demo,
Live, OOS, shadow, economics or release acceptance. The final D0/legacy
containment group passes 234 cases (104 D0 and 130 existing boundary cases),
with source pins unchanged. Four independent cancellation/error probes also
pass without writes or authority; these are separate from real SQL acceptance.

## Post-938634d account and clock repairs

The sixth checkpoint is `938634d0cc89202c5f2e410f42fde2108a4dbb5e`, tree
`a729b4b098cf790df9160b8583ba5ca124c04701`, migration head 0020. Its exact
archive Windows suite subsequently terminated with `TimeoutExpired`; it has no
complete final XML and remains FAIL. Subsequent working changes below are not
covered by that run and cannot borrow its counts. The previous full
`fbe0729` suite remains FAIL: 10,624 passes, 89 failures, 104 errors and 137 skips.
The original two research-boundary defects were fixed in the sixth checkpoint.
Native ancestor permissions and unavailable isolated PostgreSQL remain separate
unresolved acceptance requirements.

Current OKX fill contracts permit bounded negative trade IDs for liquidation/ADL
and explicitly empty order IDs for block activity. The account parser now
preserves those source strings only with the documented subtype context; unknown,
missing or contradictory exception context fails. Bill IDs remain positive row
and pagination identities. No empty field becomes an invented order lineage.
See [account coverage](qualification_account_v4.md). All 1,683 account/bootstrap/
journal/forensic compatibility cases pass, including 110 new parser cases; no
skips and unchanged source pins. Ordinary v2/v3/v4 complete frozen packets remain
byte-identical. Twenty independent probes confirm raw negative IDs and unbound
block fills cannot produce false order attribution or account authority.

Versioned public attempt v2 now retains returned native-clock observations before
and after collection, including failed/incomplete/truncated diagnostics. Each
sidecar is published without clobbering, independently read back and hashed.
An unavailable terminal clock stays null instead of reusing a prior timestamp.
Negative evidence advances only the attempt audit; it cannot create a measured
availability receipt or a healthy-clock claim. Raw bytes still inside an abruptly
terminated native child process are not claimed durable.

Independent review reproduced four carrier copying/serialization failures. The
repair keeps payloads in a private identity registry, binds each carrier to one
attempt/stage and consumes it before publication. Failed publication cannot make
it reusable. Seven independent ownership probes and 43 negative tests pass.
The implementation's disjoint focused/public groups have 269 passes, 17 native
ancestor-handle failures and one platform skip. The 43 negative tests overlap
that total. The failures remain FAIL; memory publishers do not certify native
storage. Legacy v1 attempt files, existing complete v1/v2 capture packets and
native health-check function ASTs are unchanged.

Original Notion policy pages were read again on 2026-09-23. They require explicit
strategy-specific reversal structure but do not supply the missing integrated
range-protection implementation or complete sweep HTF formula. Existing policy
denials remain. The missing integrated source prevents claiming an exact port;
it does not prohibit a new explicitly versioned policy under the user's mandate
to complete strategy-specific HTF/protection rules. A new policy must preserve old
records, independently replay source conditions and retain all applicable vetoes.

The directive requires atomic reservation of the worst sampled candidate/reference
risk, bounded adverse FOK execution and actual-fill validation. A theorem covering
every possible future fill price is an additional research design, not a required
dispatch gate. The existing `all_fill_prices_covered=False` contract remains
accurate. No code gate requires it to become true. Earlier documentation that
listed universal fill-price coverage as a completion prerequisite is corrected;
trusted account/public ownership, current controls and final dispatch integration
remain real engineering requirements.

## Outstanding source-independent evidence

The host was measured behind OKX public time and NTP. Windows denied this process
permission to start W32Time. Timestamp tolerances and recorded receipt times have
not been altered. Public-source acceptance remains closed until the host clock
is repaired and directly remeasured.

Existing retrospective holdout summaries have already been exposed. They cannot
be resealed as unseen OOS or promoted from computational rehearsal to predictive
evidence. Engineering tests and genuine statistical acceptance remain separate.

## 2026-09-28 continuation

The host can now reach Docker's Linux engine. Original deployment containers
remain stopped. A fresh no-cache image from the exact `ced812c` archive built
successfully with its existing manifest and hashed Linux dependency lock. This
image is a validation baseline, not acceptance for later working changes.

New PostgreSQL 17 and Redis 8 instances use the repository's pinned digests and
synthetic state. Three databases migrated to 0020 inside an internal network.
The first attempted Windows-host connection failed because that internal network
did not expose its requested host ports; this failure is retained. Running the
client inside the same internal network succeeded without adding egress.

Frozen source overlays then executed six Range V5 SQL cases and two B2a query
verifier SQL cases, all passing without skips. These are real database component
tests with synthetic exchange inputs, not final source-COPY or account acceptance.
Independent review subsequently found B2a boolean/integer equality defects in
row-lineage checks and cross-stage clock fields that were not fully bound to
their source events. Exact canonical comparisons and measured event/predecessor
checks repair those joins. The unchanged independent probe moved from 16 failures
and 7 passes on the frozen old source to 23 passes without skips on repaired
source. The failures remain retained; pre-repair passes cannot certify the
repaired source. The hermetic harness now requires both new SQL modules in its
explicit no-skip acceptance list.

Separate loopback-only synthetic PostgreSQL/Redis services are available for the
native Windows suite. Their fresh 0020 migration and schema-drift check pass.
They are distinct from the Linux internal-network hermetic environment.

The operator-authorized Windows Time service repair started W32Time and changed
its startup to Automatic. Actual native observation passes, but an independent
OKX time response and NTP samples still measured the host roughly 0.71 seconds
behind. Native health alone therefore does not establish public timestamp
acceptance. A bounded native correction and fresh measurement remain required;
no CTCC tolerance or retained source/receipt timestamp was changed.

Live remains default OFF. No current completion, release or trading acceptance
is asserted by this working document.
