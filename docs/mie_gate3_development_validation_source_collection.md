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
