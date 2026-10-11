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
runtime-consumer and execution authority are fixed false. The freeze helper
returns canonical bytes and SHA-256 in memory; no file publication or
independent custody is claimed by this seam. An interrupted invocation may
leave durable raw or database evidence without a final receipt and must be
reconciled from original stores before any later attempt.

Focused synthetic tests cover due/early/late, changed ACK, uncertain result,
source-to-DB readback matching, tamper rejection, and zero authority. A
disposable PostgreSQL integration test checks the exact DB0030/DB0029/ACK
preflight and stops before public I/O; it is included in the dedicated
PostgreSQL CI group. Local PostgreSQL skip is not acceptance. No real future
seal, market request, Demo or Live action has been executed by this change.
