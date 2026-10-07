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

On Windows the admitted archive bytes are sent directly as Docker's tar context.
The review extraction is not the build input: a Windows directory context was
observed to change archived non-executable files to executable files. Exact COPY
verification deliberately rejects that difference. The runner admits and builds
from one retained byte buffer, so replacing the archive file after admission
cannot substitute the build input. It records its own source hash alongside the
target commit/tree. Neither byte nor executable-bit verification is relaxed.
[Docker's tar-context interface](https://docs.docker.com/build/concepts/context/)
supports passing the archive on standard input.

The eighth checkpoint `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9` first failed
exact COPY validation on `.dockerignore`: its SHA256 matched, but the image mode
was `100755` while the archived Git mode was `100644`. That run remains FAIL.
The tar-context correction passed 41 Linux archive/identity tests, including
executable-bit drift and archive replacement after admission. Its native Windows
targeted run passed 39 cases with two explicit POSIX-mode skips. These component
results are not final-source hermetic acceptance; a fresh full run with the
corrected runner is required and the runner hash distinguishes that rerun.

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
These checks describe the existing Demo service. The new qualification runtime
still requires an owned mapping of its separate portfolio policy to those caps;
legacy settings alone do not establish that mapping or enable the new pipeline.
The status display also reports the configured aggregate margin limit when the
300-USDT bucket is enabled; the bucket does not disable that existing hard gate.

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

## 2026-09-28 thermal interruption and preserved working source

The later operator-authorized clock correction completed and retained W32Time
Automatic/Running. Two native observations plus direct OKX time responses passed
causal validation before the second restart. No source timestamp, received time,
or CTCC tolerance was changed. This is historical clock evidence; a new boot
requires fresh observation before any source acquisition can be accepted.

Windows restarted unexpectedly at 21:34 and 21:57 (Asia/Taipei). System event 86
from Kernel-Power explicitly records a critical thermal shutdown at 21:57:04,
with ACPI threshold `_CRT=370K`. That threshold is not a measured CPU temperature.
The operator reports improved ventilation. MyASUS later showed both fans rotating
(CPU 3,483 RPM, GPU 2,118 RPM) and a displayed temperature of 72 degrees Celsius.
Those point observations do not certify sustained cooling or production uptime.
The host's unexpected shutdown is not a passed controlled chaos test.

Docker and long regression runs remain stopped during investigation. A new
previously absent `.wslconfig` sets two processors, 3 GiB memory and 2 GiB swap for
future WSL 2 starts. These limits affect all WSL 2 distributions and have not yet
been verified in a running VM. No deployment container, exchange order route or
automatic recovery/Arm was enabled. Removing only this unchanged newly created
configuration restores the previous absent configuration; later user edits must
be preserved.

The sealed `caa1588` Windows full suite and corrected-tar Docker full suite were
interrupted, with no completed full-suite XML. The renderer's post-restart broad
suite was also interrupted. They are `INTERRUPTED_NOT_COMPLETED`, not PASS. Their
original partial logs and RUNNING records remain intact, with separately hashed
interruption records explaining their later state. The exact-source Docker run
did finish its 133 PostgreSQL integration cases before interruption; that result
does not cover the later working DB0021 or account/control changes.

DB0021's separate frozen schema overlay completed fresh upgrade, drift check,
empty downgrade to 0020, re-upgrade and drift check, plus 34 durable migration SQL
cases. Its Linux migration-identity module had 19 passes and 28 explicit native
PowerShell skips; the identical frozen module then passed all 47 cases on Windows.
The ad-hoc runner incorrectly required zero Linux platform skips and its original
aggregate FAIL is preserved. A separate scoped evidence review records these
completed groups without promoting the new account repository, normalized-child
negative tests, full PostgreSQL acceptance or final regression to PASS.

Before further changes, 747 working-source files were frozen into an exact-byte,
uncompressed preservation archive with readback, source hashes, unstaged/staged
diffs and the prior verified history bundle. Its checkpoint manifest SHA256 is
`10d5b70e9f868369452750489ec63c9301ad2195696deb99dee5238c97dc9fdc`.
This is an uncommitted recovery point, not an admitted Git source-COPY archive or
a release. The old source manifest remains intentionally stale until the next
reviewed seal. Known credential matching found no secret; four pattern findings
were separately classified as synthetic development database credentials or the
literal authentication scheme prefix. This scoped scan does not replace the
final history, reports, image and release audit.

The private evidence is under the task's `validation-results` directory:
`host-thermal-20260928`, `thermal-working-checkpoint-20260928`,
`migration21-schema-after-reboot-20260928/scoped-evidence-review.json`, and both
`reboot-interruption-20260928-*.json` records. No source-independent acceptance,
real Demo/Live execution, OOS, CI, main merge or release result is implied.

At 22:15:31 MyASUS completed a newly selected fan-only diagnostic, displaying
100%, zero problems and zero recommendations. No firmware, fan setting or driver
was changed. This resolves whether the fan diagnostic can pass now, not sustained
thermal stability. A new post-boot native clock/direct OKX time probe also passed
both checks; raw response and measurement hashes are retained in
`host-time-native-569d9b49b2f44d48bc3d6d1beb2a879c`. Only bounded single-process
checks may resume before resource-limited long-run stability is established.

Docker subsequently restarted after preserving, rather than deleting, the stale
runtime socket directories left by the shutdown. Direct engine readback confirms
two processors and 3,050,414,080 bytes of VM memory. Each original deployment
container was individually inspected and remains stopped. The engine aggregate
running counter disagreed with those individual flags; both observations are
retained, without treating the aggregate count as evidence that trading resumed.
The separate synthetic PostgreSQL service is capped at one CPU/512 MiB and the
synthetic Redis service at one quarter CPU/128 MiB, with restart disabled.
The retained Redis configuration has an existing loopback port binding; this is
not described as having no host port. Evidence: `host-thermal-20260928/`
`docker-caps-readback.json`, SHA256
`ea91cfb20953789537af3b02d5941dbd90b33fb89070eb0fd7ded1f7d5de22b6`.

Short Windows component runs use verified one-logical-CPU process affinity,
below-normal priority, a 45-second limit, a clean execution-disabled environment
and frozen source with before/after hash checks. The following completed runs
have terminal exit zero and no failed, errored or skipped cases:

| Frozen component | Passed | Evidence directory under `validation-results` |
|---|---:|---|
| Summary V2 renderer | 12 | `summary-v2-bounded-after-fan-check-20260928` |
| All-state ledger event observation | 28 | `bounded-ledger-after-fan-check-20260928` |
| Current account observation | 13 | `bounded-account-after-fan-check-20260928` |
| Captured instrument rules | 44 | `bounded-instrument-after-fan-check-20260928` |
| Funding observation V2, repaired test IDs | 63 | `bounded-funding_repair-after-fan-check-20260928` |
| Executable quote V2 policy | 23 | `bounded-quote-after-fan-check-20260928` |
| Original base candidate, corrected fixture chronology | 49 | `bounded-precursor-after-fan-check-20260928` |
| Quote V2 raw packet replay and pre-IO ownership rejection | 16 | `bounded-quote_collector-after-fan-check-20260928` |
| Account-history single-verification comparison | 4 | `bounded-index_comparison-after-fan-check-20260928` |

The original funding test run retains 62 passing cases and one errored case:
pytest's automatically generated oversized-input case ID exceeded Windows'
environment-variable length limit. Explicit short test IDs fix that framework
failure while retaining the identical oversized input and production parser.
Both the failed and repaired frozen records remain available. The candidate's
first authored fixture had an invalid setup/trigger chronology; it was corrected
in a separate test-only freeze before execution, without changing the production
event extractor or weakening assertions. It is not reported as a failed run.

These tests prove only the stated synthetic component behaviors. They do not
prove native source ownership, full account completeness, a genuine G12 example,
complete qualification, authenticated Demo execution, or sustained host thermal
stability. Full Windows/Linux regression and the final acceptance matrix remain
uncompleted; no counts from these overlays are borrowed for the sealed source.

The exact ten-file event-ledger overlay subsequently completed all 14 PostgreSQL
cases, split into nine serial runs of at most 45 seconds. The dedicated fresh
database migrated through that overlay's 0020 head and passed drift checking.
Each client readback confirmed one CPU, 1 GiB RAM, restart disabled, exit zero
and no OOM kill; its database used the separate one-CPU/512-MiB service on the
internal network. These cases cover all event states, cross-currency same-UID
lookup, unknown scope, bounded reads, invalid clocks, shared UID locks, terminal
tombstones, competing reservations, and explicit legacy-collision rejection.
They do not validate the later account-index 0021 or unfinished B5 methods.

After all clients exited, the synthetic database was backed up and deliberately
terminated with SIGKILL. The same container recovered from exit 137, and complete
deterministically ordered row hashes for every public table matched before and
after recovery. The database had no concurrent clients or exchange capability.
The backup SHA256 is
`cd71b87d1abe340a64921f104f6f60efd021d57d9259094988fda569b3df7ee2`;
the recovery identity SHA256 is
`988069f742328645e0b9d0ca2bb6fb759e96b7aaa1058ba3b55d874a8ef9e2db`.
Evidence is under `capped-event-postgresql-20260928/database-crash-recovery`.
This is a passed bounded database-row durability check, not API/process restart
acceptance, Arm recovery, exchange reconciliation, or the full chaos matrix.

## Further bounded working-source validation

Before the public V2 owner integration, 774 working-source files were preserved
with exact-byte archive readback and the verified sealed history bundle. The
checkpoint identity is
`240422b7753e856d81b45e4687220e9135405c08dbf58d124fb08a58b9d700fe`.
This recovery point includes unfinished B5 work and is not a release seal. The
credential-pattern review found no known credential; its four review findings
match the earlier synthetic credentials/authentication prefix. The runtime
supplement has a mandatory companion correction: `live_armed:false` in that
supplement was not a durable Live-storage read. Actual durable Live Arm is
`UNKNOWN_NOT_READ_FROM_LIVE_DURABLE_STORAGE`; no operator Micro Live Arm has
been received. Both original and correction remain retained.

The independent quote V2 replay review found that fresh source stamps alone
could admit a diagnostic packet whose request or overall capture exceeded its
transport budget. The repaired frozen collector now enforces the complete
request-through-response-close two-second bound and the full capture-through-
client-close six-second bound. All 30 bounded Windows cases passed, including
exact-boundary, one-microsecond overrun and rehashed oversized-duration cases.
This does not grant native ownership or trading authority. The earlier 16-case
result remains scoped to the earlier source. Evidence:
`bounded-quote_budget_repair-after-fan-check-20260928`, XML SHA256
`db9f42dfc21fecccf5f2bbb410d447c69563dd6c3db7119f30da35fdd4169b7a`.

The original history precursor completed 44 unique Windows cases across six
serial groups: source and prefix replay, fixed event/entry/expiry, and negative
inputs. The original unsplit expiry group exceeded the 45-second budget and
remains `NOT_ACCEPTED` with no completed XML. Its six unchanged cases subsequently
passed in three pairs, using the same frozen source and the same time/resource
limits. All accepted groups have completed XML, zero skips and unchanged source
hashes. The aggregate evidence SHA256 is
`a5c86423917653b009a59ed77ed108687a9aad17fdd1977d7cf4c27e9da49744`.
These are synthetic structural-history checks, not historical point-in-time
availability, native candidate ownership or genuine Demo examples.

The frozen B3/B4 account-index source also completed 11 PostgreSQL cases against
a fresh 0021 database, including immutable normalized records, prefix checks,
restart, concurrent append, original-source replay and late failure. The mutation
group's three parametrized cases all passed, but its first ad-hoc harness expected
one case and therefore reported `NOT_ACCEPTED`. Its original identity is retained;
a separate source/XML/container readback review records the scoped three-case
result. The runner's expected count was corrected without rewriting that result.
The longer 37-capture continuation case and later B5 changes are not certified by
these runs. Evidence: `capped-index-postgresql-20260928`.

## 2026-09-30 continuation

Readback still reports the 2026-09-28 21:57 boot and thermal event; no newer
thermal shutdown is recorded. W32Time remains Automatic/Running. A new native
clock observation and one direct, no-proxy/no-redirect/no-retry OKX time response
both passed causal ordering. Evidence:
`host-time-native-86c009f8676b4acc8c16a9245dff395d`; exchange receipt SHA256
`cc28a3e8ebb6bb6116abd59ecd519fbc8b0ba29b6d9845a70a16b3df3dce320c`.
This point observation does not certify sustained host uptime or market sources.

Docker failed again on an inaccessible leftover `dockerInference` socket.
Its local log also records a GUI request to reset factory settings at
14:13:31 UTC. The request's human/process actor is unknown; the agent did not
issue that reset. Only the verified zero-length runtime socket directories were
renamed to recoverable preservation locations. No database or volume was changed
by that recovery script. Recovery record SHA256:
`7888afc2b98dc7c0df0d54aba9234cd27017600c37421750569a0ad04d89890d`.

Actual engine readback then confirmed two logical processors and 3,050,418,176
bytes of VM memory, with every inspected container stopped. Existing source-
pinned test containers, images, database volume and original deployment
containers remained present. Evidence: `docker-caps-readback-20260930.json`,
SHA256 `085ae0300835c90e931d1554a929419a274f71c5ba87a9eabf09c1874039b688`.
The preserved state permits new bounded synthetic tests; it does not establish
exchange reconciliation, runtime Arm state or final Docker acceptance.

The frozen B5 second source completed its 13 component and eight runtime Windows
cases with zero failures, errors or skips, under the same one-CPU/45-second
limits. Their XML SHA256 values are respectively
`e0a81c15bd413b3e4d72b722086a66d6a843db7170d7362ee1adc44bab227604`
and `0bdf2ea1761f72269bcf332aad1e6d332a7a893fa49b96b0879a8ac86b13253a`.
Synthetic acquisition is explicitly denied native ownership. Measured balance,
sampled HWM and local flat components do not supply complete lifecycle, funding
or streak evidence: `PortfolioRiskSnapshot` remains absent. Dedicated B5
PostgreSQL acceptance is tracked separately from these unit cases.

The B5 second freeze then completed fresh 0021 migration, schema drift checking
and all three dedicated PostgreSQL cases. Original balance source pages and
prefix/head refusal survived repository restart; legacy missing initialization
remained unknown and the EStop latch remained recorded; a USDC read could not
hide the same UID's expired USDT hold or consumed intent. Each client exited
zero with one CPU/1 GiB, restart disabled and no OOM kill. These source-pinned
synthetic checks do not certify native account completeness or B5 production
latency. Evidence: `capped-b5-postgresql-20260928`.

The sweep history policy's first two positive cases failed correctly with
`prior_range_safety_denied`. Their constant HTF closes produced zero derivative/
state confidence and insufficient mathematical coverage. A separately frozen
test-only revision supplies actual nonconstant OHLC that still computes neutral
HTF conditions; the production policy and its 26 dependencies are byte-identical.
All 29 cases then passed in two bounded Windows groups, including preservation
of the old constant-price denial. XML hashes:
`805f9f9f5db2806fad5cac6857e11d062b0d0c904dcae199746006111a147b07`
and `96fb9255651142f1bfd9219f160f0fe112516cb60ea2a08ae872cb3c12cffdd4`.
The first failures remain retained. This establishes the synthetic history
permission intersection, not source authenticity, complete qualification,
candidate entry-zone acceptance or predictive point-in-time availability.

Independent review subsequently found that B5's second runtime could mistake a
caller UTC callback for native clock evidence and did not bind its carrier to
the original task/invocation. The 21 Windows and three PostgreSQL results above
remain scoped results for that frozen source; they do not establish a trustworthy
current native owner. The defect is recorded in
`account-audit-source-only-20260930/b5-clock-context-required-addendum.md`.
The new V2 diagnostic separates TLS observations from current and historical
native clock evidence and refuses ownership until a durable clock-proof issuer
exists. Its countercases and changed source still require behavioral validation.

Before the G1 arithmetic extraction and account clock repair, 785 working files
were preserved with exact archive readback and the sealed history bundle.
Checkpoint manifest SHA256:
`aed500bb2fee9de764dde2c17ae7f94a9f4c8fb70ab000ff6cdb4909ca72c9b5`.
All four unresolved security patterns match previously reviewed occurrences,
line numbers and exact source hashes for synthetic database credentials and
authentication-scheme prefixes. Classification supplement SHA256:
`cbd5003d4455184c34fc16b07fae490eda5519f841d4a9ef7fafb1181afb599e`.
This is a working-source classification, not the required final history/log/
release secret audit.

The first frozen full-public V2 owner completed 44 cases in six bounded groups.
Its remaining 14-case packet group reported 13 passes and one failed mutation
assertion (`quote-rate`); the group is `NOT_ACCEPTED`, and the original failure
is retained for root-cause repair. The first P1 legacy byte comparison failed
before producing an identity because a synchronous fixture called `asyncio.run`
inside an active loop. Neither failure is relabeled as a pass. These checks use
synthetic acquisition and do not establish real market examples or production
acceptance. G1 V2 separately replays the full raw packet and explicit funding
pair through the shared arithmetic; its source changes have passed lint only,
and old G1 byte equivalence and behavioral validation remain outstanding.

### Later completed scoped readbacks on 2026-09-30 UTC

The funding mutation failure was a test input that changed `0` to `0`; it did
not exercise a mutation. A new test-only freeze changes the actual funding rate
and asserts that the packet bytes differ before requiring rejection. The eight
production files and all 769 dependency files match the prior freeze exactly.
All 58 full-public V2 cases then completed with zero skips, errors or failures.
The original failed attempt remains retained. Four repaired legacy comparisons
also completed: P1 success/rejection compared 96/63 files, and P2a
success/rejection compared 90/57 files, byte for byte, without normalization or
baseline changes. Aggregate evidence SHA256:
`5ce9e1f066f722f2995f67b839bdf3a4601c0513d8dcb2c7d26de2585b52e229`.

The B5 third source completed 25 clock-boundary, 13 component and ten runtime
cases with zero skips, failures or errors. This source burns foreign,
transferred, expired or cancelled carriers and keeps injected-clock diagnostics
without native ownership. A genuine account clock-proof issuer and historical
high-water-mark clock provenance are still required; these 48 cases do not
certify account completeness, portfolio risk or execution authority.

G1 V2 subsequently completed all 58 new cases, including refusal before calling
foreign serializers or mappings. The same exact frozen source completed all 83
legacy G1 cases. Separately, 29 old/new comparisons confirmed identical model
JSON, canonical results, rebuilt source bytes, measurements and digests, including
first-failure ordering, hostile Decimal settings and exact arithmetic boundaries.
No full Windows or Linux suite result is inferred from these component runs.
G1 scoped aggregate SHA256:
`7bdaab93c1598c117d4b234f6fb74639813766e81b10b104c03c6a76493bdc1c`.

One actual public-only acquisition used native clock checks, direct TLS REST
and WebSocket sources, four candle frames and immutable journal readback. It
completed 125 journal events without credentials, retries or order writes.
Packet SHA256:
`e1059526baec62733a2b31ff76daf48a85a085398dd70fe58ca997471c61b309`.
At its original measured cutoff, `2026-09-30T15:21:36.464030+00:00`, full raw
replay produced G1 `passed` and verified identical output. At the later actual
UTC cutoff, `2026-09-30T15:59:29.149375+00:00`, reuse of the packet was refused
by the fixed source profile. No timestamp, expiry or freshness tolerance was
changed, and no acquisition owner was restored. The diagnostic remains `DENY`:
it is not G1-G12, a new candidate, current execution eligibility or Demo
acceptance. Evidence: `saved-native-g1-v2-cutoff-20260930-A`.

The first V6 sweep Stage A/B runtime attempt failed during collection because
the new structural-protection contract imported a regime evaluator that imports
the partially initialized strategy base. This is an engineering dependency
cycle, not a market denial or an accepted test. The original log, XML and
`NOT_ACCEPTED` identity are preserved in
`bounded-v6_inside_long-after-fan-check-20260928`; a separate leaf-contract repair
must complete source-pinned behavioral validation before acceptance.

### 2026-10-01 local continuation

The V6 third scoped freeze completed all 58 Stage A/B cases in six bounded
Windows groups with zero skips, errors or failures. Its 156 dependencies are
fixed to the reviewed second freeze; two account files deliberately exclude
ongoing native-account work. This is a scoped composite source, not acceptance
of the entire current canonical tree. All 29 original permission cases also
passed while comparing the complete old/new canonical permission bytes on 32
actual evaluator invocations. Actual Unknown regime, the original out-of-zone
cancellation and original sweep-extreme protection remain preserved. Aggregate
SHA256: `fe650c0c8551688adcd1be6b57dabc93308d8400d2986729b88c93a25857373d`.
V6 G12/recheck/ledger version dispatch remains a separate required integration.

Original-source preflight had an independently reproduced callback defect:
an exact native MappingProxyType could wrap a foreign mapping. Six new cases
failed on the original source because caller callbacks could run; two native
dictionary cases passed. A native referent check now refuses such mappings
before len/iteration/serialization. All eight new cases and 16 unchanged opaque
preflight cases passed on the fixed source. The seven other function/class
definitions have unchanged ASTs. Original failures remain `NOT_ACCEPTED`.
Fixed module SHA256:
`02f4e775ce55aa6197c53a96eee3d17d0e2dbf44e419adde8aa9136010b5a97c`.
Evidence: `one-shot-proxy-guard-review-20261001` and bounded oneshot groups.

A fresh isolated PostgreSQL database migrated through 0021 and passed schema
drift checking. The real control-bound reservation race and active-transaction
requirement each completed with one passing case, zero skips and unchanged
source. The reservation race admitted exactly one durable winner for the same
UID/event. Evidence: `capped-control-postgresql-20261001`. This is synthetic
SQL acceptance and supplies no native account or exchange authority.

The full control-bound exact-intent test reached the unchanged 45-second thermal
cap on the original source. An eight-second sampled stack diagnostic showed
real repeated source replay at 32 seconds and the independent inner intent
rebuild at 40 seconds; it did not show a database-lock deadlock. A test-only
reuse of the already fully constructed binding still reached the cap. All three
client containers were stopped, without OOM, and the original logs/identities
and committed synthetic intent rows remain preserved. No timeout was extended,
validator disabled or capped run counted as passed. A source-reviewed bounded
test split and removal of the redundant same-invocation inner replay still
require implementation and fresh byte/rejection/regression verification.

## 2026-10-01 bounded source-specific continuation

These checks apply only to the named frozen sources. Full Windows/Linux
regression, native account completeness, Demo, Micro Live, OOS, CI, main and
release remain incomplete. Order authority stays DENY.

The fourth native account proof freeze (795 files, source-map SHA256
`5830adcbccb4342c44a722f7f2f762e6c232763306f7331c791a5a6751d6422f`)
passed 82 unique synthetic cases, zero skips, in seven serialized one-CPU
groups capped at 45 seconds. Genuine raw-page positive proof replay precedes
each mutation/storage case. The original old-anchor and exposed cases retain
exact denial. Third-source six failures and the rejected selector-count run
remain preserved. Evidence: `validation-results/native-account-clock-scoped-evidence-20261001.json`,
SHA256 `2881c4507ae78e575d2fa169e1b19a276ed64715120a72da357f261b1e44dda4`.
This does not establish real native acquisition, authenticated Demo, native
PostgreSQL, historical HWM, full PortfolioRiskSnapshot or post-G12 ownership.

The control private-assembly freeze (788 files, source-map SHA256
`8a80d026f28b0e774315d6bc78b7a44ea9850ae53f73d5b1c5ba0e14acfbdf7e`)
passed 15 unique PostgreSQL cases and 11 actual old/current canonical-byte
comparisons. Each public builder/replayer still performs one genuine inner
intent replay. Historical readback remains audit-only. This supersedes the
pending private-assembly implementation above, without accepting the earlier
capped runs. Evidence: `validation-results/control-intent-scoped-evidence-20261001.json`,
SHA256 `62ad568d7e6c74e01398ca741404d4cb7cf5cc4c7681460d61ae107c180573d7`.
Three capped runs and a separate Range V2 missing-account-binding AttributeError
remain NOT_ACCEPTED. Full intent/PostgreSQL regression and production latency
are unresolved.

Stage C first-phase freeze (813 files, source-map SHA256
`8f8b301d8584c104817f71b446a66537180c21f325ac3093f159f6a281f7d4bc`)
has actual long/short G1-G11 and six-artifact G12 filesystem publication/hash/
readback positives. Both recorded-recheck tests pass computation and independent
source replay assertions, then fail normal JSON output reconstruction. They
remain failed tests, with logs/XML preserved. A narrow exact-version decoder
is under repair. Sources/account values are explicitly synthetic; these are
not the four required real Notion/Demo examples.

The sixth original-precursor freeze preserves fixed production/policy bytes.
Base, catalog, input-pin and replay groups passed. History fixture transport
errors and impossible Python foreign-__dict__ fixture installation were
reproduced and are being repaired. The full 158-case module has not passed.
No WAIT/DENY result, old receipt or JSON output acquires authority.

## 2026-10-01 continued frozen-source verification

The seventh original-precursor freeze (797 files, source-map SHA256
`62dfa0e4d8aaaff490ba048f3ee122d587066969ce787830524217d0678c81be`)
passed all 158 unique cases, zero skips, in 14 fresh serialized bounded groups.
Only its test fixture changed from the sixth freeze; production and policy
bytes remain unchanged. The prior transport and impossible foreign-dictionary
fixture failures remain preserved. Evidence aggregate SHA256:
`b1f7c4f4aff2fa593f7b47f632c1c0b4f35b5e407eaa8f9eeace35971fb508ad`.

The third Stage C phase-one freeze (814 files, source-map SHA256
`834e65696d3b013062357c32169b8fa136a67c135f98e95db04ea9cfe2726b31`)
passed all 212 unique scoped cases, zero skips, in 22 fresh bounded groups.
This includes six-artifact G12 publication/readback, current recheck, exact
normal JSON reconstruction, source/lineage mutations and old Sweep tests.
The exact-version decoder repair retains unknown-field rejection and compares
computed outputs against their recomputed exact JSON types and values.
The last test-only repair documents the existing behavior that caller-supplied
market quality is regenerated. The first and second freeze failures are kept.
Evidence aggregate SHA256:
`c0a498dd08bbe398eb7aca5e80ef914e8cc548e4293762fe68e485d3a41b4015`.
Phase-one whole legacy byte comparisons and phase-two durable account binding
remain pending. These synthetic cases do not establish native or Demo evidence.

The B6A freeze (818 files, source-map SHA256
`80ddfa161045ac0917b04528f349737a9937c0ec66c856f7328bc69446c8a2a3`)
has an actual old/new comparison of 16 complete original forensics outputs
and six exact error results, with no difference. Comparison SHA256:
`40952f82b62cb0335e323249f5c2277e94e7320853886006456fda184e344f2a`.
Its new lifecycle cases are not yet accepted. Funding accrual, complete net
loss history, loss-streak seed, historical high-water mark and current-tail
closure remain unknown; no complete PortfolioRiskSnapshot is issued.

The raw V2 numeric freeze (824 files, source-map SHA256
`dd8040092dd74580a9be95c7c75a1c4ce9fc649bcf87f8cae4a295a7c014a6ca`)
has 115 actually collected cases matching its preregistered inventory.
Independent AST expansion and source-delta verification preserve the old
location and economics guard/math semantics. Runtime verification is ongoing;
collection and partial passing groups are not full acceptance. Its fixed
cost profile is explicitly synthetic, without an authenticated fee source.

All checks above remain source-specific. Full final regression, trusted native
account history, risk and intent integration, Demo, sealed OOS, Shadow,
Micro Live, GitHub CI, main merge, release and Notion completion are not passed.

The second raw numeric freeze (824 files, source-map SHA256
`b948ce58c414b2070b220c9748a424dfeb84b1bbf0174ffed6882e8026a0ac3b`)
now passed all 115 unique preregistered cases, zero skips, in 21 fresh bounded
groups. Root established that the first failing funding test assigned its
original value `0`, leaving the whole packet and digest unchanged. A genuine
`0.001` inspection mutation was actually rejected in both directions; the
test-only repair preserves the other 823 files and the original failure.
Scoped evidence SHA256:
`f033a764a40831e84c672f9e15d87b6a52bf2be79e3750bbf4cd48cba6b51fa3`.
An additional 75 preregistered old/current cases compared complete actual normal
and round-trip JSON, canonical result and retained raw-capture bytes. All match;
aggregate SHA256:
`6fc5f19a71a5ab89e19f36d886286378acad9252d559dd427de0253c1e8d1c1d`.
The original sealed comparison helper remains preserved; its separately
identified extension captures normal serialization without normalizing it.
These remain synthetic source diagnostics with unknown native fee, event ledger,
complete account/risk and current execution ownership. All authority stays DENY.

The second B6A freeze (818 files, source-map SHA256
`7034b18a5b0246e3bb4e64d0c3e42fc4b7a0ee3633a26350f1c9401facb9d223`)
now passed all 37 unchanged preregistered cases, zero skips, in five fresh bounded
groups. Only the first original fill's supported net direction derivation changed:
its verified entry/exit role now participates with side. The original exit-only
A16 failure remains preserved; missing entries are never manufactured and now
produce the original known-inventory rejection. Full net, funding, loss-streak
seed, high-water mark and current tail remain unknown. Independent findings about
contradictory bill identity and foreign operands nested inside exact Fraction
objects still require separate reproduction/repair; this is not full B6 acceptance.

The first actual legacy Stage C comparison (`engine_v1`, long, original default
synthetic profile, qualification only) matched all 38 complete normal/round-trip
and scalar payloads on exact before808/after814 sources. Comparison SHA256:
`f8a022def14ba28d67d41a7e76e1eec12aff23687397d0c7f6c57dee374ce387`.
Remaining phase/direction comparisons and mandatory same-profile phase joins
are pending. This first pair provides no native timing, risk or intent authority.

The original four numeric modules now passed all 627 actually collected nodes
on SECOND824; the three original forensics modules passed all 363 nodes on
direction SECOND818. All 86 bounded groups and six disjoint completed batches
were independently read back, including setup/call/teardown records, complete
JUnit node identities, source populations, logs and recorded one-CPU limits.
No failures or skips occurred. The immutable scoped dossier report SHA256 is
`77723d84a42f0550e9a9c5c789cdc84cffc274e8eecaf3f3d55acccd5666738a`.
This does not borrow the separate 115/37 new cases or establish final regression.
The complete 37-case B6A second-source evidence SHA256 is
`cebabfcf009f14029047c8906b5be0c7a2c41a3846bedcb8ac37f0ebf4693b67`.

All four explicit original bill identity contradictions and six nested foreign
Fraction cases were genuinely reproduced on unchanged direction SECOND818.
Their defective component classifications/operator callbacks remain preserved
as NOT_ACCEPTED evidence. A fresh second outer harness additionally checks the
actual Settings names with Demo/Live writes and Notion disabled, absent host
credentials and `_env_file=None`; its SHA256 is
`018d8a575c4e2bb3f5995ef4eb2e386393211c743e7c78133fe332b6896cf935`.
The earlier diagnostic flags alone were not actual Settings readback; FIRST
tools and records are retained separately. Guard repairs require their own
coherent source and fresh original/new regression and complete-byte comparisons.

Before further guard or private-facts changes, root preserved 828 complete
working-source files, exact archive readback, recovery Git bundle, source/dependency
identities and an isolated synthetic control database dump. Checkpoint SHA256:
`b8752bc759debd39ec1eedf849a51955ec45090026161ac406e849faa923b16b`;
redacted runtime supplement:
`fa77345bc06b5c3437ae62c6353add887bf208e423fd047659989176b5191344`.
All three original production containers were stopped. Actual Demo account
reconciliation/exposure and durable Live arm state remain unknown; operator
Live authorization remains absent. Four exact previously reviewed source-pattern
findings were classified without storing credentials, classification SHA256
`4fb0e0d066ba86b03e682f21bc6c349f711fcdab58510fb56a45152be4124bd3`.
This checkpoint is neither a release manifest nor final secret audit.

On the separately frozen guard source THIRD820, source-map SHA256
`0b34768d7b4d3cd0312f765cf9f46dbe1ff36ad4f8d250769db56f7d50d27270`,
all 422 actually collected tests now passed with zero skips: the original 37
lifecycle cases, four new raw bill-identity cases, 18 nested Fraction cases and
all 363 original forensics cases. All 40 groups, complete ordered node partitions,
setup/call/teardown reports, XML identities, original collection artifacts,
Settings readbacks and source/tool bytes were reread by the root assembler.
The actual scoped report SHA256 is
`1dc79ec80531353beadd88970dc1fbc0311a33df273a690c1f637adf7626514d`.
The original 16 positive outputs and six exact errors also match every full
member byte, freshly reread after testing; comparison report SHA256
`59e51c803dc55c1a44f72f2ee6cb12a99775c1f5e83fa7e5041af8f4dfbd02e7`.
The FIRST root collection's exit4 relative-path failure remains NOT_ACCEPTED;
only the new SECOND tool's fresh outputs are included. Worker affinity/priority
were actually measured initially; a separate post-test native limits readback
was not performed by that tool. Completed workers returned within their
supervision cap; kernel termination of an entire timed-out Windows process tree
has not yet been tested. This is neither final regression nor native financial,
Demo or Live acceptance. Missing funding, complete outcome history, loss-streak
seed, high-water mark and current tail remain unknown.

The clean external private-facts source THIRD extraction retains SECOND829
source-map SHA256
`7e6c5bb0172b07956b910853b1cacfe5af11caa100c85344d20950cc4e2e28a2`.
Root actually collected 549 nodes across seven scopes. Facts groups g000/g001
passed; g002 genuinely failed its long public-precursor hostile Decimal-context
case: caller Inexact/Rounded flags changed. The original raw log/XML and all
three group identities remain preserved. No source merge or runtime acceptance
is granted; before824/after829 paired diagnosis is pending. The original SECOND
physical-directory cache discrepancy and FIRST tool population exclusion remain
separate preserved failures. Neither source AST similarity nor a static case
count substitutes for this failed runtime contract.

The original Stage C synthetic compatibility protocol currently has 44 of 46
complete paired comparisons. Range V5/long intent replay BEFORE exceeded its
45-second cap; its partial built-intent document does not establish successful
replay. Range V5/short replay is not run. The final completeness checker remains
unexecuted. A separate diagnostic startup was rejected and provides no new
accepted pair. A benign OS-only root observation established that the Windows
venv launcher inserts a process layer: the subprocess's immediate parent PID
differs from the supervisor PID. The original generic startup denial does not
uniquely identify its failing bootstrap guard. Held process ancestry, image
identity and actual kernel process-tree timeout/cleanup require a fresh proof;
no parent check or validation deadline is removed.

On October 1 root ran two separate, pinned Windows OS-only success-case
probes. FIRST (helper c17280ae1ccf175f505a7d0fed1de7e715b0205e07e603fd45d5c0e686285e42)
was rejected before its first private release at
`outer_single_coordinator_membership_required`. Its original PID/count
operands were not retained and remain unknown. The exact own coordinator
handle was terminated with exit 91; the observed job was empty after cleanup.
The external root observer recorded complete process exit in 0.3269498 seconds.
Root evidence is 4c873370e68e68bc8b24aff43f6f135211b33b42595d13540291f30ea3bac0f9.

SECOND only added declared read-only native observations, preserving the
original job limits, acceptance predicate, private gates and deadlines. Its
actual PID query returned held coordinator 42252 and unexpected member 41104,
with two assigned/listed members. A separately labeled additional accounting
query reported two active members. Snapshot
ce8b05223d9e9c2223a6436699deba0b95a6807e26ebb8c2fdaecfd4b2a5db5c
establishes that SECOND's PID predicate failed; it does not recover FIRST's
missing operands or identify member 41104. SECOND was also rejected before
release and cleaned up, with external complete process exit 0.9055753 seconds;
root evidence is 316ff6e5a4db51c1768b2356c730401329b1ea40919cec224e19d4a016131e78.
Both attempts remain NOT_ACCEPTED. No app, compatibility capture or order
execution occurred. Four-case OS acceptance and the application process-tree
bound remain unproved. The unexpected member requires measured native image,
birth and ancestry; it is not exempted or guessed to be a console helper.

The external Context-isolation FIFTH830 candidate has independent source,
archive and AST preservation review only. A separate 830-bound FOURTH tool
package is sealed with unconditional closed runtime entries. Its 581-row plan
is static preregistration, not collected or executed test evidence. Neither
candidate nor tool is merged or assigned application acceptance.

The fresh THIRD OS-only diagnostic retained current live native identities for
its exact returned job members. Held coordinator 3464 and unexpected member
35596 matched the second current census. Member 35596's measured image was
`C:\Windows\System32\conhost.exe`, with parent PID 3464 and image SHA256
`e449bce01f275cd08f3d4e64bb73b3b43ae845a0dbdb3e6131426e66537705e5`.
All three native PID reads, held birth/image/liveness and specific membership
checks matched. Snapshot SHA256 is
`f44945aa5e9e0dd3d8678fb7c189195ff300add750bb45089da8f06fa1f3e451`.
This identifies only this fresh observation, not SECOND's old member 41104 or
FIRST's missing operands. The unchanged single-coordinator predicate rejected
the launch before release. Root observed exit2 in 0.3223022 seconds with no
external kill; native cleanup left zero active job members. Root evidence is
`2c4b82f1e049cc786691eda1291357c15db963adea0d347eee80f4774b0e7b5f`.
THIRD remains NOT_ACCEPTED. A separate reviewed console-launch correction must
retain all original population limits, memberships, gates and deadlines; no
console process exemption or application acceptance is granted.

FOURTH changes only child startup flags to the default console inheritance plus
explicit hidden startup information. Its fresh actual launch satisfied the
original single-S inventory/accounting (40628 alone, active1), the complete held
W41012/S40628/R39372/C39624 native graph, exact outer3/inner2 membership and the
success-branch acknowledgement. It was subsequently rejected with the safe
child denial `inner_job_not_empty_after_exit`. The original inner-zero operands
were not retained and remain unknown. Microsoft documents ActiveProcesses in
relation to exited processes and release of process references; the current
source retains process handles at the zero check. The next separate version
must preserve independently measured held exits before releasing references,
then measure the unchanged empty-job criterion. No zero count is fabricated.
FOURTH result SHA256 is
`041a7e2f2b350f9f5bcf213aa40bc69933226df532f9a372932165de390de69a`;
root evidence is
`6f71ec2f6844e60255d4b9ce7d72fdc58929474acc2078c5c1ce24485416db4b`.
Root measured exit2 in 0.5758213 seconds, no external kill, and zero active
members after final rescue cleanup. FOURTH remains NOT_ACCEPTED and the other
three cases were not run. All application tools remain closed.

## 2026-10-01 actual OS process-tree proof

FIFTH preserves the original native guards, limits and budgets, saves independent
held exit evidence, releases all duplicate and original Popen references, then
requires fresh exact empty inner/outer Job inventories and ActiveProcesses=0.
Its first success launch completed natively, but the root reader rejected the
PowerShell 7 Int64 counters through an Int32-only clause. That NOT_ACCEPTED
namespace remains unchanged. SEVENTH changes only those two clauses to exact
Int32 or Int64 before the same zero comparison; booleans, strings, floating,
decimal, null and nonzero values remain rejected.

Four fresh, serial root cases subsequently passed on the same FIFTH helper:

| OS-only case | Complete externally observed process exit | Held native exits |
| --- | --- | --- |
| success | 1.4548503 s | S/R/C = 0/0/0 |
| timeout | 1.5715251 s | S/R/C = 0/91/91 |
| parentcrash | 0.5590775 s | S/R/C = 17/0/0, actual observations |
| cleanup | 0.6010374 s | S/R/C = 0/91/91 |

Every case completed within five seconds without external kill or outer rescue,
with actual held birth/signal/exit proofs and fresh empty outer Job readbacks.
Success/timeout/cleanup also retained fresh empty inner readbacks. Parentcrash
does not reconstruct the closed inner Job: its state is UNKNOWN_CLOSED_HANDLE.
One CPU, BELOW_NORMAL, private gates and original membership criteria remain.

The root capsule is
`validation-results/windows-job-os-proof-root-20261001/four-case-fifth-parentfix-20261001`.
Report SHA256:
`d894f4c47103e06949db3635bcbfe0b7b93f7904059a2e31fccdfedb6dbb6d27`;
seal SHA256:
`ebd25d775f8b1ea7c5e0ded2b527d277dd19983036aba8fc1710304e6c530935`.
Its 77 file pins bind the actual native/root evidence, helper package and parent.
This is OS-only acceptance. Application supervision, the old legacy 44/46,
full Windows/Linux regression, native sources and trading acceptance are not
upgraded. The original closed application tools remain immutable; a separately
reviewed successor must prove the original whole-process 45-second application
budget before any application replay resumes.

## 2026-10-01 application startup and account-source semantics

The application successor remains source-only. Its review found missing shared
dependency checks, a worker cwd handoff, native scalar/clock joins and the need
to observe the PowerShell parent's actual exit after its last receipt I/O.
Those findings do not reopen the original closed tools or retroactively accept
old application runs. The initial PowerShell slash concern was a root display
reading error: direct parser AST measured all three literals as one character,
codepoint 92, with zero parser errors. It is not a source defect.

Root inspected the actual original FrozenImporter code without importing or
executing `site` or any application. All 30 code objects were compared with
the pinned disk source: two membership constants are tuples in the frozen
runtime and frozensets in the source. Their types are preserved and source
equivalence remains false. A typed fingerprint of the actual frozen code is
`7085a4aa5e650dc01f527003c916883d706d7add9e1cb485f633ba49a8579c96`.
GetModuleHandleW/GetModuleFileNameW also measured the actual loaded
`python312.dll` path and SHA256
`c1ce6d603041759061139f482c4b90c4ac9db676e30abf52fda66a794aab1bd0`.
The raw source-inspection receipt is
`validation-results/original-frozen-site-root-inspection-20261001/observations.json`,
SHA256 `c9249b67490f38b1c7e2e0fea3c43825eca119dd818916b60c4f767183043314`.
This fixes the description of the original runtime; it is not site activation,
application acceptance or complete installed-dependency validation.

Root independently verified all 11 files of the positions-history official
source research and recomputed all 8 section and 37 field locators against
the retained original HTML. Readback SHA256:
`4fd0205c42db71461ad3d778631e64b1b4ba69d779ee4b9ac492deac46d23227`.
Current bills row `ts` is balance-complete-update time; historical export
response `ts` is request time. Neither is funding accrual/settlement time.
Positions-history cumulative funding/fees can provide independent amount
reconciliation, while reused position IDs and latest close types cannot prove
complete lifecycle epochs. Subaccount creation time alone does not establish
financial genesis. No authenticated account request, complete financial tail,
native portfolio authority or trade acceptance was produced by this research.

## 2026-10-05 source recovery and dependency identity

The preserved `canonical-final` repository is the selected engineering baseline.
Its `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9` commit is a direct descendant of
the currently published `develop/v1.7-entry-qualification-evidence` commit
`d9847539b5d5d03e3af385488ae11bb019fd4858`, with 16 commits ahead and none
behind. The public `main` remains `d3f206a59888ef3d72732fa30deaa8278ac72cc5`.
The V2 completion branch is local-only. A new local branch,
`develop/v2-final-completion-20261005`, was created at the preserved source HEAD;
no source commit was rewritten or pushed. The other `canonical` worktree remains
preserved separately because its uncommitted migration 0019 differs from the
0020–0021 lineage selected here.

After reviewing the 99 new source/test/documentation files and 36 changed files,
`MANIFEST.sha256` was regenerated for the selected current worktree. Its check
passes for 829 source files, raw manifest SHA256
`4ae06cbc60c4a7e3064cb8a3aafe5b8ea4583528f068d21090bbaeb85ab8fabe`. This is a
working-tree source identity, not the committed Git tree or a completed release.
The exact archive, two-form canonical/raw hash map, verified Git bundle,
dependency source identity, and secret-free environment-key snapshot are stored
under `validation-results/canonical-final-candidate-caa1588-20261005-v2-manifest`.
Archive SHA256 is
`059f2b3fe9bbbb73c2be974117f54c52c7731d52513050a542fd4821dc7ff8f8`; bundle
SHA256 is
`fa4747cf791ec4d32eee359731501cc544324cfc5173b41c5d492a1c0c668d78`. The
checkpoint records the database/account snapshot as carried-forward, not current:
the previously configured `ctcc` database was absent, `ctcc_control` was only
read at migration 0020, and authenticated exposure reconciliation remains
unknown.

The September 15 dependency refusal is attributed in the preserved provenance
record to normal Python stdin discovery counting one `/app/ctcc_v2.egg-info`
source metadata record in addition to the isolated 39-distribution inventory.
It was not a dependency-version change. The strict verifier's exception is
limited to that matching root project metadata record; other missing, changed or
extra distributions still reject. Separately, the October 4 locked Windows
environment initially lacked `tzdata==2026.4`, which is present in the reviewed
Windows lock. The retained repair installed only that locked wheel; no source,
manifest or other dependency version changed. Its first targeted test attempt
also exposed a contaminated `PYTHONPATH` that let the real source metadata enter
a synthetic temporary-source test; the clean rerun passed 23 tests with two
explicit Linux-only symlink skips. Both original attempts and the correction are
preserved in `validation-results/canonical-source-reviewed-20261004-dependency-repaired-v10`.
The current Windows lock check then ran on the selected source: 41 pinned wheel
distributions and the CTCC project produced an exact 42-of-42 installed
inventory, with no excluded metadata. Lock SHA256 is
`a4b4bb7f390a62fe0f032aa23ab0c288494d61956fc91bc40ffc2fab034d6f16`. The
verifier explicitly reports `accepted_full_validation_baseline=false`; the
recorded 40-vs-39 historical baseline is not reused as this branch's full
validation contract, and the check was not downgraded to a warning.

The scoped Windows source-selection suite completed with 60 passed and two
explicit skips for native symlink cases reserved for Linux acceptance. The
covered modules are account observation index, manifest, and release dependency
verification. Full Windows/Linux regression, PostgreSQL acceptance, exact-source
Docker, G1–G12, current authenticated account/public-source acceptance, Demo,
OOS, Shadow, Micro Live, CI, main merge and release remain unaccepted. Evidence
and raw test output are sealed at
`validation-results/source-selection-verification-20261005`; this continuation
does not change order authority or claim CTCC completion.

## 2026-10-05 shared clock and account-time boundary repair

The targeted public-source tests exposed an import-boundary violation: the
public host clock depended on account-specific clock issuance, account proof
depended on candle receipt types, and account source code imported the broader
market collector for its unauthenticated clock probe. Shared canonical JSON,
hash and native-clock primitives now live in `app/domain`; account clock
issuance is account-scoped; and account time evidence has its own versioned
plan/receipt and a fixed one-request `GET /api/v5/public/time` adapter. The
adapter pins the global OKX host, verifies TLS, disables environment proxy,
authentication, redirect and retry paths, retains bounded raw evidence and
fails when exchange time falls outside the measured request/body interval. The
account proof, policy, trace and readback schemas were versioned to V2; prior
V1 companions cannot be relabeled as this contract.

The first public-data runtime rerun recorded 11 failures and 75 fixture errors.
The common cause was a stale synthetic test hook that patched the former
`public_clock.native_os_clock` alias after native acquisition had moved to the
domain adapter. The tests therefore sampled the actual host clock and correctly
failed closed. The fixtures now patch the real domain adapter; production clock
checks were not relaxed. The original failure and the passing rerun are both
retained under `validation-results/public-data-runtime-boundary-tests-20261005`
and `validation-results/public-data-runtime-boundary-tests-20261005-rerun`.

Focused Windows Python 3.12 tests used the pinned
`requirements/validation-windows-py312.lock` in a separate temporary
environment. Results: account time/proof 69 passed; public clock and import
boundary 158 passed; public-data/owned-runtime 146 passed; and the native
source, account clock, public capture and receipt-storage group 88 passed with
one documented skip. These are fixture and unit results only: they do not prove
live host-clock health, authenticated account completeness, public-source
availability, PostgreSQL acceptance, Demo execution, OOS, Shadow, Live or
release readiness. Full Windows/Linux regression and all later acceptance
stages remain unaccepted.

The subsequent offline qualification-core run passed 437 cases with two explicit
Windows skips for tests that require POSIX six-file disk publication. The skips
are not interpreted as Windows publication evidence. Its JUnit XML is retained
at `validation-results/qualification-core-boundary-tests-20261005/junit.xml`
with SHA256
`e5b931d7fdff57e66d52b3de73f9a2696f09a1d3f1084fdad9b810c854484f7a`. This
suite covers qualification ledger contracts, submission intents, native
recheck, one-shot execution boundaries, bootstrap boundaries and the Demo
qualification transport boundary; it does not establish PostgreSQL integration,
exact Docker, source completeness, Demo acceptance or any Live acceptance.

## 2026-10-05 post-restart clock spot check

A low-load host check observed the Windows time service as Running with Automatic
startup, the Taipei time zone at UTC+08:00, and a monotonic Stopwatch counter
advancing during a bounded sleep. One unauthenticated, no-proxy, no-redirect,
no-retry request to `https://www.okx.com/api/v5/public/time` returned HTTP 200.
Its exchange timestamp was between the measured request start and body completion,
so this single request satisfied the causal-order check. W32Time status reported
`time.windows.com`, stratum 5, last sync at 2026-10-05 05:52:42 Taipei time, and
a 512-second poll interval. The unprivileged full configuration query was denied.
A follow-up read-only elevated query produced no result and was interrupted; no
service setting was changed. Full clock configuration and sustained stability
remain unverified.

A later low-load check at `2026-10-05T10:40:48Z` again observed W32Time as
Running/Automatic and the Taipei zone at UTC+08:00. One direct request to
`https://openapi.okx.com/api/v5/public/time` used no proxy, redirect, or retry;
the returned server time `10:40:48.186Z` fell between request start
`10:40:48.034710Z` and body completion `10:40:48.282161Z`. The raw response and
bounded report are retained at
`../validation-results/host-clock-after-restart-20261005/`; report SHA256 is
`ca55fd7e8aae520071f9320ff86e7a8f00dddae99f6bc52e5a60b3625d3acef2`, and raw
body SHA256 is `4e86a600ca4dec8748711a4ef831978e8a023f1ddbf4aa6aafd355690f2c213d`.
This confirms only a one-request host-clock comparison; it does not establish
market-source completeness, provenance, or sustained thermal stability. No
Windows time setting was changed, and no account request or order write occurred.

The bounded Windows Python 3.12.14 clock-boundary suite passed 193 tests with
zero failures, errors, or skips. Pytest warned that Windows denied creation of
`.pytest_cache` in the checkout (WinError 5); the test run completed successfully.
JUnit and hashed evidence are stored under
`../validation-results/host-clock-postrestart-20261005/`. This does not establish
public-market completeness, account provenance, or trading readiness.

## 2026-10-05 OKX account pagination review

The current official OKX API guide documents `GET /api/v5/trade/fills-history` as
covering the last three months and paginating with `billId`; account bills also
use `billId`, while order-history uses `ordId`. The guide distinguishes trade
match time (`fillTime`) from record-generation time (`ts`). The capture contract
keeps cursor identities separate and maps those timestamps to `trade_match` and
`record_generation`. Missing terminal pages, duplicate identities, non-advancing
cursors, and page-chain mismatches remain fail-closed. See
`https://www.okx.com/docs-v5/en/`.

A narrowly selected Windows fixture group passed 31 tests with zero failures,
errors, or skips. JUnit and hashes are under
`../validation-results/trusted-account-pagination-20261005/`. A broader five-file
unit run was interrupted before completion after sustained CPU use while the
laptop fan condition was unconfirmed; it provides no pass/fail claim. No private
account requests were made. Region availability, exact account identity, complete
history, PostgreSQL, and Demo acceptance remain unverified.

## 2026-10-05 HighVol installer static validation

Before the identity-set repair below, the fixed package verifier accepted the
then-current six-file package identity and native PowerShell parser, with
`dry_run=true`, zero external calls, and no deployment. The later repair adds
`test_installer_restart_disarm.py` to the required set, making the current
identity seven files and superseding that earlier count.
Five Windows unit tests passed, including deterministic repeated dry-run,
rejection of the original `$Mode:` parser error before script execution, and
fail-closed rejection of changed, missing, or duplicate package files. Installer
script SHA256: `aef8d7b736fc431d9b52e6c0df0b037c0f729a6743fd69cfc3200d2823a32c5c`.
Package identity SHA256: `87c3110104cb6991f5deeefe47cb7cdabd5a784e50afa772928cd4dede3e1b9b`.

`PSScriptAnalyzer` is unavailable. The verifier reports
`canonical_qualification_integration=NOT_ACCEPTED`. No Docker image build,
activation, install/reinstall/upgrade/rollback, Demo access, or exchange call was
performed. Those stages remain unaccepted. JUnit and hashed evidence are stored
under `../validation-results/highvol-installer-static-20261005/`.

## 2026-10-05 PostgreSQL 0021 offline downgrade contract

The DB0021 downgrade contract suite passed 10 modeled ordering cases:
ACCESS EXCLUSIVE NOWAIT locks precede the empty-history guard and reverse-order
drops, and the modeled writer cannot be lost between guard and drop. The test
explicitly does not establish PostgreSQL execution. No PostgreSQL service or
process was found in the inspected host state. Actual DB0021 integration, DDL,
locks, concurrency, downgrade, re-upgrade, and crash-recovery acceptance were
not run. JUnit and hashed evidence are under
`../validation-results/postgres-0021-downgrade-contract-20261005/`.
PostgreSQL acceptance remains open.

## 2026-10-05 account observation fail-closed unit checks

The account observation index unit module passed 29 tests with zero failures,
errors, or skips. It covers unknown execution time and missing cashflow operands
remaining unknown, late/conflicting source invalidation, duplicate capture
denial, and unsupported fee-currency preservation. These are unit replay
contracts only; they do not exercise DB0021 repository triggers, PostgreSQL
locks, restart recovery, authenticated account data, or Demo execution. JUnit
and hashed evidence are under
`../validation-results/account-observation-index-unit-20261005/`.

## 2026-10-05 bounded public-source capture

One unauthenticated public-only capture was made for `BTC-USDT-SWAP` using the
canonical V2 public collector, with proxy and redirects disabled and retries set
to zero. It recorded 13 REST requests and one WebSocket subscribe/read/close
cycle. Offline semantic replay read back 200 confirmed candles each for 4H, 1H,
15m, and 5m. The journal has 119 ordered events; monotonic request chronology
passed for all 13 request chains. The recorded public response-header names were
limited to `content-length`, `content-type`, and `date`; the metadata scan found
no credential-like header names.

This is source-capture evidence only. The packet remains `DENY`:
`source_authenticity_verified=false`, `original_source_verified=false`, and
`measured_availability_eligible=false`. No account endpoints, candidate,
qualification, G1–G12, execution authority, reservation, or order were involved.
The capture therefore does not satisfy Trusted Public Source or any trading
acceptance gate. The wrapper failed to persist its result after passing a path
where the replay API requires a controlled directory handle; the separate
offline verifier replayed the already captured journal successfully. The
wrapper was corrected to use the controlled directory API and passed syntax
validation. No second network capture was made.

Evidence, raw-response journal, offline replay, and checksums are stored under
`../validation-results/public-source-probe-20261005-v1/`.

## 2026-10-05 Demo/Live transport-boundary regression

Two targeted unit modules passed 65 tests with zero failures, errors, or skips.
The tests use mock transport only and confirm that unqualified Demo entry routes,
manual/automation calls, and direct Live order transport are rejected before
exchange HTTP dispatch; maintenance-write retries remain fail-closed. JUnit and
hashes are stored under `../validation-results/final-authority-boundary-20261005/`.

This proves denial behavior only. The production issuer that binds original
qualification, post-G12 fresh recheck, atomic risk reservation, durable intent,
and final current guards into a one-use Demo dispatch capability is still
absent. New Demo entries therefore remain denied; Demo acceptance is not passed.

## 2026-10-05 risk and margin guard regression

The targeted dynamic-risk, risk-engine, and Demo aggregate-margin unit modules
passed 45 tests with zero failures, errors, or skips. Coverage includes explicit
`risk_score=0` preservation, the 3/5/8/10/20x ceiling ladder and 20x downgrade
conditions, cost-adjusted risk, 0.5% structural risk, 1% portfolio stop-risk,
300-USDT buckets, the 60% aggregate-margin ceiling, a late guard recheck, and
EStop for over-cap exposure. JUnit and hashes are under
`../validation-results/risk-cap-guards-20261005/`.

These unit and mocked service checks do not prove that the currently unavailable
trusted qualification issuer enforces those caps, and they grant no order
permission. The issuer and guarded Demo execution path remain unaccepted.

## 2026-10-05 full-public V2 G1 replay tests

The targeted `tests/unit/test_public_market_data_v2.py` module passed 58 tests
with zero failures, errors, or skips. It exercises the V2 raw-packet replay and
shared G1 arithmetic using synthetic transport, TLS, and storage fixtures. JUnit
and hashes are under `../validation-results/public-market-g1-v2-unit-20261005/`.

The live V2 owner verifies native TLS and records peer/host evidence, but the
persisted packet and offline replayer intentionally retain
`source_authenticity_verified=false`; packet replay alone cannot prove the
original live owner. An owner-derived source attestation is not yet integrated
into the qualification chain. These unit results are not Trusted Public Source,
PIT, G1–G12 production, Demo, or Live acceptance.

The actual 2026-10-05 public capture separately passed structural TLS evidence
checks for all 13 REST response events and its one WebSocket connection: each
records the owned native TLS classification, expected OKX hostname, TLS version,
and a SHA256-shaped peer certificate digest. This supports transport provenance
for that capture, but offline journal replay still does not prove the original
runtime owner; admission remains `DENY`.

## 2026-10-05 owned-public V2 handoff and G1 replay regression

The targeted `tests/unit/research/test_owned_public_runtime_v2.py` and
`tests/unit/test_public_market_data_v2.py` modules completed with exit code 0:
59 owner-handoff cases and 58 G1/raw-replay cases (117 total). The runner emitted
one `PytestCacheWarning` because this checkout could not write its `.pytest_cache`;
there were no test failures. The tests use mocked transport/TLS/storage and
synthetic market data. They confirm that one-use owner handoff checks remain
separate from offline G1 replay, which continues to report
`source_authenticity_verified=false` and `admission=DENY`.

No production profile was promoted: the only pinned full-public numeric profile
is explicitly synthetic/test-only, and no production fee source or native
qualification issuer is registered. These results do not establish trusted
public-source acceptance, PIT, G1–G12, Demo, or Live acceptance.

## 2026-10-05 account-source boundary unit regression

The targeted account current-source verifier, native proof, and observation-index
unit modules passed 108 tests in 59.34 seconds, with no failures or skips. The
run used the isolated workspace interpreter, disabled pytest cache writes, and
did not start PostgreSQL or contact OKX. This verifies those local parsing,
clock/proof, and index contracts only; it does not establish complete paginated
account history, exact live account identity, a materialized portfolio risk
snapshot, Demo reconciliation, or production acceptance.

## 2026-10-05 HighVol/Momentum installer safety review

The reviewed installer package identity was checked before editing and the
original bytes were preserved at
`../validation-results/highvol-installer-original-20261005.zip` (SHA256
`5b847fd74310046a5d0c0e13377bc0a03a0f960eb770f0ad837b38314c4761ec`). The
PowerShell parser and ambiguous variable-colon scan pass; PSScriptAnalyzer is not
installed. Six new mocked controller/installer guard tests pass, Ruff passes for
the controller and guard tests, embedded package pins and the seven-file package
identity verify, and the default installer path was executed and correctly
stopped before Docker with
`CANONICAL_V2_DISPATCH_INTEGRATION_REQUIRED`.

The installer no longer restores prior Arm/running state after a restart or
rollback. Both paths recheck flat/disarmed state, and the default deploy path is
blocked because the installer still targets a fixed older image and does not
use the canonical G1–G12, post-G12 recheck, reservation, intent, and final-submit
authority chain. The offline `-VerifyOnly` Docker build, idempotent/reinstall/
upgrade/rollback runtime tests, PSScriptAnalyzer, and legacy high-volatility
profile tests remain unverified. The legacy profile test cannot collect against
this source tree because its patched `demo_high_volatility_policy` module is
absent. No installer deployment was run.

The canonical tree's `ruff check app tests scripts` static lint passed on
2026-10-05. This is a lint result only and does not substitute for the still
unrun final unit/integration, PostgreSQL, Docker-hermetic, Linux, or Windows
regression suites.

## 2026-10-05 MIE Gate 3 contract regression

The current canonical `tests/unit/mie` suite passed 374 tests in 32.46 seconds
with no failures or skips. The run used the isolated workspace interpreter and
disabled pytest cache writes; it did not download or open market archives. It
validates computational contracts and synthetic replay only. There is still no
production candidate/trial freeze, independently qualified row-level PIT
source, fresh sealed holdout receipt, or formal OOS evaluation; Gate 3 remains
unpassed and Gate 4 remains blocked.

## 2026-10-05 low-load clock and submit-boundary recheck

The current host reports W32Time Running with Automatic startup and Taipei
Standard Time (UTC+08:00). The canonical native clock v2 observer returned an
accepted observation at 2026-10-04 23:37:39.971671 UTC and validated the pinned
Windows clock profile, timezone conversion, monotonic clock domain, and fresh
W32Time status. A separate no-proxy, no-redirect, single unauthenticated GET to
OKX `GET /api/v5/public/time` returned HTTP 200/code 0 with server time
2026-10-04 23:35:52.333 UTC; it fell inside the local request window
2026-10-04 23:35:52.1882495–23:35:52.4222596 UTC (232.91 ms RTT; server was
27.75 ms ahead of the local midpoint). The official API guide defines this
endpoint as API server time in Unix milliseconds. No clock tolerance or Windows
service setting was changed. This is a one-off clock diagnostic, not a retained
R5 market-source receipt, account evidence, or sustained thermal-stability
acceptance.

A source audit enumerated the manual Demo order route, SafeDemoAutomation manual
and scheduled order calls, the shared Demo service, and the direct private REST
client. They converge on `enforce_demo_submission_boundary`; the private client
checks before signing and again immediately before transport. New Demo order,
batch, algo, amend, and unclassified writes remain denied. The Live execution
client likewise denies new-entry writes. D0 still has no trusted `READY` issuer,
and its independently read durable intent is explicitly diagnostic rather than
execution authority. The Demo qualification transport and Live execution REST
boundary suites passed 65 tests combined, with no failures or skips. They used
mock transport only; no account request or order write was sent.

## 2026-10-05 dynamic-leverage configuration cap hardening

The structural dynamic profile previously allowed environment overrides to
raise the per-position margin bucket above 300 USDT and aggregate portfolio
margin above 60%, despite the selected rollout values. Settings validation now
rejects either increase while allowing tighter caps. Regression cases cover
the two rejected increases and lower valid values. Source inspection confirms
the existing runtime applies the aggregate margin limit during sizing, after
contract rounding, after leverage configuration, and at the final synchronous
pre-submit callback. The feature remains disabled by default and no runtime
configuration or position was changed.

The Python 3.12.14 parent-workspace environment passed the Windows dependency
lock check with an exact 42/42 package inventory; the verifier still records
`accepted_full_validation_baseline=false`. Targeted regressions passed: 26
dynamic-leverage/structural-risk cases, 132 settings/aggregate-margin cases,
and 65 Demo/Live transport-boundary cases, all with zero failures or skips.
The one-shot, dispatch-ownership, and post-G12 recheck focused suites then
completed 309 passed and 2 skipped in 566.48 seconds. Those skips remain skips;
this selected unit run is not the full Windows acceptance suite.
Ruff passed on the changed settings and regression-test files. These focused
checks validate the affected behavior only; the final full Windows suite,
PostgreSQL, Docker-hermetic and Linux regressions remain unrun. No dependency
installation was needed, and the dynamic feature remains disabled by default.

## 2026-10-05 canonical workspace credential snapshot

The canonical source root has no `.env` file, and the current process exposes no
OKX, Notion, database URL, Redis URL, API-token, or Live-trading environment key
names. Secret values were not read. This workspace therefore has no available
credential session for authenticated Demo/Live account acceptance or Notion
property readback; this observation does not describe separately deployed
machines or containers.

## 2026-10-05 HighVol installer identity-set repair

The reviewed installer package identity contains seven required source/test
files, including `test_installer_restart_disarm.py`, while the canonical offline
verifier still required only six. This caused a real package dry-run to reject
the reviewed identity before parsing. The verifier and its fixture now require
the same seven-file set. The native verifier regression passed 5 tests; the
actual reviewed package dry-run now reports source identity and PowerShell
parser `PASS`, seven verified files, zero external calls, and no deployment.
The reviewed package's restart/disarm controller suite also passed all 6 tests.
PSScriptAnalyzer is unavailable. The old malformed `$Mode:` interpolation
remains only in the preserved historical installer checkpoint; the reviewed
installer parses successfully and contains no such interpolation. Its own
identity record still explicitly says canonical qualification integration is
`NOT_ACCEPTED`; no install, upgrade, rollback, service change, or order was run.

The reviewed package's target-pin audit compared all seven expected source
files with the current canonical worktree: three pins match, three existing
files differ, and `app/strategies/demo_structure_policy.py` is absent. The
result is `NOT_COMPATIBLE_FOR_DIRECT_EXECUTION`; the patch was not applied.
This is evidence against running the archived installer patch, not evidence
that HighVol/Momentum has been integrated into the canonical qualification,
reservation, recheck, or submit path. The machine-readable comparison is
`../validation-results/highvol-target-source-compatibility-20261005.json`
(archive SHA256
`5b847fd74310046a5d0c0e13377bc0a03a0f960eb770f0ad837b38314c4761ec`).

## 2026-10-05 account capture v5 and current-source policy v2

The current OKX algo pending query now uses only the four documented `ordType`
values (`conditional`, `oco`, `trigger`, `move_order_stop`). The v4 plan's
eight-type capture remains available only for byte-pinned historical replay;
the owned collector rejects that retired plan before transport. The current v5
plan binds its 34-stream inventory and distinct packet/plan identities. The
current-source verifier now emits observation schema v2 and a policy-v2 digest
that explicitly binds the v5 plan contract, current stream list, inventory
stream list and algo types. Historical receipts are not relabeled. The scope
follows the [OKX algo order list](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list),
[ordinary order list](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list)
and [pagination semantics](https://www.okx.com/docs-v5/trick_en/#pagination).
Details and limits are in [account capture v5](qualification_account_v5.md)
and [current-source verification](account_current_source_verifier.md).

The selected account-capture/source regression passed 1,193 tests after the
v5 stream correction, before the final policy/receipt metadata version bump.
After that bump, but before the later v5-only current-source/native-proof
restriction, the directly affected current-source, native-proof and portfolio
component/runtime tests passed **102 tests** in 45.44 seconds; the
retained JUnit XML is `../validation-results/account-source-v5-policy-v2-core-20261005.xml`
(SHA256 `BB5D81D95BE84D813BF61F7F08050A0C4CFB80DB94466E718821A1F946F3FCC5`).
It reports zero failures, errors or skips and includes three harmless
`record_property`/JUnit-family warnings. Ruff passes on all changed account
modules and tests, and `git diff --check` exits cleanly with only existing
CRLF conversion advisories.

A separate policy-v2 canonical-scope contract regression passed 14 tests in
5.63 seconds with zero failures/errors/skips; its JUnit XML is
`../validation-results/account-current-source-policy-v2-contract-20261005.xml`
(SHA256 `E599A91E25A2A8D8EA83904E5C69355FC3DFEF3FD8E1C29BCBA5AC5FB626B022`).

A broader 168-test downstream consolidation was interrupted at about two-thirds
completion because the host had previously shut down from overheating and its
temperature sensor denied access; it is **not** counted as a pass. No private
account request or order was sent. All account tests here use synthetic
records. Live account completeness, Demo acceptance, PostgreSQL integration,
full Windows/Linux regression and production readiness remain unverified.
The machine-readable working-tree receipt is retained at
`../validation-results/account-source-v5-policy-v2-20261005.json`.

## 2026-10-05 strict dependency identity reread

The current Windows lock verifier was rerun against this source: all 41 locked
wheel distributions plus the CTCC project metadata matched the installed
42-entry inventory exactly, with no excluded metadata. The lock SHA256 remains
`a4b4bb7f390a62fe0f032aa23ab0c288494d61956fc91bc40ffc2fab034d6f16`. No
dependency-version drift was found and no lock was changed. The verifier still
reports `accepted_full_validation_baseline=false` by design: the exact source
has not passed its full Windows/Linux, PostgreSQL and hermetic Docker gates.
That value is not downgraded to a warning or treated as a package mismatch;
baseline acceptance remains pending those actual runs.

## 2026-10-05 current account collector transport guard

Source review found that the collector still allowed an older regional plan to
use the normal private HTTPS transport. Those historical inventories include
algo request types no longer documented by OKX. The collector now stops every
non-v5 plan before its first real private request; old plans remain exercisable
only through the internal `MockTransport` synthetic test seam. The v2
current-source verifier also rejects historical v2/v3/v4 plan packets rather
than emitting a current flat diagnostic from their narrower or obsolete scope.
An actual isolated HTTPS client test confirms the legacy plan is rejected and
closed before any request; a separate v5 mock capture confirms the current
34-stream plan sends only the four documented algo values.

The focused collector, v5/historical packet and current-source regression passed
**449 tests** with zero failures/errors/skips in 26.44 seconds. Its retained XML
is `../validation-results/account-v5-live-collector-boundary-fixed-20261005.xml`
(SHA256 `947FBCA564991C1EE1985FBFED18B8DB478F73CBB11874A9D81189D7716A4F10`).
An earlier run had one misplaced relabel assertion in a v5 positive test; that
test correction is complete, the initial failing XML is preserved separately,
at `../validation-results/account-v5-live-collector-boundary-20261005.xml`
(SHA256 `E7DEB39ED1E49BE5ECB62C8EE2729C775BEAD796F0D4A84FF589FB6B488DAE0D`),
and only the rerun above is counted as PASS. These tests use synthetic records
and mocks; no private account request or order was sent.

The owned account-runtime suite also passes **29 tests** after updating its
native-TLS negative case to use the current v5 plan. The earlier version used a
legacy regional plan and was correctly stopped before transport; that obsolete
expectation is preserved in
`../validation-results/account-runtime-legacy-mock-compat-20261005.xml`
(SHA256 `CD38A06C8AE89BD194B13CD07D72DF7C1FDABE3EBE9D9850EACA2A5B443059D8`).
The corrected v5 TLS suite result is
`../validation-results/account-runtime-v5-transport-compat-fixed-20261005.xml`
(SHA256 `8A290914055CC59172B58F47F0850DF2EDED28B86F4AF1DABD166AE0885ECF72`)
with zero failures/errors/skips. It still denies a response that has no actual
TLS peer evidence; the test uses a mock response and does not perform network IO.

The current-source v5 restriction was also carried into native account-proof
replay. Its first 103-case dependent run had 51 failures because synthetic proof
fixtures still produced v3 account packets; those packets can no longer create
a current-source flat diagnostic. The fixture now captures the exact v5 plan
and packet throughout the native-proof chain. The portfolio diagnostic also
retains the unknown/non-SWAP metadata gaps from v5 history rather than treating
SWAP-only instrument metadata as coverage for other products. The corrected
current-source, native-proof and portfolio regression passed **103 tests** in
111.74 seconds with zero failures/errors/skips (three JUnit-family warnings).
Its XML is `../validation-results/account-v5-native-proof-portfolio-final-20261005.xml`
(SHA256 `4B6199818E290CAD00B97A5B44D4B54029385CD2C931BCA270875D1F4E0A521A`).
The prior failure record remains at
`../validation-results/account-source-v5-policy-v2-core-final-20261005.xml`
(SHA256 `0D8FDEA642D78F91CBA264AA5BF948E94231278C7A056C0E207D89ACA438763C`);
it is not counted as accepted evidence.

## 2026-10-05 final account-v5 evidence reconciliation

After enforcing the v5-only current-source, collector and native-proof boundaries,
the retained targeted runs report 449 collector/current-source tests, 29 owned
runtime tests and 103 current-source/native-proof/portfolio tests passing, with
zero failures, errors or skips. Their JUnit identities and SHA256 values are
recorded in `../validation-results/account-source-v5-policy-v2-20261005.json`.
The exact changed account source/test scope passes Ruff; `git diff --check` exits
0 with existing CRLF conversion advisories. `MANIFEST.sha256` was regenerated
and checked over 834 canonical source files. This worktree receipt remains a
partial engineering record: authenticated account ingestion, complete portfolio
materialization, full platform/database/container validation, Demo acceptance,
Live acceptance and release gates are still not accepted.

## 2026-10-05 owned native live public-market V2 capture

After diagnosing the earlier denied invocation, one fresh V2 public-only
capture for `BTC-USDT-SWAP` completed under the exact Windows dependency lock
(42/42 installed packages). The earlier calls stopped before transport because
the journal root did not yet exist and the system Python lacked project
dependencies; neither sent a network request. The successful invocation used
an existing empty evidence directory, zero HTTP retries, no environment proxy,
and no redirects.

The captured journal contains 125 ordered events across 13 REST requests and
one WebSocket connection. Native TLS peer/hostname evidence passed structural
checks for all 13 REST responses and the WebSocket (`www.okx.com` and
`ws.okx.com`, TLS 1.3). The packet includes 240 confirmed candles each for 4H,
1H, 15m and 5m, with the raw page-boundary evidence retained. Volume fields
remain explicitly separated as contracts, base currency and quote currency.
Funding, mark, ticker, books5, open interest and one live WebSocket ticker were
also captured. The journal has 196 files totaling 639,131 bytes; semantic
replay and packet readback passed. Packet SHA256 is
`d316683557f3e8945479de67b9c9a5b3bb5ffb3717de0a360ef0449539495af5`.

This remains a live source-observation result only. The packet deliberately
retains `admission=DENY`, `source_authenticity_verified=false`,
`original_source_verified=false`, and `measured_availability_eligible=false`;
account completeness and execution authority are false. It supplies no
historical first-availability proof, predictive PIT eligibility, candidate,
G1-G12, reservation, intent, Demo order or Live acceptance. No private account
request or order was sent. The immutable capture receipt and review are under
`../validation-results/public-market-live-20261005/`; the review SHA256 is
`4461bc88e39ea2cd6f5509c824990a0e910e0829a92ea83c92ab14cf0acd20eb`.

## 2026-10-05 zero-valued risk score regression

The standard risk evaluator selects `risk_score` whenever it is not `None`, so
a mathematical risk score of zero cannot fall back to a higher raw score. A
direct regression now checks `score=99, risk_score=0` against a minimum score of
72 and requires rejection with zero approved quantity. The focused risk-engine
suite passed 8 tests and Ruff passed for the test module. This proves only the
standard evaluator's zero-score behavior; it does not establish qualification,
reservation, Demo, or execution acceptance.

## 2026-10-05 Demo pending-algo REST pagination correction

Review against the current [OKX account and trading API guide](https://www.okx.com/docs-v5/en/)
found three legacy read-scope gaps: the private REST wrapper sent
`ordType=conditional,oco` as one algo query, fetched only one page, and forced
unscoped positions and pending orders to `SWAP`. The algo endpoint takes one
`ordType` per request and paginates by `algoId`; ordinary incomplete orders use
`ordId`; unfiltered positions and pending-order calls cover the account without
an `instType` restriction. The wrapper now queries all four documented algo
types separately, reads pending orders without product filters, and follows
each exclusive cursor through an explicit empty terminal page. Unscoped position
reads no longer restrict product type; instrument-scoped reads use only the
requested `instId`. Malformed, non-descending, repeated, non-advancing, or
over-limit cursor chains are rejected. A short page is not treated as terminal.

The focused private REST module passed 10 tests with zero failures or skips;
Ruff lint and format checks passed, and `git diff --check` completed with only
the pre-existing CRLF advisories. The tests use `MockTransport`. This fixes the
read request scope and bounded page traversal only: it does not authenticate an
account, prove same-revision or complete portfolio state, replace the retained
account-capture journal, or grant order authority. Demo and Live entry remain
fail-closed at the final transport boundary.

The same review found legacy account parsers converting missing required balance
or position numerics to zero and replacing a missing balance `uTime` with local
wall time. Those fallbacks are removed for balance and position snapshots. They
now reject absent required numbers, empty details, missing identity fields, or a
missing source update time; an explicit source value of `0` remains zero. The
focused parser regression passed 15 tests with zero failures or skips, and Ruff
lint/format checks passed. These mock/source-parser tests do not establish an
authenticated or complete account snapshot.

## 2026-10-05 native Windows evidence storage and outbox regression

On Windows, the evidence-storage and outbox unit suites passed 425 tests, with 10 POSIX-only cases skipped and zero failures or errors. The skip reasons are POSIX symlink/hardlink and flock proofs reserved for Linux; Windows junction and native-handle cases ran on this host. The suite exercised no-clobber publication, pinned-root sharing behavior, outbox history retention, and the injected WinError 32 late-denial path. In that path the durable state journal remained byte-identical, the root commit marker stayed absent, and the external adapter was not called.

Pytest cache writes were disabled. No ACL changes, exchange requests, order writes, Notion credentials, or external adapter calls were used. This is targeted Windows component regression evidence; it is not the complete Windows suite or Linux POSIX acceptance. JUnit is `../validation-results/windows-evidence-storage-outbox-20261005.xml` with SHA256 `5cacb8dd233a0c217eac18661b8339e0ba51e461ba6feedbb0ceac8e65cce5f8`.

## 2026-10-05 migration identity and current PostgreSQL state

`alembic heads` reports source head `0021`; the independent source-identity digest is `87c475dfebd5df43232a865b86491f56d60d03cd2e9f17aaa767a657ce296013`. The migration-identity and downgrade-contract unit modules passed 57 tests with zero failures, errors, or skips. These use SQLite/modeled contracts; they do not prove execution against PostgreSQL. JUnit is `../validation-results/migration-identity-downgrade-contracts-20261005.xml` with SHA256 `6052e46b7d9e59c64a93fc8752fc8cd4a331729face9574288ad6646a29276bd`.

A current Docker inspection found the pre-existing `ctcc-validation-20260928-c07dd3f4-postgres-recovered` container, labeled as recovered after a host reboot. It uses a persistent Docker volume and has no published PostgreSQL port, so it was not treated as a clean isolated test database and was not modified. The earlier host-state note above predates this discovery. Fresh PostgreSQL upgrade, schema, concurrency, downgrade, re-upgrade, and crash-recovery acceptance remain unverified.

## 2026-10-05 fresh PostgreSQL upgrade and drift verification

A new throwaway PostgreSQL 17 container using the pinned image was started
with 0.5 CPU, 512 MiB memory, no mounted host directory or persistent
volume, and a host port bound only to `127.0.0.1`. The process ran from the
system temporary directory with an explicit temporary database URL, so the
workspace `.env` was not read. Alembic upgraded a fresh database through
`0021`; an independent version-table read matched source head `0021` and
the migration-source digest, and `alembic check` reported no drift. No
exchange/account or Notion calls were made.

The subsequent eight-file PostgreSQL integration batch was interrupted
before completion because the Python test worker accrued about 120 CPU
seconds while this host exposed no usable thermal/fan sensor readings.
There is no completed JUnit report and no integration test count is claimed.
The temporary container was removed; the older recovered PostgreSQL
container and its persistent volume were not touched. Receipt:
`../validation-results/postgres-fresh-upgrade-drift-interrupted-20261005.json`
SHA256 `5495037de2f77b7b7549c57bfc29f66901cb1ab0cb096220f4b537beff71cc74`. Actual PostgreSQL migration/repository integration
acceptance remains open.

## 2026-10-05 PostgreSQL 0021 repository integration

A fresh ephemeral PostgreSQL 17 database was upgraded through `0021`; its
independently read database head matched source head `0021`, and Alembic drift
checking reported no differences. Two actual PostgreSQL repository tests
passed (zero failures, errors, or skips): normalized constraint rollback
preserved the original journal, and concurrent identical ingestion produced
at most one append. They use synthetic fixture data and made no exchange,
account, or Notion calls. The resource-limited container was removed afterward;
the older recovered database was not touched. JUnit
`../validation-results/postgres-0021-observation-index-single-20261005-1215db45.xml` has SHA256
`3f925ee15bea8303f10e71c2af8b2d492f9e502778e526bbb93fdafa20820692`.
Receipt `../validation-results/postgres-0021-observation-index-2-tests-20261005.json` has SHA256
`ef136a4c4a62a324b206a664ab00045dd8a0effe48d6250c38be0092bd3d1c9a`. This targeted result does not complete the broader
migration downgrade/re-upgrade, all repository, concurrency, or crash-recovery
matrix; the earlier eight-file batch remains interrupted with no result.

## 2026-10-05 targeted PostgreSQL migration, repository, and intent regressions

Against fresh throwaway PostgreSQL 17 databases, full source migration upgrade
through `0021`, independent source/database-head readback, and Alembic drift
checks passed in each run. The bounded database tests passed: 2 account
observation repository cases (normalized constraint rollback and concurrent
at-most-one append), 7 actual `0021` downgrade/lock cases, and 5 durable
submission-intent cases (exact-body commit/restart replay, one concurrent
consumer, rollback on late failure, and committed intent retained after
readback failure). A separate empty `0021` downgrade/re-upgrade shape case also
passed; it is repeated in the seven-case matrix, so the JUnit invocation count
is 15 with 14 distinct selected cases. All reports show zero failures, errors,
and skips.

Each database ran in a CPU/memory-capped ephemeral container with a loopback-only
host binding, no host directory/volume or workspace `.env`; all temporary
containers were removed. Test data was synthetic. No exchange/account or Notion
calls or order writes occurred. These are targeted PostgreSQL integration
results, not the full migration/repository/concurrency/crash-recovery matrix.
Receipt `../validation-results/postgres-targeted-migration-repositories-20261005.json` has SHA256 `e8ae36c9efb2143f13c90568a8a111b5124d4439767ed188ecaf0476e1ce5137`; its
JUnit references preserve each run's individual hash. The earlier broad
integration batch remains recorded as interrupted and contributes no test
result.

## 2026-10-05 PostgreSQL qualification-ledger reservation concurrency

A fresh throwaway PostgreSQL 17 database was upgraded through migration `0021`; its independently read head and migration-source digest matched the current source, and the upgrade completed without schema drift. Two focused PostgreSQL integration cases passed (zero failures, errors, or skips): concurrent workers and a renamed report could not reuse the same original event, and a failed journal insert rolled back both the reservation and account revision. Pytest reported `2 passed in 10.01s`.

The database ran in a CPU- and memory-capped temporary container with a loopback-only host port, no persistent volume or mounted host directory, and no workspace `.env`; the container was removed after the run. Test data was synthetic. No exchange, account, Notion, or order requests were made. JUnit `../validation-results/postgres-reservation-concurrency-20261005-f7039d01.xml` has SHA256 `e3ff7533da13b49bee9652455b8502488211a1e2f3566ed78de6a7e83795330e`.

This verifies these repository-level PostgreSQL cases only. It does not prove a production call chain from the post-G12 recheck through atomic reservation, durable intent, and final dispatch; full PostgreSQL migration, repository, concurrency, and crash-recovery acceptance remains open, and order authority remains fail-closed.
## 2026-10-05 one-invocation G12-to-recheck unit regression

The isolated one-shot unit suite passed 68 tests, with zero failures or errors and 2 platform-specific skips. Its six-file native POSIX publication cases were skipped on Windows with the explicit reason that Windows success is not inferred; Linux immutable validation must cover them. The tests use `httpx.MockTransport` and synthetic market/account responses. They verified the 12 evidence gates, no-clobber G12 publication/readback before new post-barrier public and account GETs, and replay into the recorded recheck. JUnit `../validation-results/one-shot-capture-20261005.xml` has SHA256 `57825f1189ad32fb91f195e8717edd9115a9bbd82f2a7ffd75198a52e2bd7b37`.

The complete synthetic account remained explicitly incomplete, so the current-risk check failed closed. Results retain `execution_authority=false`, `atomic_risk_reserved=false`, and `order_submitted=false`. This does not establish trusted live source data, complete account history, a production G1–G12 invocation, atomic reservation, durable intent, or Demo acceptance.
## 2026-10-05 dynamic leverage and aggregate-margin hard-gate regression

The current-source dynamic leverage profile module passed 12 tests with zero failures, errors, or skips. It verifies the configured 3x/5x/8x/10x/20x leverage caps while retaining a 0.5% per-trade ceiling, rejects restored higher structural risk, treats leverage as an upper bound rather than a forced tier, and confirms that a zero `risk_score` does not fall back to a high raw score. JUnit `../validation-results/dynamic-leverage-profile-20261005.xml` has SHA256 `e554127ae1d9bbdda369e32ce40f0d858a8e0c43775db48602c3959ac194fdd9`.

The aggregate-margin guard suite passed 24 tests with zero failures, errors, or skips. With synthetic accounts it verifies two 300 USDT buckets reach the 60% cap at 1,000 USDT USDT-equity and block a third; unrelated assets do not inflate the margin basis; changes after leverage and immediately before POST are rechecked; malformed or unresolved exposures fail closed; existing position size, leverage, margin, FX, pending algo and protection mismatches block or latch the stop. JUnit `../validation-results/aggregate-margin-hard-gate-20261005.xml` has SHA256 `f3bc20d599517393e48194d512af7ba563c91d9640798374489037f1ceb1a398`.

These are synthetic local tests, not an authenticated Demo-account run or production R7 reservation/intent flow. They do not authorize any order, prove deployment configuration, or complete Demo/Micro Live acceptance.

## 2026-10-05 PostgreSQL control-bound reservation and intent core

Seven selected cases from `tests/integration/test_control_bound_ledger_repository.py` passed against a fresh PostgreSQL 17 container: exact inner v2 intent replay, one-winner event and intent races, a stop change, Arm-expiry rollback at reserve and consume, and committed-intent recovery after a readback uncertainty. The tests reported zero failures, errors, or skips. JUnit is `../../validation-results/postgres-control-bound-core-20261005.xml`, SHA256 `9e81a09c78f7b1a2cbddfadec3beb68158e9e954ebff202291f3daac0ca20582`.

The pinned container was limited to 0.25 CPU, 384 MiB memory and 64 processes and bound only to loopback. No host bind or persistent host volume was used; the image declares its anonymous `/var/lib/postgresql/data` volume, and the container ran with `--rm` and was absent after the test. The runner used the system temporary directory, where no `.env` was present, and migrated the fresh database to the current Alembic head. This invocation did not independently read back `alembic_version`. Fixtures were synthetic; no authenticated exchange/account, Notion or order requests occurred. The full control-bound module, complete PostgreSQL matrix and a production R7-to-dispatch handoff remain unaccepted; the transport still denies order entry.

## 2026-10-05 Range V5 pre-reserve account-session binding

Range V5 now has a `ReservationRequestV3` / `ReservationReplayBindingV3`
contract that carries the bounded account packet and its packet/plan hashes
before reservation. The control-bound boundary verifies its exact UID,
settlement currency, and credential-session pin. Historical V2 request bytes
remain decodable; a control-bound reserve rejects V2 with
`bound_control_account_session_binding_missing`. The packet does not grant
source authenticity or execution authority.

Ruff passed for the six changed Python files; Python syntax compilation and
`git diff --check` passed. Two focused Range V5 unit cases passed in the
isolated pytest runtime: V3 G12/recheck serialization and replay round-trip,
and V2 historical decoding plus fail-closed control-bound rejection. The
console-only unit invocation did not write JUnit.

A fresh PostgreSQL 17 database migrated through `0021`. The long-direction
Range V5 wrong-session reservation case passed, as did the generic intent
session-mismatch rollback case in separate partial runs. The matching-session
Range V5 reserve-to-intent replay remained CPU-bound and was deliberately
stopped under the host thermal precaution; it has no test result and produced
no valid JUnit. A repeated run was stopped before its Range V5 cases completed.
The exact partial statuses are recorded in
`../validation-results/control-bound-session-v3-20261005.json`, SHA256
`BB65FA706AE09802F785FB657178231A80BBD01578317CBE780880675800F808`. The updated
checkpoint is `../validation-results/ctcc-v2-canonical-checkpoint-20261005-range-v3-session-binding.json`. No authenticated
exchange/account, Notion, or order requests occurred. Matching-session durable
intent integration remains an open verification item.

## 2026-10-05 reservation canonical-digest reuse

The reservation repository now serializes the immutable request once before
opening its account-scoped transaction, then hashes those exact canonical
bytes. Stored requests are still decoded and required to match the canonical
encoding before their persisted bytes are hashed on consume/readback. This
removes a redundant deep model serialization while retaining the same request
digest contract. The focused canonical-text equivalence test passed for both
long and short fixtures (2 passed); Ruff and Python syntax compilation passed.
The first test attempt exposed a test-edit scope error, which was corrected
before the passing rerun. The isolated PostgreSQL matching-session replay was
not rerun while the host thermal/fan state remained uncertain, so transaction-
level performance and replay acceptance remain unverified. No account,
exchange, Notion, or order requests occurred.

## 2026-10-05 Range V5 policy rejection diagnostic

The V5 history evaluator's wrong-policy failure message incorrectly named the
V4 contract. It now reports `exact_history_v5_policy_required`. The focused
negative test passed (1 passed); Ruff passed for the evaluator and test module.
The test passed without building market history. Pytest could not write its
local cache because the existing `.pytest_cache` path is access-restricted;
this did not affect test execution. No account, exchange, Notion, or order
requests occurred. This is a diagnostic correction and does not change any
qualification result or grant execution authority.

## 2026-10-05 Windows structural-risk unit regression

The focused `tests/unit/test_demo_structural_risk.py` suite passed all 14 tests
with zero failures, errors, or skips (0.491 seconds). The JUnit report at
`../validation-results/windows-structural-risk-20261005.xml` has SHA256
`211751b5982653ee421a449ecd3d69d6c05197768e375f88753ef7d91bf8a133`. This is
a local unit-test result for structural risk and leverage selection; it is not
evidence of a trusted account snapshot, an exchange fill, protection readback,
Demo acceptance, or Micro Live acceptance.

## 2026-10-05 account source proof and clock unit regressions

The focused Windows run of `test_account_native_proof.py`,
`test_account_native_clock.py`, and `test_account_current_source_verifier.py`
completed with 96 passed, zero failures, errors, or skips (100.043 seconds).
JUnit `../validation-results/windows-account-source-proof-20261005.xml` has
SHA256 `9efc175a3e4d95a79ea1f706d7eafe1597dcce610d980d591f2525fe7a918992`.
The run used local unit fixtures and did not make authenticated account,
exchange, Notion, or order requests. Pytest emitted `record_property` warnings
because the project uses xunit2 JUnit output; they did not produce failures.
These tests exercise account proof/clock contracts only and do not establish a
complete authenticated account snapshot, runtime integration, Demo acceptance,
or Micro Live acceptance.

## 2026-10-05 legacy environment-key inventory

The separate `C:\CTCC-V2\.env` file exists outside the canonical source. A
key-name-only inventory found 152 distinct variable names, including Demo and
Live credential slots plus token and database-password slots. No values were
read or recorded, the file was not loaded into canonical code, and no exchange
request was made. The path is currently untracked and ignored by
`C:\CTCC-V2\.gitignore`; a path-only search across that repository's local Git
history found no `.env` commits. This does not prove the slots contain valid
credentials or that the file is safe to use. The redacted inventory is
`../validation-results/ctcc-v2-legacy-env-key-names-20261005.json`, SHA256
`a3022b4edd83d3df331c9d4241adfeea2eca053671ec63c89e7f004284185de4`. Per the
acceptance sequence, no authenticated Demo request is made before offline
acceptance is complete.

## 2026-10-05 comparison with the separate `C:\CTCC-V2` working tree

The separate repository's local `main` is at `ba8dd2474aea6eca99bd627bd057f192d9927ada`
(tree `caec0f0e882e6399033ee7bb58cf3299e945de68`) and is 21 commits ahead of
its recorded `origin/main`. That commit is the merge base and an ancestor of
canonical HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`; its committed history
is already present in canonical.

The separate working tree has an uncommitted Demo-only 4H volatility-profile
change in `app/analysis/service.py` and `app/demo_automation/service.py`, a
modified manifest, and an untracked synthetic test. The change moves the Demo
automation 4H high-volatility boundary to 2%, changing regime and mathematical
core outputs; canonical does not contain it. Its focused synthetic test passed
24 cases with zero failures, errors, or skips (1.195 seconds). JUnit
`../validation-results/local-source-h4-demo-profile-20261005.xml` has SHA256
`8f9169a9405b5478436780e6da95dced9a621e4373ab3337f9c5d6d684f6faec`.
The two modified source files have SHA256s `c49f683021f143cc39830b3973672293c3bbf0f8bc68a7007422a5f6402f03dd`
and `6228024addefdca929837f1cd7b809bef223160a93bcde0ed0e7efdf1ccd02ec`;
the test has SHA256 `34af579e7459fa2423b38fa946370bb8a3bb313129ebef529bcdbd370d6c3edc`.
This semantic strategy change remains preserved in its original tree and is not
adopted into canonical on the strength of one targeted synthetic test: the
candidate policy/preregistration and full regression do not validate that new
threshold. No branch, working file, report, or database backup in
`C:\CTCC-V2` was changed by this comparison.

## 2026-10-05 native public-source capture and replay

A single public-only BTC-USDT-SWAP diagnostic capture ran from
`2026-10-05T09:11:31.140733Z` to `2026-10-05T09:11:33.263913Z`. The REST
requests used the fixed `https://www.okx.com` origin with redirects, proxy
inheritance, authentication, and automatic retries disabled. It captured 13
GET requests: two candle pages for each of 4H, 1H, 15m, and 5m; funding rate;
books5; mark price; open interest; and ticker. A public WebSocket ticker was
also captured. Candle volume units are retained separately as contracts, base
currency, and quote currency.

Offline readback verified 198 capture files totaling 640,778 bytes and a
126-event immutable journal. Replay passed. The packet SHA256 is
`a8eb58c689b83342b4430e1aa7a02379adcbbdb8ae2b2c7a8ccf2cfd47282849`, journal
head SHA256 is `a507939f2ae4a6eeb48474ca13b5cc4b7053bea04afc5745575a0d4c0f6cbee3`,
and plan SHA256 is
`d190137a339d74d6f1d62acb7e8efd37244d3afd03fd6a35de00399815b71c18`. The
readback report is
`../validation-results/public-v2-live-capture-btc-20261005-readback.json`,
SHA256 `d4f2a64c244517ade93b9ddf22016bd88d5f52a0d9734b22eddd29c0cdadc13b`.
Its file inventory SHA256 is
`6958fe05c984bb65a5167b9c5043b8c835025a8ae80df67fcf16950ad9a6eed1`.

The capture contains 240 confirmed bars each for 4H, 1H, and 15m, and 241 for
5m, with two pages per interval. The captured windows end at 4H
`2026-10-05T04:00:00Z`, 1H `2026-10-05T08:00:00Z`, 15m
`2026-10-05T08:45:00Z`, and 5m `2026-10-05T09:05:00Z`. Both local clock
observations passed the collector's bounded clock checks. The source tree at
capture was branch `develop/v2-final-completion-20261005`, HEAD
`caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, tree
`2818ed63896dbbbb7341d15550210c6837d6ac1b`, manifest SHA256
`c76e479e17a283de67508f3728d45764f36a67600e18d8c0a18cb6b98f9ae51c`.

This is evidence that the captured public packet and event chain can be read
back and replayed. It does not establish original-source authenticity or
measured point-in-time availability. No authenticated account endpoint or
order route was used. Account completeness, candidate creation, G1-G12,
measured availability eligibility, and execution authority remain false; no
order was submitted. The capture therefore does not satisfy a trusted public
source, Demo acceptance, a Shadow/Demo example, or execution acceptance.

## 2026-10-05 reviewed HighVol installer follow-up

The separately reviewed installer package at `../reviewed-highvol-installer`
passed the read-only source-identity verifier for all seven pinned files and
the native PowerShell parser. Its identity SHA256 is
`6d0a2adfcf69942f737d71dee97a80b9fef6bbeab1d40fa9362836b43bde9fe8`; the
reviewed installer SHA256 is
`4398fd48b7ab7f800ba15482d54f9937011ab69e0c2995151014e8b223f5ab7e`. The
reviewed source uses the safe `${Mode}` interpolation. The preserved 2026-09-15
installer remains unchanged and has the original `$Mode:` interpolation; the
native parser reports `InvalidVariableReferenceWithDrive` at line 68 for that
historical file (SHA256
`a397bb5345e04e2d32c86e85fc862e776ccadfa6c30bbd36edb7ad9326c34cd9`).

The reviewed package's restart/disarm regression passed 6 tests with zero
failures or errors (0.29 seconds). JUnit
`../validation-results/windows-highvol-installer-disarm-20261005.xml` has
SHA256 `740c5766b289e6c6b12481bd896d0f6a27bc418618a61c34b51a0e25df49f680`.
The separate five-case legacy HighVol policy suite did not collect: canonical
still lacks `app.strategies.demo_high_volatility_policy`, and pytest recorded
one import error. JUnit
`../validation-results/windows-highvol-installer-reviewed-20261005.xml` has
SHA256 `e87611b64cde43a798a22798b3f1947d24968533ae47f420c71779b450238f53`.
This is a confirmed integration gap, not a passing policy test. The verifier's
`-DryRun` only confirms that its own read-only identity/parser check made zero
external calls; no installer dry-run, install, reinstall, upgrade, rollback,
Docker action, or exchange request was run. PSScriptAnalyzer is unavailable.
The reviewed installer remains unaccepted for canonical deployment, and its
separate target-pin audit still says it is not compatible for direct execution.

Machine-readable source, parser, JUnit and scope record: `../validation-results/highvol-installer-followup-20261005.json`, SHA256 `a564bab6dc9ca592766b83cee8567c68b49e5d6406af6e68d8e8aa0ee50697ff`.


## 2026-10-05 official account endpoint and region audit

The current official OKX API documentation and changelog were rechecked against
the canonical v5 account plan. Its endpoint map matches the current standard
account contracts. The page-chain map is endpoint-specific and consistent:
orders use `ordId`, fills and bills use `billId`, and pending algos use
`algoId`; a cursor is exclusive, page size is bounded at 100, and every
non-empty chain requires a later empty terminal. The parser preserves `tradeId`,
fill `fillTime`, and record-generation `ts` as distinct source semantics.

The regional plan routes Global through `openapi.okx.com`, US/AU through
`us.okx.com`, EEA through `eea.okx.com`, and Turkey through `tr.okx.com`, in line
with the current [OKX API overview](https://www.okx.com/docs-v5/en/#overview) and
[2026-05-20 domain changelog](https://www.okx.com/docs-v5/log_en/#2026-05-20). However,
the current-source/native/bootstrap/portfolio admission proofs remain Global-only,
and the Live REST integration permits only Global and EEA. No registration region
was authenticated and no private account request was made.

The official [since-2021 bills archive flow](https://www.okx.com/docs-v5/en/) is not in
the current 34-stream v5 plan; the implemented source query is bounded to 28
days and ordinary bill archive to three months. The current account evidence
therefore cannot prove lifetime realized history, funding accrual, loss-streak
seed, or high-water history. The canonical checks continue to fail closed;
`TRUSTED_ACCOUNT_SOURCE` and complete `PortfolioRiskSnapshot` remain unaccepted.

The 2026-03-24 official archive API changelog also confirms that the since-2021
bills feature uses a quarterly `POST` application followed by a `GET` for an
asynchronous `fileHref`/state response. It is not a normal page chain; current
quarter data is excluded, the response is documented for unified accounts, the
download link expires after 5.5 hours, and the same quarter need not be reapplied
within 30 days. The current 34-stream owned collector remains GET-only and has
no raw archive download, quarterly coverage receipt, or merge/reconciliation
stage. The apply `ts`, CSV bill `ts`, and funding accrual time are distinct semantics.
The account risk snapshot remains denied until this separate protocol and its
reconciliation are implemented and verified. [OKX API reference](https://www.okx.com/docs-v5/en/) ·
[2026-03-24 changelog](https://www.okx.com/docs-v5/log_en/#2026-03-24).

The same official reference says `result=false` is still generating: check the
status after two hours, and contact OKX if no link is available after three.
Its post-2024-10-11 quarter-coverage example labels Q2 as `[2024-07-01,
2024-10-01)`, inconsistent with calendar-quarter boundaries. This ambiguity is
now explicitly fail-closed in the account-source notes; no quarter coverage or
Demo support is inferred from that example. The time-limited `fileHref` is not
persisted, logged, or bundled. This source clarification does not close the
archive-ingestion gap.

## 2026-10-05 R7-to-ledger and dispatch-boundary source review

Source review of the current repository confirms that `reserve_control_bound`
uses one account-scoped database transaction to lock and reread Demo control,
account and ledger revisions, reject an already-recorded event or unresolved
exposure, recompute current coverage/caps, and persist the reservation with its
control-bound journal entry. The controlled consume path repeats current
control/expiry and account-revision checks, recomputes risk against current
claims and active reservations, and writes the consumed transition and
control-bound intent in that transaction. It then opens a separate repository
session to replay the committed reservation, intent, and control binding.
Unknown commit/readback outcomes do not grant dispatch permission.

This storage path is not connected to a production `publish_and_recheck(...)`
coordinator. `one_shot.publish_capture_recheck` has no reservation/intent
dependency and returns an incomplete or denied result; the V2 public runtime
is likewise diagnostic-only. `DispatchOwnership.require_ready` always denies,
and no trusted issuer turns a readback into a one-use order capability. The
missing source/account ownership and complete account snapshot therefore keep
the R7 execution chain unavailable even though the repository contains
transactional reservation and intent primitives.

The service call sites for manual Demo order and SafeDemoAutomation converge on
the same private REST client. That transport rejects order, batch, algo, amend,
and unclassified write paths before credential signing and checks again directly
before HTTP dispatch. The Demo transport-boundary module passed 52 synthetic
cases. Three focused dispatch tests also passed, including both direction
fixtures for terminal denial and rejection of a diagnostic intent at the real
entry boundary. JUnit
`../validation-results/windows-r7-authority-deny-core-20261005.xml` records 3
tests, zero failures/errors/skips, SHA256
`7c1b06a35219ff823ef9bcee2660e1e4761f14cd3baab3bd6c1838d29ac264e4d`.
The full dispatch-contract file was interrupted at 46% because its deliberate
wait/race cases exceeded the current low-load test budget; no completed result
is claimed for that run. These checks establish fail-closed denial only. They
do not pass post-G12 recheck, reservation, durable-intent integration, Demo, or
Live acceptance, and no account request or order was sent.

## 2026-10-05 Windows durable-publication late-failure recheck

The native Windows evidence publisher uses a same-directory link operation from
the already-open, flushed file handle, so it does not reopen the root directory
while holding the exclusive publisher lease. A native Windows publication,
readback, and no-clobber regression passed, as did the injected late
`WinError 32` case. In the latter, the initial state journal remains byte-for-
byte intact, the root success marker is absent, retries cannot backfill it, and
the outbox refuses both read and dispatch. Five synthetic journal late-write
failure cases also passed without deleting successfully published evidence.

The 7-case targeted JUnit report is
`../validation-results/windows-outbox-sharing-late-failure-20261005.xml`, SHA256
`26cd234d9e03bc633101d4ff7efe39328b102e9b574d87ce7b2bbcceb9e05e0e`. The
2-case native Windows readback report is
`../validation-results/windows-outbox-native-readback-20261005.xml`, SHA256
`be764ec855b1d9b6d6ca4e953e5a637eb3cf0449247df81212b05b88fd6e0762`; its
captured output confirms real pinned publication/readback and the retained
late-denial journal. This resolves the tested Windows publisher defect. The
entire outbox regression, Linux native-storage suite, durable worker crash
matrix, and authenticated Notion outbox delivery remain unaccepted.

## 2026-10-05 account V5 focused regression

The account capture parser and owned Demo collector tests passed: 696 tests,
zero failures, errors, or skips, in 11.873 seconds. JUnit
`validation-results/windows-account-source-v5-20261005.xml` has SHA256
`57c7fb8cf83f5803b14e6eb6ddc711746a88fd8bb64b81c6e580ef260e733803`. These
tests are offline/synthetic and verify the current V5 parsing and owned fixed-GET
collector contract; they do not exercise authenticated account data or the
separate quarterly bills archive. No account credentials or private requests
were used. `TRUSTED_ACCOUNT_SOURCE`, account completeness, and portfolio risk
materialization therefore remain unaccepted.

The new offline parser in `account_bill_archive.py` validates caller-supplied
apply/status JSON and ZIP/CSV bytes, but is not connected to authenticated
acquisition, durable account history, or portfolio materialization. Its latest
13-case synthetic suite passed with zero failures, errors, or skips; Ruff also
passed. JUnit `../validation-results/windows-account-bill-archive-20261005-r2.xml`
has SHA256 `fefa35807596967d225b88091814b5a7144527b4edd65beb93aba1d06b38202d`.
The initial test failure for the 2021 Q1 half-open end boundary remains in
`../validation-results/windows-account-bill-archive-20261005.xml`; the corrected
boundary and subsequent URL/ZIP hardening passed in the latest run. This parser
result is an engineering component test, not `TRUSTED_ACCOUNT_SOURCE` acceptance.

## 2026-10-05 locked Windows tests and native public v2 sample

The current Windows Python 3.12.14 runtime initially lacked the repository's
test and HTTP/WebSocket dependencies. The CPython 3.12 AMD64 Windows lock was
rechecked against `pyproject.toml` and installed into a separate
`validation-venv-windows-py312` environment using required hashes and binary
wheels only. All 41 pinned distributions matched; `pip check` and the strict
dependency-lock verifier passed. Lock SHA256 is
`a4b4bb7f390a62fe0f032aa23ab0c288494d61956fc91bc40ffc2fab034d6f16`. This
environment is not a full-validation baseline.

The current working source passed a 135-case targeted Windows run covering the
offline account-bill archive parser, HighVol installer identity verifier,
full-public V2 packet evaluation, and owned V2 runtime; there were zero failures,
errors, or skips. Ruff passed. The report explicitly records zero external
market requests, authenticated account requests, and order writes during those
tests. JUnit `../validation-results/ctcc-v2-targeted-windows-20261005-r1.xml`
SHA256 is `4fcc883c254152056d3e49d34497e4b5a9533f13f6ff3b42d7c64efa930a9dd0`;
the summary JSON SHA256 is
`6cb6e82981167e2ce7aa6686c8264b6d58a6b8733f58e5c6a7b32aaac20375e8`.

The candle unit and public bridge regression then passed 46 cases with zero
failures or errors and two POSIX-only native-publication skips on Windows. The
test confirms the parser's ordered contract/base/quote mapping; it uses
synthetic transport and does not count as a live sample. JUnit
`../validation-results/ctcc-volume-unit-regression-20261005.xml` SHA256 is
`7d6e4f876190e269da620d2df90831cfefd8538545de6f5da8e875896c53dbf8`; its
summary JSON SHA256 is
`0c9ef329379bc3956c1af25866c0e82b9a28863ce4791307fef85f155073fe7e`.

One bounded actual public-only initial V2 capture completed at
`2026-10-05T10:58:04.084039Z` for `BTC-USDT-SWAP`, with plan environment
`demo`. The sealed inventory contains 198 files and 126 journal events: 13 REST
requests (8 candle pages, 3 quote endpoints, 2 market-aux endpoints) and the
WS connection/subscription/ack/ticker/close sequence. Each of 4H, 1H, 15m, and
5m has 240 confirmed bars over two pages. `vol`, `volCcy`, and `volCcyQuote`
remain contracts, base-currency volume, and quote-currency volume respectively;
for this instrument the quote denomination is USDT. This matches the [current
OKX API guide](https://www.okx.com/docs-v5/en/) and the parser regression.

Native journal replay and packet semantic replay passed. Packet SHA256 is
`77f752b39dc1e969da7198cabe577fde244e9649254d83522d18f6ab1075e256`; journal
SHA256 is `56a9c6b279c023298bba11a7ba03d10b633ffc1686bdf517c4c762bec2e57af9`.
Coverage readback SHA256 is
`e7d92e2e7f31ed6456e6c714966761bd001b290c0bb0f56bb528ece9fa897435`, and the
native outcome SHA256 is
`ec97a3e850611f982997e324b7acac0765f4a62eee87f1ec8b5fd54186987401`. Detailed
files are under `../validation-results/ctcc-public-v2-native-initial-after-clock-r2-20261005/`.
The first attempt was preserved as DENY: the native writer requires a
pre-existing output directory, and the attempt stopped before network I/O. The
R2 runner created a new empty root before its one bounded acquisition; no
automatic request retry occurred.

The actual capture result remains
`initial_public_v2_captured_g1_metadata_account_required`, with admission DENY.
`source_authenticity_verified`, measured-availability eligibility, metadata
completeness, account completeness, and execution authority remain false. No
account request or order write occurred. This sample is not Trusted Public
Source, PIT Gate 3, current executable recheck, G1-G12, Demo acceptance, Shadow,
or Live evidence. The canonical source identity remains HEAD
`caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, tree
`2818ed63896dbbbb7341d15550210c6837d6ac1b`; manifest SHA256 at capture was
`a383288bb246227041f4fc18bb613e7bf228c3eda66eb00d21572a825aa38369`.

## 2026-10-05 offline G1 replay diagnostic

The native public journal and packet were replayed from the retained R2 inventory,
and the full G1 data calculation was independently repeated at the packet's
original capture cutoff (`2026-10-05T10:58:04.084039Z`). G1 returned `passed`,
with `quality_recomputed=true` and `analysis_recomputed=true`; the independent
G1 replay verifier matched. Evaluation SHA256 is
`491790045a3c91f8dd793432b83b37ee3308f476bb8ffcb5caf3f0431aece090`.

This is a bounded historical diagnostic only. The only available fixed policy
is `ctcc-fixed-synthetic-full-public-numeric-v2` (policy SHA256
`874f1ea1183cac3f3f3f9a2ee540bee1b8fe5c68a2ae49ddf2855355bd697a57`), whose
profile records `native_profile_registered=false`. The report therefore keeps
`source_authenticity_verified=false`, `original_source_verified=false`,
`execution_authority=false`, `admission=DENY`, and
`decision_freshness=STALE_FOR_CURRENT_DECISION`. This does not establish
Trusted Public Source, an approved production G1 policy, G1-G12 acceptance, or
current trading eligibility. No account data or order writes were involved.

The sealed diagnostic report is
`../validation-results/ctcc-public-v2-native-initial-after-clock-r2-20261005/g1-diagnostic.json`,
SHA256 `5bc145a6f441ad48529555a9c0e53ec4a114b5c77c95c19870bf10d71c5b881a`.
It binds the actual packet SHA256
`77f752b39dc1e969da7198cabe577fde244e9649254d83522d18f6ab1075e256`,
coverage readback SHA256
`e7d92e2e7f31ed6456e6c714966761bd001b290c0bb0f56bb528ece9fa897435`, and
Windows dependency lock SHA256
`a4b4bb7f390a62fe0f032aa23ab0c288494d61956fc91bc40ffc2fab034d6f16`. The
source manifest file SHA256 at diagnostic time was
`1ac72c49967e618c161ddc6c063ac13054c36bf03b3f4450f03883069328afca`.

## 2026-10-05 submission-boundary fail-closed regression

The targeted Windows private-REST and direct-service boundary suite passed 133
tests with zero failures, errors, or skips in 2.040 seconds. It exercises the
Demo direct-service boundary, Demo qualification transport boundary, Demo/private
REST behavior, and Live private REST. With no trusted qualification/one-shot
issuer installed, entry order writes are rejected; this is a verified fail-closed
boundary, not permission or production integration. No exchange, Demo account,
or order request was made. JUnit
`../validation-results/ctcc-submit-boundary-windows-20261005-r1.xml` SHA256:
`8e1e9ecb13c0a8aa49942cdbeb5336d47ba476219ce73799c98538ed9c8b159b`.

## 2026-10-05 preserved Demo credential-key inventory and runtime state

A redacted key-name snapshot of the separate preserved `C:\CTCC-V2\.env` is
`../validation-results/ctcc-workspace-env-key-names-20261005-r2.json`, SHA256
`05aec7332bf70d3c77eb92c6780184b7a91e38e4051ac8bf446598580bf8e41d`. It
contains the three OKX Demo credential variable names and no Live credential
names; secret values were not saved or printed. R1 is superseded because its
`environment_is_demo` label actually classified only `TRADING_MODE` and it did
not record the Demo-specific execution flags.

R2 records `TRADING_MODE=okx_demo`, `OKX_DEMO_ENABLED=true`,
`OKX_DEMO_ALLOW_ORDER_WRITES=true`, and `OKX_DEMO_AUTO_EXECUTION=true` as
boolean classifications. `AUTO_TRADE=false` and `LIVE_TRADING=false` do not
disable those Demo-specific flags. This is an unsafe legacy configuration if
its API/scheduler is started; the old container must not be restarted or used as
the V2 qualification path.

At the same read-only check, `ctcc-v2-api` was `exited` with Docker restart
policy `unless-stopped`; no matching CTCC/OKX OS process or scheduled task was
found. The only running Docker container was the pre-existing recovered
PostgreSQL validation container, which was left untouched. No account API
request or order write occurred. This snapshot does not prove credential
validity, Read/Trade-only permissions, Withdraw disabled, IP binding, expected
UID, or account-session identity. The canonical source root still has no
`.env`.

## 2026-10-05 low-load manifest check and stopped account-source suite

The 14-case Windows manifest unit suite passed with zero failures or errors.
JUnit `../validation-results/manifest-windows-20261005-r1.xml` is retained as
the test record. Its source-tree check was subsequently rerun after this entry
was added.

A 140-case synthetic account-source/lifecycle selection was started while
keeping the host workload bounded. It had not completed after more than 35
seconds and left two Python pytest processes active, so the run was stopped to
avoid sustained laptop load while fan status remains unknown. Pytest produced
no completed JUnit report; this selection is NOT_ACCEPTED and contributes no
pass count. No network, credential, database, or order operation was part of
the requested tests. The stop is a thermal-safety limitation, not a test
failure diagnosis; individual causes remain unverified.

A separate low-load regression passed five cases for the explicit zero-risk
score boundary in both the core risk engine and structural leverage selector.
It confirms `risk_score=0` does not fall back to a high raw score. JUnit
`../validation-results/risk-score-zero-windows-20261005-r1.xml` SHA256 is
`93ae315451aa8455c5b27d453244f4bd68566a5fdbf959b444e369c439a08956`. This
focused invariant does not constitute dynamic-leverage rollout acceptance.

## 2026-10-05 Demo and Live submission-boundary recheck

The bounded Windows regression over Demo direct-service policy, Demo
qualification transport, Demo private REST, and Live execution REST passed 136
tests with zero failures, errors, or skips in 1.976 seconds. The MockTransport
cases confirm that direct/unqualified order, batch, algo, amend, and unknown
write attempts are rejected before reaching HTTP, and that the write authority
is checked again after signing. JUnit
`../validation-results/submit-boundary-windows-20261005-r2.xml` SHA256 is
`be28630b81c95eddd20910fe4d387544b22311fa4024b94bc7660fc38f68884a`.

The source review also confirms the current product limitation: manual Demo
routes and legacy automation still call the old Demo service, while the
qualification dispatch owner has no production issuer and `require_ready`
fails closed. The REST boundary therefore prevents an unqualified entry but
does not yet provide the required production G1-G12/recheck/reservation/intent
submission path. This is boundary evidence, not Demo acceptance or integrated
dispatch acceptance. No OKX request, credential use, or order write occurred.

## 2026-10-05 persisted Demo arm restart invariant

A new isolated Windows unit case starts with a persisted `armed=true`, invokes
the Demo automation recovery path, and verifies both in-memory status and the
saved state are `armed=false` with no scheduler running. The case passed. JUnit
`../validation-results/demo-restart-disarm-windows-20261005-r1.xml` SHA256 is
`e68caf77f30800b38247061f89a7ce4c456454db12c8b61e96148f28a39e9d23`. This
in-memory repository test verifies the service restart rule only; it does not
prove PostgreSQL process-restart recovery, durable EStop, exposure
reconciliation, or the new qualification runtime integration.

## 2026-10-05 bounded bill archive parser regression

The isolated Windows `tests/unit/test_account_bill_archive.py` selection passed 13 tests with zero failures, errors, or skips. JUnit `../validation-results/account-bill-archive-current-windows-20261005.xml` SHA256 is `D4B301AF5843D1887B78CB491F70FC3709DC2D99CA776DD32F043952E2E57A22`. This covers offline response/ZIP/CSV validation only; it does not authenticate or acquire OKX archive data, prove quarter completeness or account identity, or connect the receipt to the account collector/materializer. A wider three-module parser/history selection exceeded the 30-second low-load limit after 18 progress dots and was stopped; it produced no completed report and contributes no pass count.

## 2026-10-05 fresh PostgreSQL reservation, intent, and migration lifecycle

At source HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, working-tree archive tree `2ccee86825cb3c4108f4d59b8aa6fab88ef54181`, and manifest SHA256 `b15cb36faf3a44dab0b50cc560dd38d42f4be82c467a3e93a7175ba06ea1f409`, a new loopback-only PostgreSQL 17 container was created with a 0.5 CPU and 768 MiB limit. It used no host `.env`, no existing persistent volume, and no exchange connection. A fresh database upgraded through revisions 0001–0021; `alembic check` reported no new operations, and the read-only migration identity verifier returned matching source/database head `0021` with migration source SHA256 `87c475dfebd5df43232a865b86491f56d60d03cd2e9f17aaa767a657ce296013`.

The PostgreSQL reservation and submission-intent integration selection passed 36 tests with zero failures, errors, or skips in 166.005 seconds. It includes competing consumers, durable intent commit/readback after repository restart, rollback on late guard rejection, and no retry after uncertain readback. JUnit `../../validation-results/postgres-reservation-intent-isolated-windows-20261005.xml` SHA256 is `e855ce25afccf2751cac8bd4669bfdce70933cf9567021ab6f1c830449f6cc45`.

A second fresh database passed 34 actual PostgreSQL migration lifecycle tests with zero failures, errors, or skips in 66.715 seconds. It covers empty downgrade/re-upgrade shape, active-writer lock rejection, writer races between guard and drop, and retention of nonempty durable records across rejected downgrade. JUnit `../../validation-results/postgres-downgrade-reupgrade-isolated-windows-20261005.xml` SHA256 is `fe60a143ebc19ed115b822663896e736f3d892fdf2329472ed6536f71d75aee8`. Both temporary containers were stopped and auto-removed; the pre-existing recovered PostgreSQL volume was not used.

In the same source working tree, focused Windows selections also passed: account bill archive parser 13/13; current account source verifier 14/14; outbox 162 passed with 7 POSIX-only skips; and owned one-shot publication/fresh-capture/recheck diagnostics 66 passed with 2 skips. These tests do not establish authenticated account completeness or production dispatch. `require_ready()` still fails closed, and no Demo or Live order was sent. The evidence records are under `../../validation-results/canonical-final-candidate-caa1588-20261005-v4-account-bill-parser-regression/`.

## 2026-10-05 account portfolio runtime check

The focused Windows `tests/unit/test_account_portfolio_runtime.py` selection passed 10 tests with zero failures, errors, or skips in 8.601 seconds. It validates the local materialization/runtime contract over synthetic fixtures, including fail-closed incomplete-source behavior; it is not evidence of an authenticated or complete OKX account snapshot. JUnit `../../validation-results/account-portfolio-runtime-windows-20261005.xml` SHA256 is `ac6782e98c34f5ff0637c022a6671604f588030c96a678e949255cde95457259`. It ran at source HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, working-tree archive tree `4ee616210554151ef49c94a06939777e15ca893f`, manifest SHA256 `86ed30f3b00269a6b6c9f742ef5fefb99f07253b9d90a2c26c6bd514a30513a0`.

## 2026-10-05 aggregate margin and dynamic leverage regression

The bounded Windows selections `tests/unit/test_demo_aggregate_margin.py` and `tests/unit/test_demo_dynamic_leverage_restore_profile.py` passed 36 tests with zero failures, errors, or skips in 1.027 seconds. Coverage includes two 300-USDT position buckets reaching the configured 60% aggregate margin ceiling, denial of an additional over-cap order, a fresh pre-submit account check, last-await cap recheck, disarm/recovery profile caps, and confirmation that high score does not force 20x. JUnit `../../validation-results/margin-leverage-windows-20261005.xml` SHA256 is `d7efa3842a8bb5874eaa9fb5a4cc7e3976e0c83a282403fd49f9693e04ebb3ad`.

This verifies the existing controlled Demo risk path and dynamic profile contracts only. The separate production qualification dispatcher remains unavailable and fail closed; the result is not order-path integration, a real account limit observation, Demo execution acceptance, or Live readiness. The run was at HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`, working-tree archive tree `86f875bbb1df2bc59bca28953f58fb6c54a06b13`, manifest SHA256 `2afe94daef127b86221a816be845f517dbf40f30b341af5ce6184d0b9a42717e`.

## 2026-10-05 Demo/Live boundary regression refresh

The current-source regression over the Demo qualification transport boundary, private REST, Live execution REST, and Demo service passed 161 tests with zero failures, errors, or skips in 1.774 seconds. The first run exposed an outdated Demo service test fixture: it omitted balance `uTime` and required currency detail, so the production parser correctly failed closed. The fixture now contains a timestamp and complete USDT detail; no parser requirement was relaxed. JUnit `../../validation-results/current-submit-boundaries-windows-20261005-r2.xml` SHA256 is `0295448fda2579f3029de65c47a8ac2691f530a12cbfee00caf6089d21d73117`.

This remains mocked transport/service evidence. It does not prove all production dispatch paths are integrated, authenticated account completeness, a Demo execution, or Live readiness. No private account request or order write occurred.

## 2026-10-06 Windows publisher implementation correction

The `FILE_LINK_INFORMATION` implementation described in the 2026-10-05 entry
was superseded. The current Windows publisher validates the final filename and
opens it with `CREATE_NEW` while the publisher lease and ancestor handles stay
held. Name reservation is no-clobber, but file contents are written in place;
consumers must require flush, readback, and the journal receipt before accepting
the file. A late write or envelope failure retains any already-created bytes,
leaves the receipt/commit marker absent, and fails closed without reconstruction
or dispatch.

The focused native storage selection passed 18/18 cases (JUnit
`validation-results/ctcc-v2-windows-sync-storage-final-20261006/junit.xml`,
SHA256 `0afcb6d495c311b24b18648a5d3a36ddd5ebcc50586dd436b40ca0b425eabef5`).
The native outbox success/readback case passed 1/1 (SHA256
`0b5b583d8665607f5129e1b2c9191b88017d268e160f3c17bb0579b88eaf5e9c`), and
the injected late envelope-denial retention case passed 1/1 (SHA256
`b84001418669872a5901d836242a5337b1b17883fecbe5b42740197a63b17428`). Ruff
and `git diff --check` passed on the changed implementation/tests.

The complete Windows storage module ran 266 cases and remains **failed**: 259
passed, 3 skipped, and 4 failed. Three junction fixtures could not be created
(`New-Item` returned Access denied); the fourth failure was creation of a test
hardlink (`os.link` returned `WinError 5`). No ACL or privilege changes were
made. This is not recorded as a passing full Windows storage suite. Async
integration/public-capture attempts also remain **incomplete**: faulthandler
showed the local event loop blocked during Windows socket-pair initialization,
before the test body or collector request began. No market request or account
request occurred. These results do not accept the public source runtime or
complete outbox worker matrix.

The synchronous public-runtime failure-code allowlist regression passed 4/4
cases (JUnit
`validation-results/ctcc-v2-public-clock-error-code-unittest-windows-20261006/junit.xml`,
SHA256 `da04a2984689a3fa509be89885c5a94039e9269745dc9c3be6599a0920cbdf36`).
It verifies that only approved clock failure enums are retained and untrusted
exception details stay redacted; it does not validate collector transport or a
market capture.

## 2026-10-06 generated validation artifact boundary

The source-manifest check initially reported 20 extra files, all under local
`validation-results`, alongside the 9 source files changed by this work. These
reports, temporary pytest roots and retained failure evidence are not release
source. The manifest exclusion list, `.gitignore`, and `.dockerignore` now all
exclude that directory; the evidence remains on disk and is not removed. The
manifest and CI-workflow selection passed 17/17 tests (JUnit
`validation-results/ctcc-v2-manifest-scope-windows-20261006/junit.xml`, SHA256
`39842b1322f7debaf978de2ea16066f4b8cc5b6645c2336ab7c076e908f81e7b`). The
working-tree manifest was regenerated and then checked successfully over 836
canonical source files. This is an evolving local identity, not a full
validation baseline, commit, or release identity.

## 2026-10-06 host clock recheck

The host reported Taipei local time `2026-10-06T00:57:17.6585461+08:00` and UTC
`2026-10-05T16:57:17.6640263Z`; the independent task clock reported
`16:57:18Z`, a roughly 0.34-second difference at its one-second precision.
Eight `perf_counter_ns` samples were strictly increasing and marked monotonic.
The read-only record is
`validation-results/host-clock-windows-20261006-readonly.json`, SHA256
`441f79ad0921518186ea915de42cef524d103efda3cbea8a2b0da1b1754cf1f3`.

This is not a clock-sync acceptance: the current execution environment could
not query the W32Time service or its startup configuration, registry access was
denied, and `w32tm /query /status` returned `0x80070005`. The documented OKX
server-time endpoint is `GET /api/v5/public/time`, returning a Unix-millisecond
`ts` ([official API guide](https://www.okx.com/docs-v5)); the live endpoint
response was inaccessible from the available web reader, and the owned market
collector never began a request. No server-offset sample or public market
capture is claimed. The MyASUS fan-status window was also unavailable through
the Windows UI access gate, so fan operation remains unverified. Clock and
thermal-sensitive capture stay fail closed.

## 2026-10-06 host clock and native public capture correction

The preceding host-clock entry records what the earlier restricted process could
observe; it is superseded for the current process. Windows now reports W32Time
`Running` and `Automatic`, with `Taipei Standard Time`. A successful
`w32tm /query /status` showed source `time.windows.com,0x9`, stratum 5, and a
recent successful synchronization. A later `/query /source` returned access
denied, so this is a point-in-time service observation rather than a claim that
every status query is available or that future clock drift is impossible.

One unauthenticated, no-proxy, no-redirect, no-retry TLS GET of the official OKX
public time endpoint returned HTTP 200. The exchange timestamp was
`1791220572627` ms, between the measured request start
`1791220572424850600` ns and body completion `1791220572717197000` ns;
headers, completion and validation preserved causal ordering. The raw 53-byte
body SHA256 was `54bcc4a63ea6117d1fecb3575ef92742a8406ce59631e1261bf332f194b60e1a`.
The public diagnostic record is
`validation-results/ctcc-v2-okx-time-probe-20261006-r1/probe.json`
(SHA256 `dbe043bfe4c4a53fb1ce1ab6a51143675fab11495a2b477ca0ad5640409caa41`).

The controlled native initial-public v2 collector then captured
`BTC-USDT-SWAP` in 8.1 seconds with 125 journal events. Its packet SHA256 is
`565955243c65bdffe63b9555a3cfb5f24a9e72ede3fb3ec0298ab9271d4dddc6`;
the journal summary SHA256 is
`ab8671ee1a43fefbb627ba07d80ba2b72dac8401343fec57c633a8432a304767`.
A separate Python process reopened the exact native journal and passed
`replay_public_runtime`, including raw inventory and typed semantic replay.
Its plan SHA256 was
`791cf882047616ecfdddfc279b765c6bcb3eaf6c230f6432327865bbfb9b252a`.
The journal and independent replay record are under
`validation-results/ctcc-v2-r5-live-public-v2-20261006-r1/` and its sibling
`.replay.json` (SHA256
`de02ad94f683566aecf0bdfae00c4ff8602c34d170f104b1b4ffb83f6c7863de`);
the status sidecar SHA256 is
`e5716bcda27f01f51e259dd966faf4a2fb0cc5c51dd6bdd11b6fa2e3923bda2d`.
The replay record explicitly retains `execution_authority=false`,
`measured_availability_eligible=false`, `original_source_verified=false`, and
`account_complete=false`. The collector returned admission `DENY`, made no
private account request and sent no order. This establishes one current public
capture and replay, not full R5 authority, G1-G12, R7, Demo or Live acceptance.

## 2026-10-06 guarded Live routes and bounded current-G1 continuation

The read-only Live REST client now rejects any non-GET at the shared transport's
last pre-send check, including an explicit call through the base request method.
Both historical PowerShell Live entry scripts stop immediately after StrictMode,
before reading credentials, arming or making an API request. PowerShell parsed
both scripts successfully. The focused transport/script selection passed 22/22
cases; Ruff and `git diff --check` passed. The scripts were not executed, no
private request was made, and the legacy route remains unavailable for a
qualified one-shot order.

The new V2 post-G12 diagnostic reads a same-invocation native post-publication
public carrier, checks stage, original publication barrier, report and
instrument, then re-evaluates current G1 under the unchanged original data
policy and verifies the result against the same raw packet. It remains `DENY`.
The focused publisher/changed-spread selection passed 4/4 cases with no
failures, errors or skips in 26.215 seconds; JUnit
`validation-results/r7g1-focused-20261006-0118.xml` SHA256 is
`9d5aa32f4d8771ac1705d2f3da7187d99b7250d12828112351a483acb5855918`.
Ruff, format check and `git diff --check` passed. Original candidate source
ownership, current G2-G4, complete current account, atomic reservation and
durable intent are still absent from this coordinator. These tests do not
authorize Demo or Live submission.

## 2026-10-06 Windows storage rerun in a new controlled temporary root

The four previously failing junction/hardlink security cases were rerun under
a new writable test root and passed 4/4 (JUnit
`validation-results/windows-link-recheck-20261006.xml`, SHA256
`42079bbfe74183ca540c4c7056ebba2a1c245dae8a226c3934da2ca784d78fe8`).
The full current `tests/unit/test_trade_evidence_storage.py` module then ran
267 cases: 264 passed, zero failed/errors, and three intentional POSIX-only
symlink cases skipped on Windows. JUnit
`validation-results/windows-storage-full-20261006.xml` SHA256 is
`038ac2b9c649c2f2c15e01d339f47d309de6af3d4d0fa9e96937e95f604c017d`.
The earlier four fixture-creation failures remain historical evidence; this
rerun establishes the current Windows storage module result. It does not
substitute for Linux POSIX coverage or the complete Windows regression suite.

The full current `tests/unit/test_trade_evidence_outbox.py` module also ran
170 cases: 163 passed, zero failed/errors, and seven POSIX-only cases skipped
on Windows. JUnit `validation-results/windows-outbox-full-20261006.xml`
SHA256 is
`74251999954b4492b1ced505aa5dfb5a89f23a5f2aa9690555003440d54f4cda`.
This covers the current `CREATE_NEW` publisher and retained-journal late
failure path under Windows; Linux native storage, worker crash/retry and real
Notion delivery still need separate evidence.

## 2026-10-06 separate current-account and history join contract

The v5 account plan was split into a versioned 13-stream v6 current-only plan
and the unchanged v5 historical plan. The new pure join independently replays
both original B1 journals, then checks exact UID/mainUid, credential session,
registration region, mode, currency, source references, chronology and recorded
local checkpoint hash. It deliberately returns `snapshot=None` and `DENY`;
matching stored hashes are not a fresh locked DB revision. The focused old-v5
compatibility and new-v6/join selection passed 9/9 tests in 6.1 seconds;
the independent rerun JUnit `validation-results/account-v6-join-focused-20261006.xml`
has SHA256 `42f77f63badb137e16c0af5ad092ea4d45c3493fd472e0b8921993e889b6d0c3`.
Ruff and `git diff --check` passed. No private account request or write was
made. Native v6 ownership proof, authenticated current account, complete
historical tail/funding/streak/high-water evidence and current DB revision
remain outstanding. See `docs/account_current_history_join.md`.

The wider current-source regression over v5/v6 account collector, capture,
current-source verifier and join then passed 455/455 cases with zero failures,
errors or skips in 29.211 seconds. JUnit
`validation-results/windows-account-source-regression-20261006.xml` SHA256 is
`a4574c7bec9f27c014d7a891d5322005428c815c3a4cbca3f54fe17de1320f53`.
This is synthetic/local source-contract coverage; it does not make an
authenticated OKX account inventory complete.

## 2026-10-06 MIE Gate 3 source availability audit

The current Gate 3 checker exits successfully but explicitly classifies the
claim as computational, with an exposed descriptive holdout and
`predictive_oos_eligible=0` (qualification SHA256
`baad6d40c42c6453a2c0e98f6b1818d62081a75547f24957d983f873dac60429`).
The earlier 120 downloaded BTC/ETH archives and
172,800 minute rows replay deterministically but were acquired after their
2024/2025 historical cutoffs. The previously examined 2026 holdout was exposed
before a candidate seal. No existing file is promoted to a sealed predictive
OOS source by deterministic replay alone.

The [official OKX historical-data page](https://www.okx.com/en-us/historical-data)
lists downloadable candlestick archives but does not expose a byte-bound
first-publication or per-row availability receipt. The
[OKX candlestick FAQ](https://www.okx.com/en-gb/help/candlestick-faqs-and-settings)
describes a two-day publication delay for daily files, while the
[official V5 candle API](https://app.okx.com/docs-v5/zh/) defines `ts` as candle
start time and `confirm` as completion state. Those fields cannot prove the
exact bytes were available at an earlier intraday decision cutoff. This is an
inference from the official source contracts; a separately authenticated
pre-cutoff byte-bound receipt would be reviewed if found. Until then Gate 3,
sealed predictive OOS, Gate 4, economic OOS and stress claims remain closed.

## 2026-10-06 dependency and HighVol installer identity recheck

The current `caa1588` canonical HEAD is 16 commits after the public feature
branch tip `d984753`. Its Python project and Windows/Linux lock hashes match
the retained checkpoint, but both lock manifests still declare
`accepted_full_validation_baseline=false`; the current Windows lock verifier
passed its scoped source-metadata exclusion with 41 pinned wheels, 42 expected
isolated entries and one additional exact local `ctcc_v2.egg-info` in the
direct environment. The historical 9/15 40-versus-39 discovery difference was
that local source metadata, while the later missing tzdata wheel was a separate
dependency error. Neither has been converted into a full-release baseline.

The reviewed HighVol package dry-run passed exact 7-file identity and parser
checks with zero external calls or deployment. A separate native parser pass
over 33 repository PowerShell scripts and that reviewed installer found zero
syntax errors; PSScriptAnalyzer was unavailable. The reviewed installer uses a
safe `${Mode}` interpolation, while its preserved historical original still
contains the invalid `$Mode:` at line 68. Target comparison against the current
canonical source found three matching pins, three mismatches (`regime.py`,
`service.py`, `structural_protection.py`) and one absent target
(`demo_structure_policy.py`). The package declares canonical qualification
integration `NOT_ACCEPTED` and remains non-deployable. The read-only command
was `scripts/verify_highvol_installer.ps1` with the reviewed package root,
identity path and `-DryRun`; no installer or trade engine was run.
The original `CANDIDATE_MATCH_FOUND` output was not located in preserved
records during this recheck, so it is not assigned an invented exact source
tree. Current Git ancestry and individual source identities are separately
verified; the unresolved historical provenance label remains a release
blocker until its originating evidence is found or a fresh exact-source
validation baseline replaces it.

The reviewed HighVol overlay was also compared by behavior, not only hash.
It would route high volatility from a snapshot label, relax original 4H
permission using 1H alignment, and add a legacy structural geometry exception.
Its synthetic 15m BOS fixtures do not prove the prior compression/new-BOS/later
5m momentum chronology required by current source-bound history rules. Its
controller lacks complete v5 account history and local uncertain,
reservation/intent checks, and its manifest helper updates only five hashes.
The canonical engine already contains eight named strategies and its stricter
high-volatility path remains `NO_TRADE` absent complete history and later
stress/OOS evidence. Therefore the old overlay is not applied or re-pinned;
HighVol/Momentum must be expressed as a versioned raw-history policy through
the canonical qualification and risk boundary before deployment.

## 2026-10-06 V6 native account proof and current G1-G4 diagnostic

The 13-stream v6 current-only account plan now selects a distinct native v3
clock proof and no-clobber readback; v5 continues to use its original v2 proof
contract. The v6 proof replays the original B1 journal/raw pages, source and
time witnesses, and separate durable readback within the original current-data
lease. Its focused synthetic v3/v2 compatibility and late-readback-denial
selection passed 7/7 cases in approximately 12 seconds; Ruff and format check
passed. The separate JUnit rerun passed 7/7 with zero failures/errors/skips;
`validation-results/account-v6-native-proof-20261006.xml` SHA256 is
`aba1564c09e1786cdeb1471900dce211540b0495a0869c95ec53ef5e144b0e8e`.
No private account request or order write occurred. It remains an
initial flat-start diagnostic, not a complete account risk snapshot.

The V2 post-G12 public diagnostic now recomputes current G1-G4 for four base
strategy families using the fresh packet and unchanged original policy and
fixed candidate geometry. History-dependent families are explicitly denied;
no current event-survival proof, complete account, reservation or intent is
claimed. The focused actual-publisher/fresh-market and long/short changed-
market tests passed 6/6 in 42.146 seconds. JUnit
`validation-results/r7g4-final-20261006-0126.xml` SHA256 is
`a0f6f417ffff0e5e3c641fa3725be3487ea8479a5d188e956744799fa0940630`.
Ruff, format and diff checks passed. Only the available trend fixture had a
family-specific end-to-end current-market test; the other three base families
are implemented but not separately demonstrated. The result remains `DENY`
and cannot submit an order.

## 2026-10-06 current-source preservation checkpoint

Before any further integration, a manifest-only source archive was created
under `validation-results/ctcc-current-source-checkpoint-20261006-r1/`.
Its archive SHA256 is
`645aba74ce677f21602409c5a7e07000704fb645449870d5ec637d90647d5b19`;
the accompanying manifest SHA256 is
`f4794524e0726404e9378a8f9a805e272586130d939be7ee709acf9d4b90f231`
over 842 source files. A separate complete Git bundle of the committed branch
HEAD `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9` passed `git bundle
verify`; its SHA256 is
`84ba11f5f8722fd05a70dbf787e9f2a52588d4f6b818d1fc913ac28ab3b35378`.
The archive was reopened and every included byte compared with the source
used to create it. The archive captures an uncommitted working tree and is
explicitly not a validated release, clean Git tree, secret-audit clearance or
execution authority. It excludes local `.env` and validation results. The
current source may evolve after this checkpoint.

## 2026-10-06 original-event and zone continuity diagnostic

The post-G12 v2 diagnostic now replays the original event against its pinned
original G1 source, compares all four fresh confirmed-candle sequences as
append-only, checks the original invalidation anchor, reconstructs the original
zone, and evaluates the fixed entry against the fresh executable quote. It
cannot substitute a newly detected event or change entry, SL, TP or expiry.
Changed or rolled-off history fails closed. The focused positive long/short
publisher cases and rewritten-original-candle cases passed 4/4, with zero
failures/errors/skips. JUnit
`validation-results/r7-event-focused-20261006.xml` SHA256 is
`958afc289fd76f430c36de85efc6f56d7872e6d7c533850b629e15c5d59670e0`;
Ruff, format and diff checks passed. The result remains `DENY` because OHLC
cannot prove the full intrabar path and original source ownership, complete
account, reservation, intent and final dispatch remain absent.

## 2026-10-06 locked local account-revision join diagnostic

The existing DB0017 UID-scoped PostgreSQL advisory transaction lock and scope
row lock now support a separate diagnostic readback: one fresh transaction
rereads the original v5 historical B1 chain, v6 current B1 chain and local
bootstrap checkpoint, then rejects a changed recorded local state hash. Its
receipt binds both source references, the pure join hash and persisted account/
ledger revision numbers. It always returns `DENY` and `snapshot=None`, and
does not claim exchange-wide atomicity, complete history or post-lock freshness.
Three focused synthetic unit tests passed; Ruff, format and diff checks passed.
Two isolated PostgreSQL integration tests were authored but have **not run**:
the Docker daemon is currently unavailable, and the host previously shut down
from overheating. No migration was required or applied in this slice. No
private account request, account write or order write occurred.

## 2026-10-06 account archive acquisition gap recheck

The v5 34-stream source contract covers at most the recent 28-day historical
window; v6's 13 streams are current inventory only. The existing
`account_bill_archive.py` is an offline quarterly ZIP/CSV parser, not an owned
OKX apply/status/download client, and it grants no account authority. The
[official OKX API guide](https://www.okx.com/docs-v5/en/) describes
`POST/GET /api/v5/account/bills-history-archive` for older quarterly bills,
with the current quarter excluded, asynchronous generation and a temporary
download URL; the [API changelog](https://www.okx.com/docs-v5/log_en/) is
retained for endpoint-version review. Demo applicability, exact download host,
empty-quarter proof, adjacent-quarter stitching and late-arrival finality are
not verified here. Bill `ts` reflects balance-update completion, not necessarily
funding accrual; order creation is not fill time. Even a complete bill archive
would not by itself prove lifetime loss streak or continuous equity high-water
mark. No archive API request was made, and `PortfolioRiskSnapshot` remains
incomplete and denied.

## 2026-10-06 fixed-candidate projected economics diagnostic

After the fresh public, current G1-G4 and original-event/zone diagnostics, the
new V2 adapter evaluates the unchanged G12 entry/SL/TP under the pinned
original cost policy, then separately evaluates the fresh executable bid/ask
reference. It uses the V2 upcoming-settlement funding forecast with its own
time semantics and the shared exact numeric cost/comparison arithmetic. The
candidate scenario is checked first; a better current quote cannot repair a
failed original candidate. The original policy's quote-age bound also applies
to ticker, mark, funding data return and capture completion. A source aged
38 seconds is denied when the original bound is 30 seconds, without replacing
unknown with zero. Four focused synthetic cases passed, including this stale
funding rejection. JUnit
`validation-results/r7-economics-focused-20261006.xml` SHA256 is
`f69eee3289346e7e4fe76c6222a594404cee6d3c54a09c49df8aa1d255b9e37a`;
Ruff, format and diff checks passed. Actual account fee, realized funding,
margin cost, fixed protection, reservation and intent remain unknown or
unconnected. The coordinator still returns `DENY` and has no order authority.

## 2026-10-06 source-manifest symlink boundary and focused safety checks

The source manifest now rejects an included symlink or Windows junction instead
of silently omitting it from source identity. No such links were found in the current source
tree. The manifest unit module passed 15 cases on Windows; its actual symlink
creation case was skipped because this Windows session lacks the privilege to
create one. That case still requires Linux validation. Focused aggregate-margin,
dynamic-leverage and blocked-Live-script tests passed 38/38. Focused Demo/Live
transport authority tests passed 75/75 with pytest cache disabled. These
targeted checks do not substitute for full Windows/Linux or Docker acceptance.

## 2026-10-06 final-time R7 and locked-account audit clarity

The V2 post-G12 public diagnostic now replays the original G1 data policy and
fixed-candidate projected economics once more at its final measured return
time. If final G1 expires, the returned diagnostic retains an explicit failed
`final_g1` and clears earlier dependent PASS-looking fields. The targeted
long/short success, G1-expiry and cost-expiry cases passed 6/6. The account
locked-readback receipt now separates recorded pre-lock blockers from blockers
remaining after the locked DB revision readback; its focused synthetic cases
passed 3/3. PostgreSQL integration of that readback remains unrun. Both paths
retain `DENY` and grant no order authority.

## 2026-10-06 quarterly archive and blind-window Gate 3 diagnostics

A quarterly Demo bills-archive journal contract now binds exact account/region,
session and quarter, preserves an ambiguous one-attempt apply claim, and refuses
an unverified download host. It has no HTTP signer, credentials, POST allowance
or durable DB claim/readback. Its parser and contract tests passed 20/20.
Official OKX documentation does not establish Demo availability or a reviewed
download host; this is not complete account history.

A computational-only Gate 3 seam binds one preplanned future-window minute to
an owned public journal, external seal/receipt/checkpoint hashes and causal
clocks. It rejects a later duplicate capture borrowing the first observation's
time. Its scoped tests passed 34/34; JUnit
`validation-results/gate3-blind-window-20261006.xml` SHA256 is
`e44c18347d59b9350f78920488b1f36b17880b94b2016e2e39ce214608077534`.
No real prospective seal, continuous capture, protected full dataset or
evaluator-first-access receipt exists, so predictive Gate 3, sealed OOS and
Gate 4 remain blocked.

The original Notion directive's four final examples were reread directly:
blocked legacy high-score error, newly qualified Demo candidate, price-change
cancellation during evidence rendering, and a readable `summary.png` with
Entry/Timing/SL/TP/liquidity/Gate. All four remain not accepted; newer grouped
sample labels do not replace the original output specification.

## 2026-10-06 host time and source-secret follow-up

Windows Time Service is running with Automatic start. A read-only `w32tm`
status check reported a successful `time.windows.com` sync at 02:47:08 Taipei
time on 2026-10-06. Earlier direct OKX public-time probes also satisfied
request/server/receive ordering. This does not waive per-observation causal
timestamps or source readback: every market/account input remains fail closed
when its own evidence is stale, future-dated or incomplete.

The public test PostgreSQL password was removed from the working source. Base
Compose now requires an explicit password, while the Gate 3 offline verifier
generates an ephemeral in-process password for its isolated stack and restores
the prior environment afterward. Import-only Settings/Alembic defaults use a
passwordless non-routable URL, and `.env.example` has blank credential fields.
The named private deployment `.env` was checked without recording its values;
its DB credential differs from the old public test default. This is a scoped
source-hygiene result, not a complete secret audit or Docker acceptance.

The manifest now rejects private-key filenames and PEM private-key material in
included source, in addition to rejecting source symlinks and junctions. Git
and Docker ignore rules exclude common private-key files. The scoped manifest
tests passed on Windows except the actual symlink-creation case, which this
Windows session cannot create and still needs Linux validation. A bounded
working-source and 16-local-commit scan found no high-confidence OKX, Notion or
GitHub secret, private header, or non-fixture account UID; a final exact-archive
scan is still required before publishing this working source.

## 2026-10-06 native original-public ownership seam

The V2 initial public capture can now be consumed inside the same task through
`capture_native_original_for_g12_v2`, without accepting a caller market, run,
candidate, receipt, barrier or PASS claim. The seam validates initial stage,
report, exact instrument, null publication barrier, native final time, and raw
packet context before returning only diagnostic hashes. Its successful code
explicitly requires a source-derived candidate. It does not call G12 or
post-publication capture and grants no account, reservation, execution or order
authority. Eleven focused synthetic tests passed. The adjacent broader V2
module was stopped before completion for host thermal safety and is not counted
as passed. Full G1-G11 original candidate, owned account inputs and R7 remain
engineering blockers.

## 2026-10-06 first remote exact-source CI attempt

GitHub Actions run `37359893808` checked out interim commit `51c69d4` and
passed archive/image COPY identity, isolated service startup, manifest,
dependency lock and Ruff lint. It failed at the repository-wide Ruff format
step because a fenced Python example in `docs/public_source_runtime.md` was
unformatted. The narrower local app/scripts/tests format check had omitted that
Markdown file. The example was formatted and the exact repository-wide Ruff
lint/format commands now pass locally; a new committed-source remote run is
required. This first run is FAIL, not Docker hermetic acceptance.

## 2026-10-06 pending source revision: DB0022, diagnostics, and Windows CI

The working source has a DB0022 immutable, UID-and-quarter-unique one-attempt
Demo bills-history-archive claim. A separate PostgreSQL session reads it back;
host claim time must precede the database record within two minutes, and the
database record must precede host readback. A forward-skewed host leaves the
durable uncertain tombstone but receives no accepted readback. This is only a
diagnostic claim: there is no authenticated OKX apply, generated file readback,
complete bill history, account snapshot, or trading authority. The DB0022
PostgreSQL tests, downgrade/re-upgrade, and schema drift have been added to the
exact-source Linux verifier but have not yet run on this working revision.
The repository now maps unexpected DB/commit/readback exceptions to fixed
non-secret error codes outside the original exception handler. An ambiguous
commit remains a one-attempt unknown, never an automatic retry. Scoped
failure-path unit tests passed; actual PostgreSQL failure behavior still needs
the exact-source remote run.
An invalid host clock before the claim is separately sanitized and creates no
database attempt.

The same-task current-account/history join rereads a recorded history source
and a new native current capture under the exact UID lock and original lease.
It burns the current carrier and returns `DENY`, `snapshot=null`, and
`account_complete=false`. A separate prospective Gate 3 timeline distinguishes
automated in-window acquisition from caller-declared evaluator read; it still
cannot prove a sealed historical or prospective predictive dataset. The
default-off HighVol/Momentum observation pins the original timing policy and
event/candidate deadlines, rejects drift, records the exact analysis version,
and grants no qualification or order permission. It is not an installer route.

The same-task initial native public V2 carrier now returns a bounded canonical
receipt after raw/context readback that records the exact missing dependency:
there is no registered non-synthetic G1 data policy and analysis version. The
receipt sets `g1_evaluated=false`, `g1_passed=false`, and `admission=DENY`; it
does not call G12, account acquisition, reservation, or exchange submit.
Thirteen focused synthetic tests passed. This is not G1 PASS or full R7.

A second GitHub job has been added to test the exact committed source on a
Windows runner with the locked Windows dependencies, all unit tests, seven
reviewed no-DB integration modules, strict native-case and platform-skip
accounting, and archive readback. Its complete remote run has not happened.
An independent review identified that collected cases must be compared with
executed JUnit cases. The same pytest invocation now records its complete
collection and the verifier requires an exact one-to-one JUnit match; this
correction has passed focused synthetic and native Windows tests but not the
full remote job. The existing Linux Docker job remains separately required.

On this checkpoint source, repository-wide Ruff lint and format passed.
A low-load hermetic targeted selection of 165 cases yielded 163 passes and two
explicit Windows/POSIX limitations. This is a scoped local result, not Windows
full regression, PostgreSQL, Docker, Demo, Live, or release acceptance.

The earlier exact-source GitHub run `37360626840` used commit `abd3a96` and
was cancelled after its single opaque workflow step exceeded 71 minutes. Its
retained artifact and JUnit show that the isolated image, COPY identity,
dependency lock, migration/re-upgrade and schema checks completed, followed
by 193/193 PostgreSQL integration passes in 2946.27 seconds. The subsequent
full Linux suite had started but did not complete. The cancelled run is **not**
hermetic acceptance, and none of its counts apply to DB0022 or Windows CI.

The Linux job now allows 180 minutes, with a 165-minute monotonic verification
budget, explicit 75-minute PostgreSQL and 100-minute full-suite stage caps,
per-minute stage heartbeat, and a five-minute bounded cleanup. The same test
lists and JUnit readbacks remain required; a timeout stays FAIL. These limits
are based on the retained run's actual duration, not a relaxed acceptance gate.
The updated source still requires its own complete remote result and final
source-bound regression after later engineering changes.

## 2026-10-06 fail-closed Demo origin and blind-window checkpoint

The earlier native V2 public diagnostic used Production public WebSocket
`ws.okx.com` under a `demo` label. Its original bytes and independent replay
remain preserved, but this is not an exact Demo source. The current native V2
capture now refuses before journal creation, clock sampling and network I/O.
Separately reviewed global, US/AU and EEA Demo route pairs require the Demo
WebSocket/TLS host and `x-simulated-trading: 1` on public REST. The route policy
alone cannot authenticate a caller-supplied registration region or bind the
account credential session, so even a correctly shaped route cannot enable
capture. Historical V2 replay remains `DENY`.

The offline Gate 3 complete-window binder replays each original public-capture
byte under sealed minute/symbol coordinates, externally supplied journal and
capture pins, first-observation indexes and strict acquisition chronology.
Missing, late, duplicate, conflicting or reordered rows are denied. Its
canonical row/dataset hashes do not prove independent external pin custody,
evaluator first access, or a real blind window. Predictive OOS and execution
eligibility remain fixed false. The native G1 seam likewise rejects externally
injected policy/version/source claims; no non-synthetic operational G1 policy is
registered. R7 still has no source-derived original candidate, complete fresh
account snapshot or qualified same-invocation continuation.

Focused R5 Demo-origin selection passed 34 cases, Gate 3 binder and adjacent
modules passed 36, and native G1 seam passed 18. These are separate scoped
selections, not the final regression. Repository-wide Ruff lint and format,
`git diff --check`, and the 870-file source manifest passed on the new local
commit `c09e7d70ecbea1d85dfc9f5aa117885f76fd5a80` (tree
`2b058cb627594cf5adfec59178d5830f69c4ce28`). Its exact archive and
complete-history bundle were read back and verified at
`validation-results/ctcc-source-c09e7d7-20261006/`; archive SHA256 is
`dafe5224d6dd58a4e00666123d55e03440a610a05a64c601b4624cb5a8c08bc8`,
bundle SHA256 is
`b3e16dde595228ac1d1f31f5850d10a4b458ec8c64c70736d7d59a3d5933c0e4`,
and manifest SHA256 is
`6a4d8c9b22e032b62df6d17c0ec248f26b49f9e8b2b87b4acad7162926f54de0`.
This is a rollback/checkpoint identity, not a release or Docker image.

Remote run `37369288145` is bound to its earlier commit `ea81bcd`, not the
new checkpoint. The Linux job was cancelled before it acquired a hosted runner
or ran a step; its check annotation says runner acquisition failed after
multiple attempts. The [GitHub Status page](https://www.githubstatus.com/)
reported an Actions hosted-runner assignment incident during this period.
The Windows job was still running when this entry was written. Neither job
establishes CI acceptance for the new source; a complete exact-source rerun is
required. No Demo or Live order was submitted.

## 2026-10-06 local Demo alignment and safety audits

An offline preflight now compares an unused controlled Demo account session to
a native Demo private REST client: exact credential values, reviewed REST
region and simulated-trading header must agree. It rejects wrong credentials,
cross-region settings, unsupported regions and injected transports, but issues
only a `DENY` diagnostic. The region evidence in the account plan is still a
caller claim, and the two clients have no shared nontransferable authenticated
session. The preflight cannot establish account/source authenticity or authorize
a public capture or order. Its route and adjacent account/private-client
selections passed 78 tests across two separate batches.

The repaired HighVol installer copy passed native PowerShell parsing, repeated
seven-file identity/dry-run checks and 11 focused tests. Its README and package
identity were updated outside the canonical source; identity SHA256 is
`34ef5b12fa3855977d784d31803e0e1c7cb871959609f68a29cca2efcbffd722`.
PSScriptAnalyzer is unavailable locally. Install, reinstall, upgrade, rollback
and integration with the canonical trading chain remain unaccepted.

Read-only forensics review found matched fill/fee and original page hashes, but
no running complete lineage through real position, protection, funding accrual
and close. The source-aware replay keeps these quantities unknown; no Demo
realized result is claimed. Three scoped forensics modules passed 363 tests.
The Windows outbox audit found the native `CREATE_NEW` publisher preserves its
durable journal on late failure and refuses an incomplete envelope. Its unit
selections passed 698 tests with 11 explicit platform-only skips. Actual
PostgreSQL projection, Linux native storage, Notion REST property-ID binding,
delivery and readback remain unverified on the eventual final source. These
scoped passes do not make OUTBOX, FORENSICS or NOTION_SYNC production PASS.

## 2026-10-06 event tombstone and blind-window boundary correction

The qualification ledger now checks the original event across every settlement
currency and reservation state under its account-UID transaction lock for both
legacy and control-bound reservation calls. A reconciled-flat tombstone cannot
be repackaged with another report ID and currency to reserve the event again.
Focused offline tests passed; PostgreSQL terminal-reuse and mixed-route race
tests were added but still require an isolated database execution. This is a
specific fail-closed correction, not R6 production acceptance. A database
unique constraint across the UID and event would additionally protect future
writers that might omit the shared lock; it is not yet implemented or verified.

The Gate 3 blind-window binder compares capture-plan creation to the seal and
first event in exact integer nanoseconds, rejecting equality at either bound.
Retained source readback must also finish strictly before the first permitted
evaluator access. Focused synthetic replay tests passed. External pin custody,
actual evaluator first-read time and a complete real holdout remain unproven;
predictive, promotion and execution authority remain false.

The preserved September integrated release contract explains its original
dependency check: isolated discovery found the 39 expected distributions and
normal discovery found one verified local `ctcc-v2==1.6.9` source metadata
extra. That narrow source-extra allowance did not accept third-party drift.
The later missing locked `tzdata==2026.4` wheel was repaired separately, but
the current source still has no accepted full-validation dependency baseline
or final release identity. `DEPENDENCY_IDENTITY` remains FAIL.

## 2026-10-06 source parser and incomplete-account containment

The separate legacy OKX public client now rejects SWAP candle rows with absent,
malformed or impossible price/volume fields, and rejects a ticker unless the
response has exactly one row for the requested instrument. Official OKX units
were rechecked: SWAP candle `vol` is contracts, `volCcy` is base currency and
`volCcyQuote` is quote currency; ticker `vol24h` is contracts and
`volCcy24h` is base currency, with no ticker quote-turnover field. The existing
V2 bridge already preserves these distinctions. The legacy client still has
retries, candle sorting and unrelated optional-field zero defaults, and is
not an R5 trusted source. Its four affected unit modules passed 170 tests with
two recorded Windows/POSIX-only skips; this is not full regression.

The Demo account materializer now withholds its formal
`PortfolioRiskSnapshot` whenever the verified packet or any row mapping reports
incomplete coverage. It retains original packet/input hashes, mapped values,
row projections and specific reasons for diagnostic and reconciliation work;
missing balance remains unknown. The current verified packet contract always
has source/history/cross-read gaps, so no current account read can issue a
complete risk snapshot or execution authority. Focused synthetic account
selections passed 330 tests. Authenticated complete account ingestion and the
PostgreSQL/CI full regression remain outstanding.

## 2026-10-06 legacy auxiliary public-read containment

The separate legacy OKX public client now requires a single, shape-valid mark,
funding, open-interest or book response. It rejects a missing or conflicting
instrument identity where OKX echoes one, malformed numeric/time fields,
nonpositive mark/depth, invalid book levels and absent funding timestamps.
Signed or zero funding and genuine zero open interest remain valid. OKX REST
books do not echo an instrument ID, so the request is the only identity pin on
that legacy path; it is checked for SWAP before network I/O. The official
four-element REST book level is respected, including a positive order count.
The old funding tuple now pairs `fundingRate` with `fundingTime`, while
`nextFundingTime` is checked as a later settlement forecast. A malformed
HTTP-200 JSON body or non-string API code fails without retry.

The initial 112 focused MockTransport tests passed before review. After the
request-identity, funding and malformed-body fixes, root reran all four affected
unit modules: 539 collected and passed. Repository-wide Ruff lint and format
checks passed. These are synthetic checks of the legacy diagnostic client, not
native OKX source capture. The client still lacks retained raw bytes, exact
environment/region ownership, causal provenance and freshness, and therefore
cannot be used for R5 or execution authority. Root-run exact-source checks and
CI remain required before accepting this code checkpoint.

A read-only submit-route audit traced the shipped Demo and Live manual,
automation, scheduler and direct-service paths to the concrete private REST
client. Its order, batch, algo and amend POSTs pass the same immediately-before-
HTTP authority check and currently hard-DENY; no alternate shipped raw order
POST was found. Exact maintenance writes have a separate policy, and the Live
read-only client rejects non-GET requests. This establishes current no-order
containment only. Registered-route and restart tests with concrete transports
still need to prove that no route can evade the boundary, and a real G12/R7/R6/
intent authority has not been built. `ALL_SUBMIT_ROUTES_GUARDED` remains FAIL
for production acceptance.

## 2026-10-06 source-bound CI failure repairs awaiting full rerun

Remote run `37369288145` at earlier commit `ea81bcd` completed with failure.
Windows collected 12,943 cases but the 90-minute job limit interrupted it after
11,179 results: 11,161 passed, 15 skipped and 3 failed; 1,764 cases never ran.
The two clock cases exposed an exact producer/replayer enum mismatch: the
runtime retained `native_clock_sample_unbounded`, while its journal readback
rejected that same fixed code. The shared allowlist now defines both sides,
keeps arbitrary failure text out, and still cannot grant source or trading
authority. The third case found new Gate 3 blind-window consumers missing from
the explicit public-source import review. The now-reviewed capture and dataset
replay imports are bounded in that review; no private or execution import was
added. The local clock rejection and import-boundary selections passed after
repair.

The Windows CI timeout is raised to 180 minutes with the exact same reviewed
test selection and JUnit readback; incomplete, failed or timed-out runs remain
FAIL. This is based on the preserved old run's 12,943-case collection and
1,764 unfinished cases, not a reduced acceptance requirement. New run
`37378953509` started at source `d298c38` before these pending repairs and
is not a final-source CI result. Its Linux and Windows jobs acquired runners;
neither had completed when this record was written.

Registered Demo and Live API order routes and Demo automation run-once now have
additional synthetic concrete-transport tests. Together with existing
transport tests, 71 cases passed and recorded zero unqualified OKX order HTTP.
This proves the current hard-DENY containment at those routes. Live scheduler
and restart-path integration still require further tests, and no true
G12/R7/R6/intent admission exists.

Pending DB0023 adds a database-level exact-UID/original-event unique constraint
across currencies and all reservation states. It checks duplicates under
non-waiting table locks, preserves the older scope constraint, adds TRUNCATE
guards on the three ledger tables, and refuses a downgrade while they contain
data. Existing collisions remain intact and block migration. Offline migration
and verifier selections passed, and Alembic reports `0023` as source head.
The new PostgreSQL integration cases were collected but not run against an
isolated database; schema drift, downgrade and concurrency remain FAIL for
this pending source.

## 2026-10-06 current committed source and outstanding acceptance

The first pushed feature source in this sequence was
`656371b23f833e14c3881396565d685f552d790b`.
It includes DB0023, the clock-code replay and Gate 3 import-boundary repairs,
and expanded no-order transport checks. GitHub Actions run
[`37380901159`](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37380901159)
started validating that exact pushed commit. Its Docker and Windows jobs were
later cancelled by the newer source push; it is not a PASS and cannot validate
any later source revision.

Subsequent commits `ce01603` and `61f8c63` retain additional account-source
rejections: partial or malformed OKX gateway `inTime`/`outTime` provenance and
current-position rows in increasing creation-time order are incomplete, never
silently reordered. The account-focused Windows selections passed 698 and 700
tests respectively, with Ruff checks, but they are not full-source acceptance.
At that point the changed source still needed a new manifest, archive, remote
CI run, and PostgreSQL integration; the later checkpoint below supplies the
first three identity steps, not a passing regression.

The next committed source update adds a narrow, read-only original public
and account handoff and a controlled Demo route declaration check. Both remain
non-authoritative. The native V2 public issuer still refuses its Demo-labelled
Production socket before network I/O, so this is no evidence of a trusted Demo
market snapshot, G1–G12 qualification, post-G12 execution recheck, reservation,
intent, or order eligibility. The account materializer also rejects a recorded
history seed that covers less than the exact fixed packet query window. Its
synthetic tests do not establish exact-source full acceptance.

After two spontaneous thermal shutdowns, the host is kept at low load. Windows
Time Service now runs automatically and reported a successful `time.windows.com`
sync at 06:11:56 Taipei on 2026-10-06. This does not replace per-request
exchange-time causality checks. Docker/WSL local acceptance remains deferred
while fan health is uncertain; remote CI is being used for source-bound checks.
No authenticated Demo order or real-money Micro Live order was submitted. No
complete account snapshot, OOS/shadow/economic acceptance, current-source full
regression, release, or final completion is claimed.

The next offline source-bound review tightened the reviewed Demo REST policy:
it now requires the fixed instrument and the exact endpoint query, including
SWAP type, five-level books size, and bounded candle bar/limit/cursor. Missing,
duplicate, extra, or cross-instrument parameters are rejected. This policy is
still not connected to the native V2 REST/WS issuer or journal. The account
gap inventory separately replays the pinned packet and materialization inputs,
lists unresolved capture/mapping proof classes without raw account identifiers,
and keeps account completeness and execution authority false. Root reran 67
public-origin/coordinator tests with one Windows symlink-privilege skip and
149 account-materializer/inventory tests with no skips; Ruff checks passed.
These scoped synthetic results do not promote Demo capture or account authority.

The local `ea3a562dc69e0faa48ef61a56bf86b4e43b2304b` checkpoint has
tree `8e9be3a759374fc6988cdce6fb71f82d9022ddb2` and a byte-exact Git
archive at `../validation-results/ctcc-source-ea3a562-20261006/`.
The archive's embedded commit and reconstructed tree matched those values, and
the Git bundle verified. Its Windows dependency verifier retained
`accepted_full_validation_baseline=false`. The scoped all-local-Git-object
secret-pattern scan found zero high-confidence matches across 2,097 blobs; it
does not cover ignored files, unknown token formats, or remote artifacts.

The source checkpoint at this stage was
`4c4c941c54067eca9f22b1a18aa4e85344b38072`, tree
`d522097edf9ddcfe8a1b022797ba3b3536dfcac3`, with a clean working tree
and 882-file manifest SHA256
`e18d3b154baab90ea52db0e73ea6d498cd9026fb8b65e93d6bca37709d5113cd`.
Its exact archive, verified Git bundle, dependency identities, and scoped
secret-scan report are retained under
`../validation-results/ctcc-source-4c4c941-20261006/`; the source archive SHA256
is `e8fc0c820b393c94924463b4ec0723da50dfa317797799347eee9db546a93de7`.
The archive reconstructed exactly the recorded commit/tree. The Windows
dependency lock verifier passed its pinned inventory but still reports
`accepted_full_validation_baseline=false`; no release baseline is declared.
The local Git-object pattern scan covered 2,106 blobs with zero high-confidence
matches under its stated limits. Remote GitHub Actions run
[`37384534084`](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37384534084)
started at this exact commit and was later cancelled by a newer source push. It
cannot be counted as
Windows, Linux, PostgreSQL or Docker acceptance until completed and inspected.

The preserved 9/15 integrated release contract at
`C:\Users\holy1\AppData\Local\CTCC\Demo-Integrated\907285d56e2045759c9219b300b52b6a\release-contract.json`
has SHA256 `f8d31e56f2231826d0e891de941f76efeb27de1ec38771383e534ee407f7fa8a`.
Its pinned candidate ZIP SHA256 is
`1a8d0b511a8e8ef7106b5cc6734ac0e017e0ce46072fc4426400b77781715eca`,
and the historical validation ZIP SHA256 is
`828abf8ae5da8c25949b13580da2f7e11d8e795a383bbef3ce1b095506240795`.
A read-only audit rehashed all 582 cached package-source files and 278
candidate-map files against that contract with zero mismatches. The contract
proves a package source-file identity, not a Git commit/tree: its file map has
no modes, and none of six tested Git refs matched all 582 package files, even
after LF normalization (the closest inspected deployment ref matched 556).
The originating literal `PROVENANCE_RESULT=CANDIDATE_MATCH_FOUND` output is
still absent, so that label cannot be assigned to an invented Git tree. The
9/15 dependency count discrepancy was one extra `ctcc_v2.egg-info` source
metadata record in normal discovery, with no pinned package-version change.
The separate later Windows lock-environment gap was the already-pinned
`tzdata` wheel; installation repaired that environment only. Neither point
accepts the current full-validation dependency baseline.

Cancelled exact-source CI run `37380901159` at `656371b` preserved a partial
Linux hermetic artifact. Its build/COPY/manifest/lock/Ruff and migration
upgrade, identity, drift, downgrade and re-upgrade stages exited successfully.
The 210-case PostgreSQL integration selection then printed three failures
before cancellation. Exact collection order places them at
`test_other_currency_exposure_denies_entry_but_not_read_reconciliation` with
`reserved`, `consumed`, and `uncertain` parameters. This is a high-confidence
index inference, not a captured traceback; the replacement run at `4c4c941`
must supply the actual error. The cancelled Windows artifact recorded 2,304
cases with zero failures and one skip, far short of its 13,201 planned cases.
Neither partial job is a PASS.

Source review found the three inferred PostgreSQL failures shared one legacy
test setup: the attempted new reserve reused the original event, so the
correct UID/event tombstone guard rejects it before the cross-currency exposure
guard. The cross-currency test now submits its existing second, independent
event on the same exact UID; it retains checks that consuming the original is
blocked, read reconciliation is allowed, and the other-currency hold remains.
No database constraint, guard order, or execution behavior changed. The three
cases collect and 28 related offline unit cases pass, but the PostgreSQL
assertions still need an actual isolated database rerun at the changed source.

The subsequent `dde5823296754035dbf13f443082d08160981497` source has tree
`a4422d1c30d180ae66401838388c1fc2ad10c70a`. Its exact archive rebuilt
that tree, and its Git bundle verified. The preserved source checkpoint is
`../validation-results/ctcc-source-dde5823-20261006/`; archive SHA256 is
`8ab29a3d267a59d3a6cf0caa2a5f24cf6bf637e9b5e37dea12336ac8d9a839bb`,
bundle SHA256 is `8f0b1c5240f295915f8b1d319c8045d1da5338bd8cfd6633efc43355d880255e`,
and `identity.json` records manifest, dependency locks and migration head 0023.
The identity file explicitly marks full validation incomplete. Matching remote
CI run [`37385413695`](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37385413695)
is in progress at this writing; no pass is inferred from its running status.

A read-only native W32Time v2 observation on this host passed the exact pinned
Traditional Chinese profile with the service Running/Automatic. The 109 focused
clock tests passed. This is a fresh local clock prerequisite only, not a blanket
market-source or Gate 3 pass. One bounded real OKX public capture then used
native TLS for `/public/time`, a one-row 1m BTC-USDT-SWAP history-candle GET,
and `/public/time`; the closed source bar and receipt were retained under
`../validation-results/prospective-public-1m-diagnostic-20261006/`. Its
separate-process replay checked all 37 manifest files and one row. Receipt
SHA256 is `507c99d02dfbb04fa2645bb99d6d82aa0e329ba4b1962e57b12910d16a4c1162`.
The checkpoint is a sibling file, not independently protected service state;
the report therefore sets PIT acceptance, predictive-OOS eligibility, and
execution authority to false. No account credentials or order writes were used.

The subsequent source slice adds an opt-in PostgreSQL 0024 append-only public
checkpoint witness, a restricted-function repository, and phase hooks that
anchor an attempt before publishing its capture. Its result still fixes
`independently_protected`, `predictive_oos_eligible`, and `execution_authority`
to false. Focused native unit checks passed 78 cases with one Windows-only
POSIX symlink skip; the new restricted-role PostgreSQL integration test
collected but has not run against a real database on this overheated host.
The final hermetic PostgreSQL selection now requires that module. Real role,
WAL/OS isolation, crash/restart and exact-source migration acceptance remain
open. A separate R7 record-consistency fix rejects rehashed timing results
that change the original setup, trigger or policy; 36 targeted tests passed,
without granting execution authority. Two targeted native Windows late-write
tests and 57 direct submit-boundary tests passed locally. A broader ad hoc
Windows selection was stopped early to limit host load and is not counted as a
regression pass.

The matching remote run at `dde5823` later completed its isolated PostgreSQL
selection: 210/210 passed, with migration identity, upgrade, downgrade,
re-upgrade and schema drift checks exiting successfully. This is scoped to that
older source only. Its Linux full-test stage then failed during collection
because an integration test and a unit test shared the basename
`test_account_bill_archive_claim_repository.py`; no Linux full regression pass
is claimed. The unit module was given a unique filename in the subsequent
working source, and local full unit/integration collection exits successfully.
The failing remote artifact preserved the exact `dde5823` source identity and
isolated image digest
`sha256:57534f85239eb4f6bb55813d9d6f70c251e1848df13158d9c3602b9beb53694a`.
That image is not evidence for the later local source or migration 0024.

The later working-source safety review found that old Demo/Live execution could
call OKX set-leverage before the common order-create denial. The shared
transport now denies set-leverage until trusted flat-account authority exists;
cancel, close, cancel-all-after and precheck remain available under their
existing guards. The direct-route and automation-focused selection passed 77
synthetic cases; no exchange write was performed. The native Demo account
parser now applies official Futures-mode field semantics to top-level balance
`availEq` and account-position-risk `adjEq`, while requiring the exact
settlement-currency `details[].availEq`; missing current source fields prevent
even a diagnostic observed-flat claim. Account-focused selections passed 468
cases, with account/execution authority still false. These selections do not
prove authenticated account completeness.

The new PostgreSQL 0024 witness role guard additionally rejects column grants,
table `REFERENCES`/`MAINTAIN` rights and other role membership. Its 9 focused
unit cases passed and 3 integration cases collected, but those integration
assertions have not yet run on PostgreSQL. A migration-identity test's stale
0023 head expectation was updated to 0024; its 47-case module passed. These
counts overlap broader prior selections and are not a full Windows/Linux
regression. No MIE Gate 3/OOS promotion, qualified Demo execution or Live
readiness is inferred.

The next offline MIE source slice adds an event-time grouped walk-forward
splitter. It requires every declared instrument exactly once at each UTC event
time and maps purge, validation and embargo to complete symbol groups;
persisted row membership is recomputed from the same plan. The new local
SQLite Gate 3 seal ledger publishes a preregistration and later receipt with
unique no-clobber rows, reads them through separate connections, rejects a
second seal for the same source/instruments/calendar window even if a caller
changes the source version or identifiers, and permits only one durable formal
evaluation reservation per seal across workers and restart. These are
computational controls: local time and a locally accessible database do not
prove independent custody, first holdout access, genuine row availability or
predictive OOS. Every returned claim remains computational, with predictive
and execution authority false. The two focused test modules passed 29 cases;
the broader Windows MIE unit directory also passed, while current-source
Windows/Linux full regression and PostgreSQL 0024 execution remain open.

The exact `7ed9f5b` validation branch started matching remote run
`37394395421`. Its Docker job verified source/tree, archive and dependency
identity, then failed before migrations and PostgreSQL at whole-project Ruff:
`app/database/models/__init__.py` placed `PublicReceiptWitnessRevision` before
`ProtectiveOrder` in `__all__`. The generated `build/lib` mirror showed the
same source error. The subsequent source places the names in canonical order;
local whole-project Ruff check and format check both pass. This does not turn
the failed `7ed9f5b` job into a PASS; the corrected exact source still needs
its own remote run and all remaining stages.

## 2026-10-06 exact-source checkpoint and Windows failure diagnosis

The corrected source `98af1ee24bf015ab6149f7ed7e6e7597573d9ef8` has tree
`d40f1766896d6713f823e0819708781dfb2df534`. Its verified source archive,
complete Git bundle, identity record, and scoped Git-object pattern scan are
retained under `../validation-results/ctcc-source-98af1ee-20261006/`. The
archive SHA256 is `9d98564c35f0194f887125e45820b17ef298776fe3d06341b5e68da3c4c4baed`;
the bundle SHA256 is `604f0bb043e801fb391bbe0e8655892d4879fe2347db7d47f72cb2c16f9b393f`.
The archive reconstructed all 892 Git blobs and the recorded tree. The pattern
scan found no high-confidence key prefixes in 2,167 local Git blobs; it does
not cover unprefixed secrets or all report, log, ZIP and remote-only contents.
Full validation and execution authority remain false. Matching remote
validation run [`37394781841`](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37394781841)
was still running when this section was written; no pass is inferred.

The older `dde5823` remote Windows run
[`37385413695`](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37385413695)
completed 13,249 tests with 13,217 passed, 29 skipped, three failed and zero
errors. Its artifact source/tree and manifest match that older commit, so the
result is a source-bound FAIL, not current-source acceptance. Two failed tests
expected a formal portfolio snapshot from a synthetic account whose packet and
mapping report unresolved source/history/local-hold gaps. Current materializer
policy correctly keeps that snapshot absent. The local follow-up assertions now
preserve their balance-shape and fail-closed intent; the 100-case consistency
module passes on the working source. The third failure compared a resolved
Windows temporary source path with its unresolved 8.3-path spelling in a test;
the local follow-up resolves both operands. The dependency verifier and its
extra-metadata rejection are unchanged; the 17 executable cases in that test
module pass locally, with two existing skips. These local targeted results do
not convert the old remote failure or the still-running exact-source run into
a Windows full-regression pass.

The next working-source slice adds a private, one-use native Demo account
session claim before its first host sample or await. Both initial-current and
current/history entrypoints reject a concurrent second owner; cancellation or
first-sample failure burns the session. The ordinary bootstrap path retains its
existing early-burn behavior, while only the matching private native observer
may adopt the original claim once. The final scoped account selection passed
79 cases, including 14 new ownership and failure-path cases, with no real
account, database or exchange call. Its source/test receipt is
`../validation-results/account-native-session-claim-source-and-tests-20261006.json`
(SHA256 `689f0d5a0c3c0e1976015d75e33197c8ba66e3f163924c555763be0cafaae2d4`).
This is not account-source authentication, region proof or execution permission.

A separate offline MIE V2 label now distinguishes measured base receipt,
decision, outcome availability and read time; its pinned plan fixes feature
parameters, bar/outcome horizons and label threshold. Replayed model-content
hashes make later bar changes visible without treating caller-provided row
hashes as authenticated exchange facts. Its 57 focused replay-adjacent tests
and the broader Windows MIE unit directory passed locally. A versioned Demo
public-route policy likewise records reviewed regional routes but remains a
hard DENY and grants no public-source or order authority; its focused source
tests passed locally. These additions are not MIE Gate 3, OOS, Demo or Live
acceptance, and they still require exact-source remote regression.

At 2026-10-06 01:11 UTC, a bounded unauthenticated production-public full
collector diagnostic returned `public_component_capture_failed`, with the
quote leaf reporting `public_quote_capture_invalid`; no packet or raw source
archive was published. In a separate quote-only diagnostic at 01:13 UTC,
ticker, mark and funding leaf records validated, but funding's reported source
time was about 51 seconds before receipt while that diagnostic used a common
five-second age limit. The final quote record still rejected. This local
observation is not a proof that the earlier September `future_component_timestamp`
has been repaired for every source. Do not widen the ticker/mark age or rewrite
funding timestamps to make the batch pass; component-specific semantics and
source-owned raw capture still need validation. W32Time was observed
Running/Automatic with an NTP sync from `time.windows.com` at 2026-10-06
09:02:36 Asia/Taipei, but host sync alone is not R5 acceptance.

## 2026-10-06 native diagnostic and maintenance-boundary continuation

The working source after `d7df266` adds a fixed, versioned **inspection-only**
native G1 policy. In the same-task original-source coordinator, V3 consumes the
one-use native V2 public packet, recomputes and independently replays G1 from
its raw bytes, and records the policy, source and result hashes. A rejected G1
stops before the private account read. The old V2 receipt is unchanged. The
numerical G1 inspection bounds have no measured trading calibration, and the
native Demo public issuer still hard-denies capture before I/O. Every V3
diagnostic remains `DENY`, with no candidate, G12, reservation or execution
authority. Its focused V3/V2/seam selection passed 41 cases with one Windows
POSIX-symlink skip.

The native current V6 Demo account path now records the TLS hostname observed
by the owned collector in new raw-finalization journal events. Only after the
signed account capture, separate proof readback and original DB-chain check
may it mint a private, one-use, same-task account-origin observation. The
observation binds the two `account/config` UID/mainUid rows, pinned origin,
simulated header, TLS peer/hostname, packet/plan/proof/readback digests and
credential session with UTC and monotonic expiry. Its session and task links
are weak references. Old journals still replay under their old contract but
cannot mint the new observation. This is not independent registration-region
proof, a trusted Demo public route or complete account materialization; all
source, account and execution authority flags remain false. Forty focused
synthetic origin/native/journal checks passed before the final weak-task
change; the eight-case origin module, including GC denial, passed afterward.

The blind-window Gate 3 minute binder now requires the capture plan to be
created strictly after the seal and strictly before the first event, using
exact UTC-to-nanosecond comparison consistent with the complete-window binder.
Its three adjacent modules passed 36 synthetic cases. Caller-provided hashes
and local seal storage still cannot prove independent first access, so
predictive OOS eligibility remains false.

An independent MockTransport audit found that direct Demo/Live clients could
send `cancel-all-after` with `timeOut=0`, which [OKX documents as disabling
Cancel All After](https://www.okx.com/docs-v5/en/). The common private
transport now checks the exact immutable body after signing and immediately
before dispatch; only a 10–120 second string timeout and bounded optional tag
are accepted, with malformed/query-altered requests rejected before HTTP.
The direct `order-precheck` POST was removed from the maintenance exception
list. New exposure POSTs and set-leverage remain hard-denied. The focused
exchange selection passed 121 cases; no exchange write occurred. Direct
cancel-order and close-position clients still lack a service-issued,
account-bound durable maintenance permit, so all maintenance routes are **not**
accepted as guarded. They remain available under existing service controls
to avoid severing emergency reduction before a reviewed replacement exists.

A combined five-module offline selection passed 108/108 with zero failures,
errors or skips; its JUnit SHA256 is
`d5b6ff340fdd7289f597a9ba7f0c3ae6dec5e495a5a5c3adc0d4c59010571b51`.
Whole-project Ruff check and format check passed on the working source. A wider
local account selection was interrupted because this host has a history of
thermal shutdown; its partial output is not a regression pass. Exact-source
Windows/Linux, PostgreSQL and Docker validation, authenticated Demo reads,
true Gate 3/OOS, Live readiness and release acceptance remain open.

## 2026-10-06 source checkpoint and synchronized public-clock diagnostic

Commit `4672a7a09cf27d723db8850905ab3d46c7719f70` has tree
`58ee537f0f31cdaf104a85eff4e41da067110fda` and is retained under
`../validation-results/ctcc-source-4672a7a-20261006/`. Its exact source TAR
SHA256 is `2f2a69cf1c1ade9420ccb1ab3227171abdbdce66ab1df9a376bbeaaf69756200`;
the complete-history bundle SHA256 is
`a6798bc1ddf8464937ec662dc0da3253ca90a1de5a81183f3d538d762c06442d`.
Readback matched all 900 Git blobs, executable bits and 899 manifest entries.
The manifest SHA256 is
`a7f8db7c2eadf2040685391300bc2c3c21aa6dd79fd11214afc74652c4457bad`.
The scoped high-confidence token-prefix scan found zero matches, but does
not certify every log, report, ZIP or remote-only object. The source was pushed
without force to `develop/v2-final-completion-ci-4672a7a`; matching
[run 37401190910](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37401190910)
was still running when checked. No full-regression PASS is inferred.

At 2026-10-06 09:53 Asia/Taipei, W32Time was Running/Automatic and reported
a fresh synchronization from `time.windows.com`. One later unauthenticated
production-public quote diagnostic for `BTC-USDT-SWAP` used a declared
60-second component age policy and accepted three raw-backed REST leaves:
ticker, mark and funding. The returned object explicitly had
`execution_authority=False` and `source_authenticity_verified=False`; the
observation was not archived as a trusted complete-market packet. The ticker
generation time preceded the HTTP request, as a market-event timestamp can;
request/receipt/body-completion times remained ordered. This is a clock and
collector diagnostic only, not Demo source or R5 acceptance. The prior failed
five-second-age attempt remains a failure; no timestamp was rewritten and no
future-timestamp tolerance was widened.

## 2026-10-07 exact-source remote CI result and retained failure evidence

The matching `4672a7a` [run 37401190910](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37401190910)
finished **failed**. The downloaded Linux and Windows artifacts are retained
under `../validation-results/remote-4672-ci-20261007/`; they identify that
exact source and tree. In isolated Linux PostgreSQL, the selected 213 intent
tests passed without failures or skips, and migrations reached head `0024`.
That selection alone consumed about 54 minutes. The Linux full suite then
timed out at its 6,000-second stage limit around 24% of collection, so there
is no Linux full regression, Docker hermetic or restart acceptance for this
commit. The Windows JUnit collected 13,394 cases and recorded 63 setup errors,
3 failures and 29 skips. Most errors trace to one synthetic quote-capture
fixture timing out under runner load; two failures are legacy account-packet
byte-hash drift, and one direct recheck test also failed on quote capture.
These failures remain acceptance blockers until corrected and rerun on a new
exact source. No CI green status, main merge or release is inferred from the
passing PostgreSQL selection.

## 2026-10-07 OKX account-pagination documentation audit

The current [OKX API reference](https://www.okx.com/docs-v5/en/) and
[pagination guide](https://www.okx.com/docs-v5/trick_en/) confirm the cursor
fields used by `account_capture.py`: recent and historical private fills use
`billId` (not `tradeId`); account bills use `billId`; ordinary pending and
historical orders use `ordId`; pending algo orders use `algoId`. `after` asks
for older records and excludes the cursor. A short nonempty page is not a
terminal proof. The captured page chain requires an explicit empty terminal
page, strictly advancing source IDs, the previous last-row cursor and the
previous receipt hash; duplicate/conflicting rows fail closed. A focused
pagination/history selection passed 145 synthetic cases. These checks do not
establish authenticated account completeness, exchange retention or a
trusted portfolio snapshot. Order-history time filters refer to order
creation, fill history has separate `fillTime` and bill-generation `ts`, and
bills have their own `ts`; these times are not interchangeable for realized
outcomes.

The [EEA API reference](https://my.okx.com/docs-v5/en/) currently lists four
additional pending algo types (iceberg, TWAP, chase and smart iceberg) beyond
the four in the global reference. The v5/v6 current capture plan requests
only the global four. The native V6 entry rejects non-global registration,
and independent registration-region evidence is still absent; EEA account
completeness therefore remains **unverified**. Historical v4 packet bytes
were not changed to mask this regional scope difference.

## 2026-10-07 fail-closed source hardening and CI partition work

The current working tree rejects selected required Demo and Live account,
position and order numeric, identity and attachment fields when they are
absent or nonfinite, instead of converting them to zero, false or an empty
attachment list. This does not prove complete endpoint coverage. Demo service
reads no longer discard rows
with missing order IDs or mark malformed private rows as a successful recent
exchange read. Live balance parsing requires an explicit, unique USDT detail;
Live mirror persistence rejects duplicate exchange order, position and algo
identities before opening a database transaction. The Live reconciliation
failure path disarms and attempts a durable safety latch. An original native
V6 account capture can now hand off its exact raw packet under the actual
claimed credential session, once in the same task, after the existing proof
and readback; this handoff still carries `DENY` and no execution authority.
Targeted parser, service, repository, native handoff, route and CI-partition
tests passed locally. PostgreSQL integration for these new changes has not
yet run against a configured local database.

The next exact-source CI design runs the reviewed PostgreSQL selection and
eight disjoint Linux full-suite shards in separate isolated Docker jobs. A
required `Docker hermetic regression` aggregation job verifies identical
source/manifest identity, each component's test report and readback hash,
and a one-to-one union of the full collected suite. Four synthetic union-gate
tests passed. The Windows synthetic quote fixture has a bounded larger
MockTransport timeout; production quote freshness and transport policy are
unchanged. A full CI result for this working tree is still pending. The
current Live mirror still lacks authenticated complete page chains, a trusted
account revision and protection readback before its legacy `reconciled`
checkpoint; therefore Live readiness and all-submit-route acceptance remain
**failed**, regardless of these narrower fixes.

A bounded pattern scan of the working source, reachable Git history, the
`4672a7a` source archive and downloaded CI evidence found no confirmed real
credential; matches were synthetic test fixtures. It skipped 28 binary files
and cannot detect unknown secret formats, so this is not final secret-audit
acceptance. The old exact-source checkpoint and all failure artifacts remain
retained. The local host's previous thermal shutdowns rule out treating an
interrupted broad Windows test selection as evidence of a pass.

## 2026-10-07 current CI, source-bound safety slices and evidence limits

The first split-suite exact-source [CI run 37591101243](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37591101243)
at `a9a9e1c273514742138e32f766e8990582ff11fd` immediately failed its
Linux jobs before Docker validation: invoking the verifier as a file made its
`scripts` package import unavailable. Commit
`1d5ae8082ee2aa57cc64147c08be3a37ee2235f0` changes both CI invocations
to `python3 -m scripts.verify_final_hermetic` and updates the manifest. Its
matching [CI run 37591525888](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37591525888)
was still running at this review; two isolated Linux shards had reported
success, but Windows, PostgreSQL, the other shards and exact-union acceptance
had not completed. Those older-source results cannot validate the later
working-tree changes described here.

This review's test-focused source changes add a one-use V6 Demo raw-account
projection of measured balance and current inventory, explicitly leaving
history, funding, peak, protection, local reservations and account revision
unknown. The existing native proof refuses accounts with exposure, so this is
only a flat-account diagnostic and never publishes a PortfolioRiskSnapshot.
Demo V2 public capture now repeats its region/session-origin rejection before
each native HTTP or WebSocket I/O entry. The runtime still lacks a verified
account-bound Demo region and transport, so trusted Demo R5 is not accepted.
Live cancel, close and Cancel All After direct POST paths now reject without a
durable, account-scoped one-use maintenance permit; no such issuer exists.
Read-only reconciliation, local disarm and Emergency Stop remain available,
but CTCC Live maintenance writes and Live readiness are not accepted. Focused
account, public-route and Live transport/service tests passed; legacy
cancel/fill-race and ambiguous-response tests remain covered with isolated
fake adapters. A broader combined local selection was interrupted to avoid
further thermal load and is not a regression pass.
The registered Demo and Live order-create routes currently deny new-exposure
POSTs at the private transport, including direct calls and callers setting
`write=False`; this is a closed system, not the required common qualified
G12/recheck/reservation/intent permit. The legacy Demo automation tracks
active trade state in memory and persists after POST, so
`ALL_SUBMIT_ROUTES_GUARDED` remains failed for production acceptance.

The MIE Gate 3 computational verifier and 429 focused Windows cases passed,
but all 15,120 historical aggregate bars lack measured availability at their
2024/2025 decision cutoffs, and the existing holdout was exposed before a
candidate seal. No formal sealed OOS evaluation, event-time Gate 4 trace,
economic OOS or stress acceptance exists. These research gates remain failed.
The original Notion instruction defines four distinct real evidence examples:
blocked old high-score error, qualified Demo candidate, price change during
rendering canceled at recheck, and a fully annotated `summary.png`. The
execution report was corrected to this original wording and read back; it
still records 0/4. Synthetic fixtures and the legacy automation do not count.
The connected Notion document tool cannot provide CTCC's runtime REST token
or the four opaque property IDs required by the independent outbox worker;
automatic Notion delivery remains unaccepted.

## 2026-10-07 bounded V6 exposure and V2 Demo origin diagnostics

The V6 native account runtime now has a separate read-only exposed-account
observation. It replays the original recorded page chain, rereads that chain
under the exact-account database lock, and records page/row hashes and observed
inventory counts. An exposed account still cannot seal the flat-only native
companion proof, create a PortfolioRiskSnapshot, or receive a Demo execution
permit. The receipt contains hashed account/session scope rather than raw UID,
mainUid or session binding; history, local uncertain state, protection coverage,
and an exchange-wide atomic account revision remain unknown. This path uses
signed Demo GETs and writes local evidence but performs no exchange order write.
Three new synthetic tests and eight adjacent selected tests passed; an
interrupted broader run is not a regression pass.

The V2 public collector repeats the Demo region/session-origin DENY check at
its own entry before clock sampling, journal creation or network I/O. A
malformed direct plan now receives a fixed rejection code. Twenty-one focused
public runtime cases and the direct malformed-plan case passed; the longer
module run was stopped because of the host's recent thermal shutdowns. The
available V6 account lease does not prove registration region and the present
V2 initial/post-G12 public plans still pin Production endpoints. Therefore
trusted Demo R5 remains **DENY**; no G12→R7→R6 execution eligibility follows.

The reviewed HighVol/Momentum installer package has a clean PowerShell parse,
including the corrected `${Mode}:` interpolation, and five narrow verifier
tests passed. Its original parser-failing archive is retained as evidence.
The reviewed package targets older source hashes and safely stops before
Docker/service calls when run as an installer. It has not been integrated into
the canonical safety and qualification path; install, reinstall, upgrade and
rollback acceptance remain unexecuted.

## 2026-10-07 retained exact-source and measured-availability receipts

The immutable `46b3eee` checkpoint under
`../validation-results/ctcc-source-46b3eee-20261007/` contains a source
archive independently read back against all 914 manifest entries, a verified
complete Git bundle and a checkpoint receipt. Its commit is
`46b3eeec8a21a8ebb02bdd9d5a2d76a381c6665d`, tree is
`ac3bcd96299aeac84c056a35ae13f9ca1f931557`, and manifest SHA-256 is
`7c5b88a4bf4f1a176048fe3046286907c3a0b6dd50863e2735c0a41a0bd5a1c0`.
The checkpoint explicitly records full regression as `NOT_PASSED`, Demo and
Live as `DENY`, and no accepted final Docker image digest. Older checkpoints
remain necessary to reconstruct source changes and failure evidence.

The separate `mie-availability-audit.json` in that checkpoint was rehashed
and read back at SHA-256
`6e0158a4a786bb65c06ea637cbe32845aaf998607c6d3e93146da871795ea51f`.
It verifies three native public capture receipts and seven linked artifacts:
251 captured minute rows include ten identical overlaps, leaving 241 distinct
BTC-USDT-SWAP minutes in two discontinuous windows. The 2024/2025 archive was
acquired after its decision-time cutoffs, and the retrospective holdout was
already exposed before candidate seal. Consequently the receipt marks MIE
Gate 3, sealed OOS, Gate 4 shadow promotion and economic OOS `FAIL_CLOSED`;
no candidate was fitted or exchange write attempted by this audit.

The exact-source [CI run 37595853584](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37595853584)
for `46b3eee` has a source-bound Linux shard-0 result of 1,409 passed, one
approved skip and zero failures. The other shards, Windows, PostgreSQL and
the required Docker union were still pending when this receipt was added.
That single shard and its image ID are diagnostic evidence, not full-suite,
Docker-hermetic, migration-head, trading or release acceptance. Any later
commit requires its own complete exact-source validation.

## 2026-10-07 pending control-bound reporting and pre-publication fence

The next working tree adds migration 0025 for exact V3 outer/inner submission
reporting with historical control-journal readback. It also refuses an unbound
Demo V2 issuer before the clock and G12 publication. The former records a
synthetic post-submit observation only; the latter prevents a new unusable G12
receipt when its post-publication source cannot be trusted. Neither change
issues an exchange POST or a qualification permit. Eight quick issuer tests,
the migration identity unit module and static checks passed locally. The
heavier V3 fixture, PostgreSQL migration cases and broader regression were
stopped or deferred because of the host's thermal instability; they remain
`NOT_PASSED` until exact-source remote CI completes. Demo, R7, reservation,
Live, OOS and release gates are unchanged at `DENY`/`FAIL_CLOSED`.

## 2026-10-07 subsequent Demo containment and exposed-account proof

An account-modifying Demo maintenance call (cancel order, close position, or
Cancel All After) could previously reach private HTTP through a direct client
call without the service's account checks. A new final-dispatch guard now
rejects all three just before HTTP while no trusted one-use maintenance permit
exists. Direct, base-class and service-path MockTransport tests record zero
requests; 355 focused cases passed. Existing Demo account exposure must be
handled by an operator outside this disabled transport until reviewed
maintenance authority exists. No exchange request was made by these tests.

The V6 exposed-account diagnostic now uses a separate V4 native proof and
independent no-clobber readback. It requires nonempty, exactly replayed current
inventory and the sole exposure/protection-join blocker; a stale publication
anchor or flat account cannot be relabelled as this proof. The result retains
`source_authenticity_verified=false`, `snapshot=null`, `account_complete=false`,
`flat_start_permission=false`, `execution_authority=false` and `DENY`. Nine
focused synthetic tests passed. It is not a complete account page-chain,
PortfolioRiskSnapshot, authenticated Demo reconciliation or trading acceptance.
Both slices still require fresh exact-source full regression after commit.

## 2026-10-07 source-bound CI failure triage and pending repairs

The exact `605cead5aa7189220ee993780494062ab9162888` CI run
[37599834588](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/37599834588)
completed all eight isolated Linux shards successfully. Its PostgreSQL component
ran 224 selected cases: 218 passed and six failed before the intended database
assertions. Three calls built synchronous synthetic sources containing
`asyncio.run()` from inside asynchronous tests; three migration tests looked up
the existing `0025` script in a test-only map that stopped at `0023`. The
migration upgrade, downgrade, re-upgrade, identity and drift stages themselves
reported exit zero. The required Docker union failed after the PostgreSQL
component failure; this is not a Docker-hermetic PASS. Separately, two shard
collection lists at this same SHA differ in two test IDs because a parametrized
invalid-clock test used `datetime.now()` at import time. The strict union gate
correctly rejects that drift. The Windows component was still running when
these findings were recorded.

Working-tree repairs move the four affected synthetic fixture constructors into
synchronous pytest fixtures, register actual `0024`/`0025` migration files in
the test loader, and replace the collection-time clock samples with fixed
invalid values. All 77 changed integration cases and 104 dispatch-owner cases
collected; six targeted invalid-clock cases, Ruff and format checks passed.
The database tests and full collection union have **not** passed for these
working-tree changes. The next exact-source CI run must establish that result.
The working-tree CI concurrency rule is scoped by both branch and source SHA so
a later exact-source run need not cancel the still-running prior Windows
diagnostic; this does not weaken any test or required status.

The same uncommitted source also carries the versioned Demo public regional
route/packet/transport wiring described in `docs/public_source_runtime_v2.md`.
Its focused synthetic tests passed, but the issuer remains `DENY` before clock,
G12 publication or network I/O because registration-region provenance and
complete account identity are unavailable. No real Demo or Live request or
order was made by this work. None of R5, R7, Demo, Micro Live, OOS, release or
final acceptance is promoted by these repairs.

## 2026-10-07 V6 account EOF and Notion private-path corrections

The V6 current-account diagnostic previously measured its 30-second receipt
age from response close, even though the original B1 journal had already
recorded when response bytes ended. A delayed close could make the diagnostic
call an expired page fresh. The active v4 policy now replays each B1
`body_complete` event, binds its EOF time to the exact page, and measures age
from the earliest EOF. It preserves the v3 policy bytes and hash for historical
receipt replay. A synthetic delayed-close boundary case shows v4 becomes
stale where v3 was still `observed_flat`; both remain `DENY` and grant no
account or execution authority. Focused account gateway/history tests passed
11/11 and three adjacent native proof/component cases passed. This is not an
authenticated account-source or reconciliation acceptance.

The Notion runtime token path check now rejects the managed validation
workspace and named validation, checkpoint, evidence and release-archive
directories while preserving external private AppData and POSIX config paths.
Fifteen focused Windows path tests passed. No token was read or created, no
Notion write was made, and the reporting worker remains disabled. These
changes require a new exact-source full CI run after commit; results from an
earlier source SHA cannot establish their full regression status.

The first exact-source CI run for `018c6d6` found a hermetic Linux shard-0
failure in the Notion private-path tests: its `/app` checkout and mounted
`/validation-results` made filesystem root `/` look like the managed workspace,
so private test files under `/tmp` were rejected. The follow-up excludes a
filesystem/volume root from workspace detection while keeping source, named
evidence/archive and real managed-workspace exclusions. It also removes a
process-working-directory exclusion that could similarly reject an unrelated
private root. Eighteen targeted Windows cases passed, including the previously
failing private-file and archive-path selections and two new root/CWD cases;
Ruff and diff checks passed. The `018c6d6` CI failure remains a real failed
result, and this follow-up requires a new exact-source full CI run.

## 2026-10-08 account integrity, diagnostic R7 join and CI gate

The current working source binds a Demo account session's credential content
to an independently held in-memory construction pin. Mutating both the copied
credential object and its session-local pin no longer changes the original
claim. Malformed `reduceOnly` pending orders retain their projection but make
the account materialization incomplete when remainder, scope or source time is
invalid. Neither change authenticates an OKX account or grants trading
authority.

A new same-invocation diagnostic joins the newly published and read-back G12
receipt to post-barrier native public capture and a one-use native account raw
packet. It pins the original event, entry, stop and target and rejects stale
data or invalid chronology. Its only admission is `DENY`; current Demo public
origin remains blocked before G12 and network I/O until registration-region
provenance and complete account evidence exist. A combined 241 focused Windows
tests passed locally; full Windows, PostgreSQL and Linux results for this
working tree remain unverified.

The required `Docker hermetic regression` GitHub aggregate now depends on the
Windows regression job as well as PostgreSQL and all Linux shards, and checks
all three results. Its previously required status could turn green while the
separate Windows job failed. This source correction has a focused workflow
invariant test, but it still needs matching exact-commit CI. No result from
the preceding `f1e6519` run transfers to this changed tree.

## 2026-10-08 closed-outcome arithmetic and owned-original stop

The account materializer now requires a positive raw fill price for every
mapped fill and independently recomputes the closed linear-SWAP gross PnL from
the ordered fill prices, sizes and instrument contract value. A missing price
or disagreement with the exchange `fillPnl` total leaves the outcome and
PortfolioRiskSnapshot unavailable. This is a fail-closed arithmetic cross-check,
not independent exchange authenticity or a complete realized-forensics claim.
Exact equality may reject a legitimate rounded exchange value until real
sampled source semantics are verified; no tolerance has been silently added.
The materializer, account runtime and trade-forensics focused suites passed
398 cases on Windows with synthetic source data.

An owned-original V5 preflight invokes the native V4 precursor in the same
task, checks its account-plan binding and records why the source-derived
G1--G11 inputs are still unavailable. It has no caller-supplied run, old
receipt, `passed` flag, G12 barrier or continuation callback; every result is
`DENY`, with G12, risk reservation, execution and order flags false. Its hash
receipt is a public shape diagnostic and can be constructed by a caller; it is
not source-authentication, qualification or acceptance evidence. The native
one-use raw packets are discarded by V4, and the V6 account source still lacks
complete risk/protection authority. Fourteen focused synthetic V5 cases passed.

A read-only submit-route audit found the concrete Demo private REST transport
still hard-denies order, order-precheck and set-leverage POSTs before signing
and immediately before HTTP, including manual, automation and direct base
transport paths. The account collector issues literal GET requests only.
Fifty-six route/transport and 21 score/leverage/margin synthetic tests passed
with no exchange IO. The manual service itself omits structural sizing and
must gain the same qualified authority before any future transport permit is
issued. This current hard denial is safe containment, not Demo execution
acceptance or proof that every eventual submit route is fully integrated.

These changes and audit do not establish full Windows, Linux, PostgreSQL,
Docker, source-authentic Demo, Gate 3/OOS, Micro Live, or release acceptance.
The exact-source full regression must be repeated after this patch is committed.
