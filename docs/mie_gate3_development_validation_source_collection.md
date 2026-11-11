# Development/Validation source collection V1

`DevelopmentValidationSourceOwnerV1` owns an actual source-acquisition path;
it does not require a candidate, preregistration, holdout, or seal. It uses
`PublicCheckpointCaptureService.capture_source_with_post_read_v3` to acquire
fresh native public data, retain the existing attempt/raw journal and DB0024
witness, append one DB0035 post-read observation, and independently replay the
source, witness and exact persisted observation. A successful ordinal has
`CAPTURED_POST_READ_VERIFIED`. This is an acquisition/readback result, not a
predictive, candidate-seal, promotion, or trading admission.

The portable `DevelopmentValidationSourcePlanV1` fixes all coordinates and
their original `PublicMinuteCapturePlanV1` digests. Each coordinate names its
partition, historical-warmup or prospective-collection purpose, instrument,
origin, complete minute window, and ordinal. Planning time is declared metadata;
it is not a protected publication timestamp or a trusted availability sample.
The plan rejects overlaps for the same instrument, changed nested plans, missing
or reordered ordinals, and authority coercion. It owns no native capability.

`build_prospective_devval_source_plan` verifies the canonical pilot bytes and
their retained SHA256 before mapping every prospective minute to this source
plan. The conversion preserves the pilot's origin, planning time and ordinal
order. It checks the native plan size and reserves the full Development
readback interval before Validation starts.

One capture still permits at most 3,000 rows, 100 rows per page, and 30 pages.
The new collection has at most the existing 1,024 capture/attempt coordinates;
no old 960-, 1,024-, or 4,096-coordinate limit is enlarged. For example, one
84-hour warmup has 5,040 minutes per instrument. BTC and ETH can each use fixed,
disjoint 2,880- and 2,160-minute batches: four captures and 10,080 source rows.
This is a batch plan, not a claim that the old single-minute 4,096-row holdout
schedule can contain that history. Missing rows cannot be filled by another
request, an extended plan, source substitution, or a replacement ordinal.

Initialization requires a pre-existing empty native owned root. It publishes
and reads back the exact plan and identity, including the source journal and
native root identity. Each `capture_once` first checks the completed prefix,
creates its ordinal directory, and publishes/reads back `claim.json` before
any database or source await. No callback, client or clock parameter enters
this native path. The source service rejects a concurrent or repeated plan
attempt and issues its completion carrier only after full V3 readback. The
carrier is registered in process memory, bound to its service, plan, PID,
thread and task, and consumed once. Copying, deserializing, supplying `True`,
or constructing a lookalike does not issue a carrier.

The new service path also checks both original repository instances before its
first I/O: exact constructor fields/classes and original bound methods, exact
native `async_sessionmaker`/`AsyncSession` configuration, and the reviewed
runtime-custody native SQLAlchemy/asyncpg engine policy. Caller factories,
instance/class method replacements, event/creator callbacks and extra options
are rejected. The verified engines and native boolean options create fresh,
private session factories and exact witness/post-read repositories. A private
capture service uses those providers for capture, freeze and verify; caller
instance/factory swaps during an inner repository await cannot enter that path.
Its latest witness state is synchronized back in `finally`, including failures.
Both caller and private provider pins are rechecked at phase boundaries and
before carrier issuance. This is not a claim to instrument every nested await
inside unchanged repositories. Read-only V3 replay also uses private providers.
No old repository class or sealed method is changed. Shared engine/class hostile
mutation, arbitrary hostile in-process replacement and hostile administrators
remain outside the reviewed application-role model.

Success publishes the complete multi-row `capture-v3.json`, a hash-only receipt,
and its digest using the existing native no-clobber/flush/readback primitives.
Failure or cancellation stops the owner and preserves its claim, raw attempt
evidence, and, when possible, a safe negative receipt. An uncertain commit does
not trigger another append or capture. A late filesystem error preserves
already published V3/receipt bytes and cannot overwrite them with a failure.
Partial or missing commit-marker material remains unresolved. Nothing deletes
or refills an ordinal.

The standalone service's burned-plan set is process/instance memory only. A
new service or a different root is not a global exactly-once ledger. Finite
dataset ordinal ownership comes from this separately pinned collection store;
a standalone source carrier or saved source DTO cannot replace its claim and
readback. This slice makes no global uniqueness or cross-root restart claim.

There is no writable resume API. `SourceCollectionReadOnly.open` can inspect
retained receipts with separately retained pins. Its `reconcile_ordinal` may
replay a saved V3 artifact against the original source journal, witness and
observation repository. That path is read-only and never mints the process-local
native carrier. A claim without a complete result remains unresolved.

