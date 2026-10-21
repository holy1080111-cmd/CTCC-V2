# Native post-read clock V4, stage one

This version adds an executable internal sample path and a pure canonical replay
contract. It does not complete trusted availability, Gate3 or PostgreSQL custody.
Its actual native/PG positive observation remains unexecuted. Existing V3 bytes,
contracts, source collectors, finite ordinal receipts and consumers are unchanged.

`acquire_native_post_read_clock_v4` accepts an exact
`PublicCheckpointCaptureService`, its actual `NativeSourcePostReadCapture`, the
pinned plan digest and the corresponding observation repository. It consumes the
existing native source carrier synchronously before this method performs native
health or DB I/O. Saved `FrozenGate3Artifact` bytes, a diagnostic DTO, callback,
caller clock/session or a supplied success boolean cannot replace that carrier.
The existing finite source owner must already have burned its ordinal; this
module does not reinterpret a V1 ordinal receipt as clock authority.

The issuer reuses the native engine/factory/repository checks and private-clone
pattern from the source-only owner. Two fresh native factories and exact private
repositories use initially verified engines and boolean options. Caller original
repository/factory references never enter a DB await. Caller and private pins are
checked at phase boundaries, and before and after each locally issued SQL await.
The existing witness reader's nested role and chain reads use only private
factory/repository references. This is not a claim of transitive instrumentation
of every existing nested await. Shared engine/class/global replacement by hostile
in-process code, DBAs and replacement of the running Python process are outside
the existing application-role trust model.

The fixed path is:

1. Acquire fresh accepted Windows V2 native health bytes internally. Retain their
   exact canonical bytes as base64 plus SHA256; never substitute the earlier
   market-attempt clock carrier.
2. In a private witness-engine read-only transaction, run its restricted role
   guard and the fixed `public.public_receipt_witness_read(:key) LIMIT 8194`
   query. Preserve the returned order, materialize its raw SQL rows, and use the
   original full-chain and selected-prefix verifier. The 8194th row is an
   overflow sentinel, never an accepted truncated chain (the maximum is 8193).
   `WitnessRevision` intentionally has no `recorded_at`. Require aware UTC times
   on the actual SQL rows and compare the selected raw row's `recorded_at` to
   V3's persisted `witness_recorded_at`; never synthesize that value or add it to
   the old dataclass. Compare the actual selected revision/checkpoint/transition
   and record digest to unchanged V3. Witness and observation session binds are
   independently checked against their respective private engines.
3. Enter a fresh private native session/read-only transaction. Run the existing
   restricted DB0035 role guard, then read a fixed connection/transaction
   identity. Read the DB0035 row with a fixed SQL statement and fully materialize
   and compare every V3 observation field.
4. Take a precise native data-read completion stamp and stamp A. Execute a
   separate fixed `pg_catalog.clock_timestamp()` query, fully materialize its
   single result, and immediately take stamp B. No caller callback or additional
   await occurs between that returned result and B. Backend, database/login OIDs,
   transaction timestamp/XID, postmaster start, server version/build and the
   read-only setting must equal the earlier identity. An unassigned XID is an
   explicit required null, not a fabricated transaction ID.
5. Finish the read-only transaction, check pins, collect a fresh after-health
   observation and take the final `sample_observation_completed` native stamp.
   This is the last native sample before canonical serialization, not a durable
   publication/handoff completion time or an availability cutoff.
6. Strictly validate/freeze/replay the complete V4 calculation and issue a weakly
   registered, nonserializable internal token. Only its same process, thread,
   loop and task may consume it once. Consume burns first, takes a new native
   stamp and enforces the five-second internal-token lease, clock order and original 900-second
   health freshness. Failure, expiry, cancellation or foreign-owner consumption
   cannot restore the consumed source or internal token. Restart loses this
   transient capability; it never issues a replacement from saved bytes.

There are two distinct existing five-second limits: sample B to
`sample_observation_completed` bounds after-health sampling, while final sample
to token consumption bounds the subsequent internal handoff. These are enforced
separately; they do not imply the DB sample is at most five seconds old at
consumption (the combined maximum is ten seconds). No decision freshness or
future cutoff permission follows. Both original health observations are aged
again at actual consumption against the unchanged 900-second sync-age limit.

The existing role guards verify login/grants, not deployed SQL function bodies,
triggers, constraints or schema custody. V4 therefore explicitly records
`db0035_schema_custody_verified=false`,
`witness_schema_custody_verified=false`, and
`db0046_custody_verified=false`. The exact returned DB0035 row is compared to the
actual consumed V3 result, without upgrading that row's historical clock or
immutability claims. No deployed witness function hash is frozen here. A future
custody phase must version its expected catalog policy, including DB0047's exact
unsigned root identity migration, and actually verify the appropriate deployed
function/trigger/constraint/grant bodies and deployment generation.

