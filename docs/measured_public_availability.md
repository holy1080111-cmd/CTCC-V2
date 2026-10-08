# Measured public minute availability (engineering slice)

This isolated research producer is **not production accepted**. It is not called
by Demo or Live routes, changes no existing market/availability DTO and grants no
execution or predictive authority. It does not open, fit, select or relabel OOS
data. Previously late archive observations and exposed holdouts remain rejected.

The new contracts are `ctcc.public.minute_plan.v1`, `ctcc.public.raw_receipt.v1`,
`ctcc.public.measured_minutes.v1`, `ctcc.public.attempt.v1`,
`ctcc.public.attempt_chain.v1` and `ctcc.public.journal_checkpoint.v1`.
`collect_and_publish_public_minutes` accepts an exact plan, its preselected hash
and an initialized service journal. There is no public client, clock callback,
source replacement, trust flag or credential parameter. No collector is started
by application startup or by importing these modules.

## Acquisition and replay

The exact configured origin is one of `openapi.okx.com`, `us.okx.com`, or
`eea.okx.com`; it is never inferred from an account or replaced after failure.
Only public `GET /api/v5/public/time` and `GET /api/v5/market/history-candles`
are used. A new HTTPX transport disables environment proxies, redirects and
retries. Its TLS context must require peer verification and hostname checking;
the live SSL object, hostname, TLS version and certificate hash are captured
before response cleanup. A MockTransport packet is explicitly synthetic and
cannot be published through the production entry point.

The source is restricted to USDT SWAP 1m candles. Requested opening timestamps
must exactly cover the fixed window, descending in the original response.
The `after` cursor is a candle-opening timestamp in milliseconds. Short pages
continue until the requested count is present; exhausted limits, missing rows,
duplicates, reversal, unconfirmed rows or mismatched cursors fail. Rows are nine
exact strings with bounded decimal syntax and valid OHLC. Raw bytes, canonical
JSON hash, page chain, full query, first/last identity and row ordinals are bound.
Only a documented reverse converts verified source order to ascending MIE order.