Historical candles acquired now retain their actual later V3 persisted
post-read `available_at` for every row. They may supply warmup context for
future decisions after that observation; they cannot supply predictive evidence
for decisions before acquisition. Candle close, plan time, HTTP metadata, and
old receipts are never substituted for post-read time. Multi-row captures do
not enter the existing `post_read_batch_v3` one-row-per-capture adapter, and
the existing sealed holdout path and V1/V2/V3 canonical contracts are unchanged.

Deployment still requires the original restricted DB0024 witness login and
separate DB0035 post-read login, matching migrations and role/catalog guards,
a healthy supported native Windows clock profile, verified native TLS,
same-origin exchange-time causality, complete original pages, and safe native
storage. No credentials, orders, scheduler, or CLI are added. Windows flush and
readback do not establish power-loss directory atomicity or protection from
administrators/same-user mutation. DB0035 server-clock trust, independent
historical custody, and first evaluator access remain unproven in V3; no new
flag clears those limitations or establishes Gate 3 qualification.

Focused tests use explicitly substituted transport/witness/DB/provider seams
and exercise native local storage separately. Additional provider tests create
real native SQLAlchemy/asyncpg engine/factory shapes without opening a connection;
they verify the positive guard path, callbacks, overrides and late mutation.
A substituted SQL-result test swaps and restores caller providers during the
original private role methods' inner await and verifies no caller callback runs.
These establish plan, wiring, one-use, failure and time invariants; they are not
real TLS, PostgreSQL, source-collection, restart/power-loss, historical PIT,
OOS, or trading acceptance evidence.

## Full prospective minute campaign

The campaign manifest binds separate, four-hour-aligned Development and
Validation windows for BTC and ETH. Each partition must cover at least
84 hours. Each one-hour collection plan contains 120 exact coordinates in
minute order, with BTC then ETH per minute. The minimum campaign binds
20,160 rows across 168 ordered, distinct collection plans. The gap between
partitions leaves the original 60-second readback window before Validation.

`freeze_prospective_devval_campaign_v1` generates the canonical parent and
every child digest. `verify_prospective_devval_campaign_v1` independently
regenerates all child plans before publication acceptance. During collection,
`capture_prospective_devval_campaign_slot_v6` regenerates the selected
child, verifies its digest and the runtime source manifest, and binds the
parent digest and child ordinal to the original V6 slot claim. The native
lease, original capture and readback deadline apply to each minute.

The declared Git commit and tree require verification against an exact source
checkpoint. The campaign also requires separate pre-window publication and
readback, original measured source evidence, and protected custody. The local
manifest and caller digest grant no pre-window, predictive, or execution
authority. Startup is explicit and a partial slot stays unresolved.

### Publication and committed observation

The install script `scripts/sql/gate3_devval_campaign_custody_v1.sql` creates
an append-only campaign and acknowledgement store in a dedicated evidence
database. Its schema is separate from the trading database's Alembic lineage.
An evidence administrator applies the script once and gives the publisher
direct schema usage and execute access to `publish_campaign(text,text)`.
The separately administered observer login receives schema usage and execute
access to `read_campaign(text)` and `acknowledge_campaign(text)`. Application
logins have no direct table privileges. The install creates no logins or grants.

The Python repository requires an exact native PostgreSQL endpoint with
hostname-verified TLS, pinned database and login OIDs, and a separately
verified source checkpoint. The publisher returns the transaction locator
after commit. The observer replays the entire canonical campaign before the
acknowledgement and reads the committed acknowledgement in a fresh transaction.
The database assigns both timestamps and refuses publication and
acknowledgement after the first Development minute opens. Readback also
checks the server timestamp against its local native request and response
clock samples. An uncertain write is resolved through read-only observation.

This records committed database ordering. Independent administration and
trusted UTC need evidence from the actual deployment. The receipt carries
no predictive or execution authority. The multi-root dispatcher must still
bind the committed observation to the original collection roots before the
first slot. The SQL integration fixture uses an explicitly selected isolated
PostgreSQL lab and generated database and logins.

### One-time campaign dispatch

Apply `scripts/sql/gate3_devval_campaign_dispatch_claim_v1.sql` to the same
evidence database after the publication schema. Give a third restricted
collector login direct schema usage and execute access to
`read_campaign(text)`, `claim_dispatch(text,text)`, and
`read_dispatch_claim(text)`. That login has no publication or acknowledgement
write rights. The immutable database claim allows one collector per campaign,
even across different local directories.