The time calculation uses integer units throughout. Official PostgreSQL17.11
`clock_timestamp()` calls `GetCurrentTimestamp()`, which uses `gettimeofday()`.
The MSVC Windows replacement calls `GetSystemTimePreciseAsFileTime()` and divides
the unsigned, nonnegative Unix FILETIME offset into integer microseconds. For
microsecond value U, its possible 100ns ticks are exactly
`{1000*U, 1000*U+100, ..., 1000*U+900}` nanoseconds. V4 conservatively requires the
whole cell to fit: `A_ns <= 1000*U` and `1000*U+900 <= B_ns`. It adds no timestamp
tolerance, does not round, and rejects the weaker partially overlapping case.
The supported arithmetic domain is nonnegative Unix time with Windows signed
32-bit `tv_sec`; pre-1970 unsigned underflow and post-2038 values are excluded.

The native SQL version/build string must match the finite PostgreSQL17.11,
x86_64 Windows MSVC Meson template. MinGW, Linux, another release, a legacy build
template not established by this source review or an unrecognized string fails
closed. The fixture string is synthetic, not a measured server profile. A build
string is self-description, not binary attestation: V4 explicitly retains
`database_binary_attested=false`. Tagged upstream source does not attest vendor
binary equivalence. Any actual unsupported profile needs its own source/deployment
review rather than a guessed hash or automatic allowlist update.

Primary source references (physical raw source coordinates):

- [timestamp.c, lines1630 and1654-1663](https://github.com/postgres/postgres/blob/REL_17_11/src/backend/utils/adt/timestamp.c#L1630)
  establishes the SQL-to-clock call chain.
- [win32gettimeofday.c, lines36 and66-72](https://github.com/postgres/postgres/blob/REL_17_11/src/port/win32gettimeofday.c#L66)
  establishes the epoch, precise API and integer division. Exact raw SHA256:
  `0e94f6e56ba24880f02b9cc572604bc55f3191ddfc7ae1c65235f716b50c1f6c`.
- [port Meson selection, lines54-58](https://github.com/postgres/postgres/blob/REL_17_11/src/port/meson.build#L54)
  and [win32_port.h, lines181-184](https://github.com/postgres/postgres/blob/REL_17_11/src/include/port/win32_port.h#L181)
  restrict the replacement to MSVC.
- [version template, line2846](https://github.com/postgres/postgres/blob/REL_17_11/meson.build#L2846)
  and [version.c, line23](https://github.com/postgres/postgres/blob/REL_17_11/src/backend/utils/adt/version.c#L23)
  explain the SQL build-string selector.

The separately retained official-source research receipt SHA256 is
`3a87e638d68c3b5dab6bd1ea6de1109c8d009383d20e3f33c2b5e5db2ba640e7`.
Use its physical-line correction SHA256
`4dc79462abdc2bc09396460accb8ed7d3c4d8d17585543abdb79aa3d70cece8a`;
earlier web-normalized line positions are not physical raw-file positions.

Whole-cell containment proves numerical containment of this fresh authenticated
query's returned sample in the observed native interval. It does not prove the
DB process is on the same host, absolute UTC accuracy, actual event causality or
the clock of an older DB0035 row. A loopback address supplies none of those
claims. V4 preserves both raw health observations and the distinct read/sample/
final native times, and keeps the original V3 available_at unchanged. It never
backdates a warmup row to candle close or adds rows to an old one-row batch.

`consume_internal_post_read_clock_v4` returns the exact frozen calculation only
for the next controlled owner to use internally. It is not a future-decision
consumer. `future_decision_authority`, historical availability, independent UTC,
database host identity, evaluator first read, predictive OOS, promotion and
execution remain false. A later phase must consume the actual token inside an
owned durable DB0046 append/commit/independent-readback path, authenticate deployed
catalogs, measure a final publication boundary and recheck any future cutoff.
Saved V4 payloads can only replay calculations and cannot mint this token.

The focused tests use explicit synthetic clock/native-source-registry/SQL-result
substitutes. They cover pure strict replay and false claims, quantization edges,
future/reversed/jumping/stale clocks, private native factory construction without
connections, callback refusal, fixed SQL/sample ordering, late provider rejection,
identity/row/DB failures, cancellation, one use, expiry, serialization and foreign
task consumption. These tests prove neither real Windows health nor actual PG
acceptance. Actual native PG17.11 positive sampling and deployment observations
remain required and unexecuted in this slice.

Witness regression fixtures use real `WitnessRevision` instances without added
fields and SQLAlchemy `Row` objects with the function's actual table-column
shape. The complete chain and selected raw timestamp are separately checked;
duplicate/missing revisions, missing/naive timestamps, changed persisted time
and the overflow sentinel fail before DB0035/sample SQL. Tests also exercise
different native engine objects for witness and observation sessions.
