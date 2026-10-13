# Gate 3 scheduled public window coordination (computational only)

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
but the caller still has to protect prior pins independently. The stored
`due_observed_at` has microsecond precision and cannot prove the exact
nanosecond boundary accepted by the original capture clock; actual HTTP
request start also needs separate causal verification. Neither this local
readback nor a full 4/4 synthetic window is a time-attested source.
There is no live public acquisition or real candidate in the tests for this
module. Focused synthetic tests cover default-off behavior, exact four-ordinal
continuation and restart readback, skipped/early/late minutes, ambiguous result,
crash after claim, duplicate source identity and changed external pins.