`run_prospective_devval_campaign_v1` starts explicitly before the first
Development window. It requires a new private directory, the collector
repository and the original witness, post-read and source-custody providers.
It replays the committed acknowledgement, creates every block's separate
journal, native and result directories, and binds their exact identities and
genesis digests to one roster. The collector commits its roster digest and
reads the claim in another transaction before the first window opens.

The runner waits on the native clock and dispatches each exact slot in
plan order through the original V6 owner. Each slot requires the database
dispatch claim in the same task and verifies its retained receipt, roster,
and result-directory identity. Local block and final receipts retain the
ordered digests.
Any unresolved write, partial slot, clock discontinuity, or missed deadline
stops the run. A restart leaves the original local and database claims intact.
There is no writable restart path. The receipts record capture progress and
grant no predictive or execution authority.

### Completed campaign readback

The v2 roster and dispatch documents retain the original local UTC and
monotonic samples bracketing the database reads. A later database read
confirms the immutable publication and dispatch fields. The original
brackets still have to establish readback before the first Development
minute opens.

`replay_prospective_devval_campaign_v1` takes the separately retained final
receipt SHA and source checkpoint. It verifies the roster, all directory
identities, the block chain, and every minute's V6 evidence and decision.
Each block's original journal is read against the final persisted witness,
and retained source custody and post-read observations are independently
read again. After the last database read, the verifier checks the retained
minute evidence and journal bytes again. The result contains the original
measured minutes, original partition cutoffs, and a deterministic row inventory
digest. Development decisions must end strictly before Validation opens.

An incomplete run keeps its evidence and cannot produce a completed replay.
Readback neither issues another dispatch claim nor starts a new capture.
Independent custody, trusted absolute UTC, evaluator first access, and
predictive qualification remain separate acceptance requirements.

The isolated PostgreSQL integration suite runs the custody SQL using
`CTCC_DEVVAL_CUSTODY_TEST_DATABASE_URL`. Set
`CTCC_DEVVAL_CUSTODY_TEST_TLS_CA_FILE` to the isolated server's test CA to also
exercise the native TLS repository through publication, acknowledgement,
dispatch, and readback on fresh sessions. The server certificate must match
the test URL host. This lab exercises synthetic plans and roles.
The DB0055 hermetic PostgreSQL component that owns this module generates
the temporary test certificate for its private PostgreSQL container and
mounts the public certificate into pytest. Windows classifies this module
as PostgreSQL-only.

### Operator command

Run `python -B -m scripts.run_devval_campaign` from an exact extraction of
the sealed Git archive. Every operation requires `--source-archive` and
`--source-archive-sha256`. The command checks the archive identity and the
extracted source before opening any database connection. Keep the archive,
credentials, CA file, and evidence directories outside the extracted source.

| Operation | Inputs |
| --- | --- |
| `freeze` | `--plan` with the campaign plan JSON and `--output` in a private directory |
| `publish` | `--campaign` and `--campaign-sha256`, using the publisher login |
| `acknowledge` | The same campaign file and SHA, using the separate observer login |
| `observe` | The campaign file and SHA, using the observer login. For dispatch readback, use the collector login and add `--root-binding-sha256` and `--observer-oid` |
| `collect` | The campaign file and SHA, `--root` pointing to an empty private directory, and `--observer-oid`, using the collector login |
| `replay` | The retained campaign SHA, `--completion-sha256`, `--root`, and `--observer-oid`, using the collector login. Add `--batch-output-root` to retain deterministic aggregation and feature output in a separate empty private directory |

For database operations, supply `--ca-file`, `--database-oid`, `--login-oid`,
and `--publisher-oid`. Set `CTCC_DEVVAL_CAMPAIGN_DATABASE_URL` privately in
the process that owns the selected login. Collection and replay also require
`CTCC_DEVVAL_WITNESS_DATABASE_URL`, `CTCC_DEVVAL_POST_READ_DATABASE_URL`, and
`CTCC_DEVVAL_RUNTIME_CUSTODY_DATABASE_URL`. Each URL must select its exact
restricted PostgreSQL login, using the hostname verified by the supplied CA.
The command emits a single JSON receipt with hashes and database transaction
IDs. Retain the completion SHA independently for replay.

Start collection before the Development window opens. A partial or uncertain
operation retains its original evidence. Use `observe` for committed
publication or dispatch resolution.

### Point-in-Time batch output

