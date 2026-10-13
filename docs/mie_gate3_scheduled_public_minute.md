# Gate 3 single-coordinate scheduled public capture (computational only)

`app.mie.validation.scheduled_public_minute.capture_scheduled_public_minute_once`
is an opt-in, one-invocation research seam. It is not registered with startup,
a scheduler, Demo, Live, risk, or order routes. It accepts exact external pins
for the preregistration seal, coordinate plan and capture schedule, then reads
the committed DB0030 seal and seal ACK through the observer role. A separate
schedule ACK reader replays the DB0029 claimed canonical pin and its original
schedule bytes. Missing or conflicting readback stops before clock sampling or
public I/O.

Only the selected ordinal's sealed plan can be used. A native UTC sample must
fall from the minute's close through strictly before the next minute closes.
Early or late calls return an `incomplete` canonical receipt without opening a
public request. A due call invokes the existing controlled public witness
service exactly once. That service persists the raw public attempt/capture and
witness, appends one DB0035 post-read observation, reads it back in a fresh
session, independently replays the original source and observation, and binds
the same single minute through `BlindWindowMinuteCapture`. A changed source,
late observation, DB uncertainty, or mismatched identity returns `incomplete`;
there is no automatic retry, journal deletion, or replacement source.

`ScheduledPublicMinuteReceiptV1` contains only hashes, the ordinal, safe
reason code and optional observed timestamps. Even after full source replay its
status remains `incomplete` with reason
`source_chain_replayed_custody_unproven`: production role separation, trusted
UTC, independent journal/checkpoint custody, first evaluator access and a
complete real holdout have not been established. Predictive, promotion,
runtime-consumer and execution authority are fixed false. The unwrapped
function's freeze helper returns canonical bytes and SHA-256 in memory; it
claims no file publication or independent custody. An interrupted invocation may
leave durable raw or database evidence without a final receipt and must be
reconciled from original stores before any later attempt.

Focused synthetic tests cover due/early/late, changed ACK, uncertain result,
source-to-DB readback matching, tamper rejection, and zero authority. A
disposable PostgreSQL integration test checks the exact DB0030/DB0029/ACK
preflight and stops before public I/O; it is included in the dedicated
PostgreSQL CI group. Local PostgreSQL skip is not acceptance. No real future
seal, market request, Demo or Live action has been executed by this change.

## One-use local receipt publication

`app.mie.validation.scheduled_public_minute_publication` is an opt-in wrapper
around that existing one-coordinate seam. The caller provides the exact,
previously pinned capture schedule and a pre-existing, service-owned empty
directory. `initialize` publishes the canonical schedule/root identity with
no clobber and readback. `resume` requires the same external seal, coordinate,
and schedule SHA-256 pins; it does not discover a new identity from the folder.

`capture_once` requires the exact externally retained SHA-256 tuple for every
earlier ordinal. It verifies the complete contiguous prefix and each earlier
source-chain receipt under the native root operation before creating
`minute-<ordinal>` and writing its claim. An omitted, changed, incomplete, or
unresolved prior receipt denies the new claim without removing earlier files.
The newly created directory is a one-use tombstone. A duplicate or restarted
invocation for the same ordinal is denied,
even if a prior call crashed before `claim.json` or before the result. Unknown
source or database outcomes are not retried. The existing source service still
owns journal and DB0035 replay; this wrapper cannot turn a local result file
into independent source evidence.

On a returned result, the wrapper publishes canonical `receipt.json` and a
SHA-256 marker with the existing native no-clobber filesystem primitives,
then reads and verifies both files and the original claim. A partial, changed,
or missing result stays unresolved; no file is removed to make a retry pass.
Post-restart `read_ordinal` also requires a separately retained expected
receipt SHA-256. Calling before the due minute consumes that ordinal and
retains `not_due`; calling late retains `late`. A future scheduler must therefore
invoke only at the reviewed due time, and it must not use this seam as a
poll-and-retry API.

The new native-filesystem tests use synthetic schedule, database and capture
responses to verify no-clobber, missing/late, ambiguous return, concurrent
duplicate, crash and restart behavior. They are engineering tests, not a real
future-window observation. The local root and digest marker are not independent
custody or proof of first evaluator access. Windows flush/readback does not
promise atomic directory persistence across sudden power loss. Every stored
receipt remains `incomplete`, `computational`, predictive and promotion
ineligible, with zero runtime consumers and execution authority. There is
still no complete-window scheduler or Gate 3/OOS acceptance.
