# Gate 3 scheduled public window coordination (computational only)

The opt-in, finite two-symbol four-hour research dispatcher and its separate
local append-only pin chain are described in
[`mie_gate3_four_hour_research_capture.md`](mie_gate3_four_hour_research_capture.md).
That separate root is not independent custody and does not change this
window's `INCOMPLETE` result.

`app.mie.validation.scheduled_public_window.ScheduledPublicWindowCoordinator`
coordinates the existing one-coordinate public capture and durable receipt store
across an exact, preplanned minute window. It is not registered with startup,
API, Demo, Live, risk or trading routes. It neither owns a timer nor retries a
missed minute. An external controller must call it once at the sealed due time
for each ordinal; `capture_next_once` defaults to `enabled=False` and does no
capture until explicitly enabled by that caller.

Construction requires an already initialized or resumed
`ScheduledMinuteReceiptStore`, the exact schedule, and separately retained
seal, coordinate-plan and schedule SHA-256 pins. The coordinator rereads the
store identity and validates those pins. Before any next claim, it reopens every
prior local receipt against caller-supplied external SHA-256 pins. The store
rechecks the same prefix while claiming the next ordinal under its native root
operation. Ordinals cannot be skipped, recaptured or reordered. A claim is a
one-use tombstone even if the process crashes before a receipt is published.

The coordinator delegates exactly one attempt to the existing
`capture_scheduled_public_minute_once` path. That path owns the PostgreSQL seal
and schedule readback, native due-time check, public-source journal, DB0035
observation and original one-minute source replay. The wrapper adds no alternate
source, timestamp, account access, exchange order or retry path. An early or
late invocation burns the selected ordinal and retains its original
`not_due`/`late` incomplete receipt; an unknown result preserves the claim and
blocks continuation. A missing ordinal, missing or wrong external pin,
corrupted readback, repeated source identity or prior incomplete minute also
blocks further capture. No file or journal entry is removed to restart a window.

`read_window` verifies the currently claimed prefix using the externally
retained per-ordinal receipt hashes and reports a hash of the verified ordered
prefix. It requires exact contiguous ordinal order and distinct capture,
source-receipt, witness, blind-capture and DB0035 identities. Even when every
ordinal has an intact local source-chain receipt, its status remains
`INCOMPLETE`, reason `source_chain_replayed_custody_unproven`, and claim
`computational`. Predictive eligibility, promotion, runtime consumers and
execution authority are structurally fixed to false or zero. A hash returned
by this same process is **not** proof of independent retention.

This is a bounded engineering seam, not a Gate 3 or sealed OOS result. The
window readback does not independently reauthenticate the raw public journal,
PostgreSQL roles and server clock, host UTC, first evaluator access, candidate
seal custody, complete real development/validation and holdout datasets, or a
single formal evaluation. Those remain separate fail-closed requirements.
The underlying `ScheduledMinuteReceiptStore.capture_once` now requires the
exact externally retained prior receipt SHA-256 tuple. Under the native root
operation, it verifies a contiguous claimed prefix and replays each prior
receipt with the matching pin and source-chain reason before creating the next
one-use claim. A missing or unresolved prior receipt leaves its original
tombstone intact and denies the next ordinal. The coordinator remains the
default-off window interface; these store checks close a direct-call bypass,
but the caller still has to protect prior pins independently. The V1
`due_observed_at` has only microsecond precision; an old V1 source-success
receipt remains readable but cannot continue the window. New V2 receipts also
pin the native `due_observed_utc_ns`, bind it to the displayed UTC value and
replay the exact half-open `[minute_end_ns, minute_end_ns + 60 seconds)`
boundary at publication and readback. This proves only the receipt's internal
boundary: independent host-clock trust and actual HTTP request-start causality
still need separate evidence. Neither this local readback nor a full 4/4
synthetic window is a time-attested source.
There is no live public acquisition or real candidate in the tests for this
module. Focused synthetic tests cover default-off behavior, exact four-ordinal
continuation and restart readback, skipped/early/late minutes, ambiguous result,
crash after claim, duplicate source identity and changed external pins.

## Stateless one-shot invocation boundary

`app.mie.validation.scheduled_public_one_shot.drive_scheduled_public_minute_once`
is a default-off entry point for a separately timed controller. Each call must
provide the exact externally retained DB0030 seal, coordinate-plan and DB0029
schedule hashes, the selected ordinal, every prior receipt SHA-256 in order, and
the pre-existing service-owned store root. It reopens the native store rather
than carrying an in-memory window across process restarts. It neither creates a
seal/store nor waits, loops, schedules a future call, retries, or changes source.

Before consuming an ordinal, it replays the existing receipt prefix and reads
the committed seal plus its separate observer ACK and the claimed schedule plus
its separate ACK. Missing or mismatched database evidence denies without a new
local claim. The owned minute capture repeats the same database read after the
claim, so the first preflight cannot grant stale authority across awaits. The
native source clock is checked inside that owned path. An early or late enabled
call is therefore published as a one-use `not_due` or `late` incomplete receipt;
unknown source/database results and a crash retain their original claim and
block duplicate capture. A second process reopening the store needs the
independently retained previous receipt pins, not a hash discovered from local
files after the fact.

This is an offline-tested invocation contract, not a deployed timer or real
capture. Its complete-window result remains `INCOMPLETE`, computational,
predictive-ineligible and without execution authority. Independently protected
clock and journal custody, a controlled first evaluator read, a real sealed
Candidate, complete multi-instrument forward data and unexposed OOS windows
remain unproven. The focused tests use synthetic service responses and make no
network request.