`vol` is **contracts**, not base, quote or USD. `volCcy` and `volCcyQuote` remain
unchanged in the retained raw nine-column row; they are not used to fill missing
volume or to perform currency conversion. The original fields and pagination
semantics are described by the [OKX API reference](https://www.okx.com/docs-v5/en/).

The OS observation and same-origin verified-TLS time probes bracket collection.
An exchange timestamp outside its request/body-receipt interval is rejected with
zero future-time tolerance. UTC and monotonic samples must advance together;
the v1 fixed clock-jump bound is 5 ms, acquisition/validation lease is 60 s and
last-good OS synchronization must be within 900 s. These limits are not caller
overrides and do not offset or replace source timestamps.

Actual stream exhaustion is recorded before cleanup. Availability is sampled
after complete primary packet replay and cleanup. `observed_at=available_at`
uses that actual capture validation time; `retrieved_at` uses the later raw and
receipt readback time. Conversion to existing microsecond datetimes rounds up,
never backwards. Old candles collected now therefore become available now.
Neither candle close nor HTTP Date/Last-Modified is a measured receipt.

## Local journal trust and failure behavior

`initialize_public_receipt_journal` requires a preexisting empty service-owned
directory. It creates a no-clobber genesis pinned to the native directory device
and inode. The implementation reuses the existing Windows ancestor handles,
exclusive root lease and no-clobber publication primitives, or the existing
POSIX no-follow descriptors, root flock and directory fsync. It does not change
the old G12 publisher or claim that Windows directory metadata is atomically
durable through power loss.

Accepted complete captures retain all raw bytes plus a canonical receipt, then
publish `entry.json` last and read everything back. A late filesystem failure
keeps already durable files and yields no completion receipt. An unexpected,
partial, missing or unanchored directory blocks reopening; nothing auto-deletes
or auto-discovers a replacement head.

Every public acquisition first opens an owned attempt in the separate `attempts`
chain. Request start, received status/safe headers, actual raw chunks, optional
stream-exhaustion marker and final static result code are retained before a
measured receipt can be published. Rejected HTTP status, media type and headers
still retain the bounded body actually received; redirects and retries remain
disabled. A stream error preserves the prefix and actual partial timings without
inventing a body-completion timestamp. The exception's text, credentials and
private headers are never serialized.

A body limit stops further reads. A received chunk that crosses the byte bound
retains only the permitted original prefix, records observed and retained byte
counts/hashes, and marks truncation. The chunk-count bound likewise keeps prior
chunks and records the actual overflow observation. A truncated attempt cannot
be complete. Each response is bounded by the plan (at most 1 MiB), each request
by 256 chunks and each acquisition by 32 requests.

Sealed rejected/incomplete attempts have no measured-availability authority.
Successful captures must additionally bind their raw bodies, request queries,
all four request/receipt timestamps, status, headers and native TLS proof to the
completed attempt. Merely changing a receipt's source label cannot promote a
synthetic or failed attempt. The main checkpoint binds both chain sequences and
heads; replay verifies the complete inventories of both chains. A crash or late
failure leaving an unsealed/unanchored attempt preserves its bytes and denies
restart. This slice does not repair that state, guess its completion time, or
provide automated checkpoint recovery.

A duplicate observation keeps its own raw evidence while the adapter selects the
first accepted observation by complete chain replay. A differing revision of an
already observed row is saved with `rejected_source_revision`, including its raw
bytes and locator evidence. It advances the local checkpoint but raises a
rejection and cannot enter the MIE adapter. The original accepted record remains
unchanged. The service must retain the checkpoint after both accepted and
rejected publication, or restart correctly refuses the unanchored append.

`resume_public_receipt_journal` is a service configuration boundary. Its latest
checkpoint must be protected independently of the journal. A caller's arbitrary
directory/hash proves only consistency and is **not** an authenticated trust
anchor. No operator configuration store is integrated in this slice. A copied
directory, stale checkpoint or altered chain cannot resume the original native
identity. Administrator/same-user code tampering, third-party signed timestamps,
WORM storage and cryptographic nonrepudiation are outside this local mechanism.

`measured_public_minutes` replays the whole bounded journal, locates exact raw
rows, rejects revised batches and creates existing `BoundMinute` records. It
does not assign research partitions, seal candidates or make Gate 3 pass.
Its v1 `measured_row_receipt` availability is the capture-validation instant,
which precedes durable publication. The journal entry's
`payload_readback_complete` also precedes `entry.json` publication, full-chain
replay and checkpoint advancement. Neither instant proves a historical decision
could read an anchored row. The in-memory `PublishedMeasuredCapture.completed_ns`
is not a replayable, independently pinned timestamp, and the current database
witness does not retain such a timestamp. Do not use v1 availability for a
predictive or execution cutoff. The blind-window dataset's current
`durable_journal_readback` label also uses the earlier payload-readback stamp;
its fixed `predictive_oos_eligible=false` must remain in force. The computational
V2 below preserves V1 receipt/hash semantics and samples after committed
publication. A promotion version still needs an independently persisted,
replayable post-publication observation, trusted clock/custody, and first-access
proof before Gate 3 may consider decision-time availability. The V3 working
seam below addresses only the first item.
Limits are 1,024 captures, 1,024 attempts and 1 GiB across both chains per journal;
reaching a bound fails closed. Journal rotation requires a separately designed
checkpoint procedure.

### V2 committed-witness observation (computational only)

`app.mie.validation.post_publication_availability_v2` leaves all V1 bytes and
contracts unchanged. Its separate V2 artifact replays the original public
journal, selects only the first accepted observation of every row, and joins
the exact capture plan, receipt, entry, checkpoint and externally retained
witness revision/hash. The restricted PostgreSQL repository opens a new
read-only session, verifies the complete committed witness chain, and samples
`clock_timestamp()` **after** reading the selected committed revision. V2
`available_at` is that later server-clock sample, never candle close, HTTP
date, capture validation, payload readback or witness INSERT time. A database
clock preceding payload readback fails closed.

This closes a computational ordering gap only. The sampled server time is
frozen into the V2 artifact and needs an independently retained artifact
SHA-256 for later comparison. The current witness schema does **not** persist
that sample, so verification can replay all source/witness identities and
ensure a later server sample follows it, but cannot independently reconstruct
the original sample. The database clock is not certified UTC, the PostgreSQL
role/host and checkpoint custody are not proven independently administered in
production, and evaluator first access is not proven. V2 therefore fixes
`historical_observation_independently_replayable`, `trusted_clock_verified`,
`independently_protected`, `evaluator_first_read_proven`,
`predictive_oos_eligible`, `promotion_eligible` and `execution_authority` to
false. It does not rehabilitate late historical bars or the exposed holdout.
No Gate 3 promotion, Gate 4 or order route consumes this artifact.

Synthetic unit tests cover binding, external pins, source duplicates, witness
state and clock order, authority tampering, and unchanged V1 rows. An isolated
PostgreSQL test covers the separate-session read-only server-clock query; a
local skip without PostgreSQL is not acceptance.

### V3 persisted post-read observation (still computational only)

DB0035 adds an append-only observation row for one capture witness revision.
A PostgreSQL 17 or newer server is required because its restricted-role guard
checks the `MAINTAIN` table privilege; PostgreSQL 16 is not an accepted DB0035
target. The repository's pinned deployment image is PostgreSQL 17.
A separately restricted observer role calls its append function. The database
trigger reads the exact, already committed `append_capture` witness row,
compares the witness record/checkpoint/plan hashes, and takes
`clock_timestamp()` only after that read. The row also pins the capture ID,
receipt SHA-256, source-row aggregate SHA-256, and the exact V2 capture artifact
SHA-256. It is unique per journal/revision and capture sequence; mutation and
truncate are denied, and a populated table cannot be downgraded. Unknown
commit outcome is not retried.

`post_publication_availability_v3` retains the canonical V2 artifact in its
versioned payload, uses the later **persisted** database sample as V3
`available_at`, then reads the DB0035 row in a separate session. Verification
replays original raw public bytes, the full witness chain, and the exact
persisted row with all source pins. It does not recalculate or overwrite V1/V2
timestamps. The observation row's source hashes are assertions until that
external replay matches them; the database alone cannot authenticate raw
exchange bytes.

The persisted sample is independently readable, but the database clock is not
certified, DB and local journal custody are not independently established,
and first evaluator access is unproven. Historical bars retrieved after the
cutoff remain late; the exposed holdout remains unusable as sealed OOS. V3
fixes `trusted_clock_verified`, `independently_protected`,
`evaluator_first_read_proven`, `predictive_oos_eligible`,
`promotion_eligible`, and `execution_authority` to false. No Gate 4 or trade
path consumes V3. Synthetic tests validate binding and denial; isolated
PostgreSQL migration/immutability/role/readback tests must run against a real
database for DB0035 acceptance. A local skip is not a pass.

## Clock platform limitation and remaining acceptance

The original v1 replay recognizes only English `w32tm /query /status /verbose`
fields. The current native v2 adapter also recognizes the exact English and
Traditional Chinese cp950 field families on the reviewed Windows build and
resource profile described in [native clock v2](public_clock_v2.md). It retains
the raw status bytes and verifies the executable and language resource hashes,
versions, and signatures before and after the query. It does not translate or
guess unknown labels. Service Running/Automatic alone is insufficient: leap,
stratum, source, last synchronization result and freshness are checked. Unknown
language or build, stale/unknown synchronization, stopped service or failed
query is a denial. It never changes the host time, service or language. The
Windows system directory is obtained from the native API, not PATH or the
caller-controlled `SystemRoot` value.

A read-only native observation on this host at 2026-10-05 22:57 UTC accepted
the v2 clock prerequisite with W32Time Running/Automatic and retained a
Traditional Chinese status stream (SHA256
`3e758192e6a80599d2ad9d2c119741ab30276f06273bc028bde259b234ca6e95`).
That result is ephemeral and does not certify later clock health, public market
availability, or an execution decision. Every real capture still needs fresh
before/after native observations and the independent exchange-time checks.

Microsoft documents the fields in [Windows Time Service tools and settings](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings).
The language-independent [MS-W32T status structure](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-w32t/f60ebce0-df96-4c96-b40b-fdbd34a2c936)
and [RPC operation](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-w32t/7e80a465-f5f4-4c3c-87ef-12f76e45f8d1)
offer a future general-locale path, but an authenticated RPC/NDR client must be
implemented and validated against the documented protocol. A similarly named
local DLL export must not be called using an assumed ABI. The observed local
provider has Event 260 XML
fields such as `LeapIndicator`, `Stratum`, `LastSyncError` and
`TimeSinceLastGoodSync`. Microsoft says [Event 260 is logged every eight hours](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-for-traceability),
so its old timestamp cannot stand in for a fresh synchronization measurement.
Neither alternative is silently used as a weaker fallback.

A future structured adapter must call only the read-only status operation
(Opnum 6), bind the response to measured native start/end clock samples, retain
the exact response and transport outcome, and independently validate the
documented leap indicator, stratum, current state, source, last synchronization
result, and `tpTimeLastGoodSync` (100 ns units). It must add measured query lag
to the last-good-sync age, retain the 900-second limit, and keep the original
v1/v2 replay schemas immutable. Failure to authenticate, decode, or bound the
RPC response remains a denial. No local RPC client/stub meeting these
requirements is presently part of the reviewed source.

Outstanding work before production acceptance:

- A general locale-independent structured W32Time status path for other
  Windows builds/locales; the reviewed local v2 profile currently admits this
  host when synchronization is fresh. Real public source causal-probe
  acceptance remains separate.
- Independently protected service checkpoint configuration and recovery policy.
- Actual native Windows and Linux publication/restart/host-crash acceptance,
  native TLS positive integration, and finally authorized real public capture.
- Source-backed Gate 3/candidate/OOS validation, which these receipts alone
  neither supply nor replace.

The pure contract and in-memory tests use explicit synthetic data and privately
forged owned-labelled carriers to exercise replay/storage semantics. They are
not native TLS, native filesystem or real market acceptance. The separate native
filesystem tests exercise the real primitives and must pass in an eligible
validation environment. No real public collection was performed in this slice.

## Source package boundary

Owned public GET acquisition, clock observations and immutable receipt journals
live in `app.public_market_source`. The passive `app.research` package has no
runtime consumer and retains its original import boundary. Reviewed application
consumers include the offline MIE measured-minute, blind-window and
post-publication replay modules, the restricted checkpoint witness, and the
read-only qualification public-source adapter. Their exact imports are checked
by `test_public_source_boundaries.py`; replay imports grant no new acquisition
or exchange-write capability. This separation grants no account,
qualification, execution or promotion authority.
Existing receipt schemas and canonical bytes are unchanged by the module move.