With `--batch-output-root`, replay uses only the minutes verified in the
current invocation. It preserves the original minute and instrument order,
separates Development and Validation, and requires measured receipts for
each minute. The existing aggregation computes 15m, 1H and 4H OHLCV in
contracts, carrying each aggregate's latest constituent availability.
Features use the original partition cutoffs with 21 bars of history.

The batch binds the campaign, completion receipt, root roster, row
inventory, and source checkpoint. It is read back from the output directory
and published without overwriting prior results. Repeating the operation
against the same retained evidence in another empty directory must produce
the same batch SHA. This artifact carries a computational claim.

### Development and Validation study rows

Replay also accepts `--study-plan`, `--study-plan-sha256` and
`--study-output-root`. The plan binds the campaign SHA, the exact
`FrozenFeatureReplayPlanV2`, event counts, and purge and embargo durations.
Retain the canonical plan SHA separately before running the study.

BTC and ETH share the later measured availability of the two base bars
as their decision time. Each feature uses its own fixed history, and the
label uses an exact in-partition outcome bar. The label read boundary is
the measured source readiness of the rows required for both outcomes.
It does not attest when an evaluator first accessed those outcomes.

Expanding walk-forward folds use Development only. Validation remains a
separate partition. A fold is refused if a training label reaches the
validation history window, including delays in the original source
receipts. The minimum 84-hour collection window alone cannot provide
4H labels after 21 bars of warmup. Choose collection windows that cover
the preregistered label horizon and sample counts before publication.

### Development candidate trials and Validation

When replay includes study output, add `--candidate-plan`,
`--candidate-plan-sha256`, `--legacy-scores`, and `--candidate-output-root`
to run the candidate study in the same invocation. Use a separate, empty
private directory for candidate output. The command verifies the canonical
trial plan against the supplied digest and the legacy probabilities against
the digest in that plan. The trial plan pins the exact study digest. The
legacy probabilities must align with the feature replay digest of every
Development and Validation row in original order. Separate custody evidence
must establish how the supplied legacy probabilities were produced and when
the trial plan was published.

Each declared trial fits only on the training rows of a Development fold.
The model bins either normalized momentum or velocity relative to volatility.
It compares pooled Development Brier score with the strongest of the
training-only constant prevalence, frozen legacy score, and no-skill baselines.
The trial record retains rejected trials and the fold results used to choose
the Development winner. The command then fits the winner on Development
and evaluates it once on Validation for this invocation. A Validation
rejection ends selection without promoting another trial. The cost model
records declared assumptions. This stage measures predictive performance
on Development and Validation and leaves Economic OOS for a separate run.

The command publishes `candidate-v1.json` with its own receipt and SHA256.
The receipt records `development_rejected`, `validation_rejected`, or
`candidate_ready_for_separate_freeze`. These are completed research outcomes
and carry no predictive OOS or execution authority. The ready outcome is
input to a separate Candidate Freeze and first-access verification process.
That process must bind the declared trial configuration and the resulting
fitted model to the formal evaluation registry.

### Fitted candidate binding

With batch, study, and candidate output enabled, replay accepts
`--formal-preregistration`, `--formal-preregistration-sha256`, and
`--fitted-binding-output-root`. Supply canonical prospective
preregistration bytes and retain their SHA256 separately. The command
checks these bytes before opening database connections and requires a
distinct, empty private directory for the binding output.

In the preregistration, keep the original trial registry and put the
selected trial's configuration SHA256 in `candidate.configuration_sha256`.
The binding separately records the fitted model SHA256 from the candidate
trial result. The training dataset names the batch SHA256 as its content,
the campaign SHA256 as its manifest, and the captured minute count and
window boundaries as its coverage. Use `bar_interval_boundaries` and
`available_at` for the time fields. The declared freeze time must cover
the last measured batch cutoff. Round study dependency, purge, and embargo
durations up to whole minutes. The embargo starts after the declared
training dataset freeze, and the Development and Validation source
windows must already have the full purge gap.

Replay derives the binding from the authenticated batch, study, and trial
result produced in that invocation. It publishes all three parent
artifacts before retaining `fitted-binding-v1.json` and its receipt without
overwriting existing files. `fitted_binding_sha256` identifies the binding
and `fitted_binding_publication_sha256` identifies the local receipt. These
record a computational lineage check. Formal seal publication, holdout
access, predictive OOS acceptance, and execution have separate controls.
For custody review, `verify_devval_candidate_binding` recomputes the study
and candidate trials from the retained batch and legacy vector, then
compares the canonical binding bytes. Independent publication timing,
the legacy score origin, and evaluator first access require their own
evidence.
