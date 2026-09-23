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

The unsealed continuation adds the separate unknown-state account bootstrap,
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

## Outstanding source-independent evidence

The host was measured behind OKX public time and NTP. Windows denied this process
permission to start W32Time. Timestamp tolerances and recorded receipt times have
not been altered. Public-source acceptance remains closed until the host clock
is repaired and directly remeasured.

Existing retrospective holdout summaries have already been exposed. They cannot
be resealed as unseen OOS or promoted from computational rehearsal to predictive
evidence. Engineering tests and genuine statistical acceptance remain separate.

Live remains default OFF. No current completion, release or trading acceptance
is asserted by this working document.
