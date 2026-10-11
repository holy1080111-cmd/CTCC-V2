# D0: same-invocation lineage, with dispatch denied

`app/trade_qualification/dispatch_ownership.py` adds a process-local registry for
an original reservation request and its independently read v2 submission intent
or control-bound intent envelope. It deliberately has **no production issuer,
READY transition or send function**. The shared private transport checks for an
owner/slot but still rejects every write unconditionally. All existing entry
write routes remain denied by `execution_authority.py` and
`exchange/okx/private_rest.py`.

The shared private transport currently rejects **every non-GET private call**,
including legacy cancel-order, close-position and cancel-all-after maintenance
POSTs, even if an earlier route classifier is bypassed. This is safe containment
for a flat, disabled deployment, but it is not a usable emergency-reduction
path for an account with exposure. Do not deploy this branch over tracked or
uncertain exposure until a separately reviewed, account-bound maintenance
authority is implemented and verified. The old lower-level write transport's
ambiguous-response behavior is presently unreachable; its former positive-POST
unit expectations are no longer acceptance evidence.

This is infrastructure for a future owned coordinator. Passing its tests is not
acceptance of authenticated account/public sources, PostgreSQL, Demo, Live or a
first-byte write guard. No migration or existing serialized contract changes.

## Internal interface

1. Construct `DispatchOwnership` with the existing exact
   `QualificationLedgerRepository` and a Demo `LedgerScope`. The scope binds the
   exact numeric UID and settlement currency. Repository/clocks are runtime
   dependencies, not certificates of source authenticity.
2. `begin(original_request)` validates and privately copies the existing
   `ReservationRequest` before durable consumption. It returns an opaque slot
   whose identity exists only in that owner's registry. The original candidate,
   event, policy, fixed geometry, risk and expected revisions remain in that
   request. This call itself neither reserves nor consumes risk.
3. `bind_intent(slot, committed_record)` validates an inner v2 intent;
   `bind_control_bound_intent(slot, committed_envelope)` validates the exact
   controlled envelope and its inner v2 intent. Each synchronously claims the
   attempt and returns an owner-held `asyncio.Task`. The task calls the matching
   repository readback method in a separate session and compares the entire
   replayed canonical record. A successful result is **`bound_denied`**, a
   historical lineage observation with execution and retry authority false.
4. `require_ready(slot)` always terminates the slot with
   `dispatch_trusted_producer_unavailable`. There is no injected producer,
   callback, bool, hash, Arm display or token that can change this decision.
5. `cancel(slot)` synchronously tombstones one invocation. `revoke_now()` and
   `close()` permanently invalidate the owner and all its nonterminal slots.
   `inspect(slot)` reports local status only; it does not read fresh control or
   account state. A diagnostic DTO, even one constructed with `state="READY"`,
   is not accepted by any ownership operation.

## Lineage and time rules

The canonical v2 record binds the full existing request and consumed receipt:
reservation, original event, report/candidate, environment, exact UID,
settlement currency, account and ledger revisions, fixed entry/SL/TP/contracts/
leverage, proposed FOK request, isolated mode, position side, expiry headers,
protection IDs and triggers, account packet/plan, evidence, recheck, execution
binding and economics digests. All of those are replayed by existing intent
code; D0 does not add a parallel order builder or accept a changed rehashed body.

An invocation must start no later than the recorded consumption time. That
consumption must occur no later than binding starts; independent readback must
finish before the original deadline. UTC and monotonic samples cannot regress.
Monotonic elapsed time also expires the slot when a wall clock merely stalls.
These comparisons are local chronology checks, not host-clock attestation or
proof that caller-supplied inputs came from trusted B/C producers.

The controlled envelope preserves historical control owner/epoch/config pins;
D0 cannot turn them into a fresh current-control observation. Authenticated
credential session, verified current account revision, elapsed market path and
price-bound risk proof must come from future controlled B/C producers. The
proposed request also is not a verified signed/wire request. Existing false
authority fields remain false, so production READY remains unavailable.

## Race and lifetime rules

Binding captures generation **before its first await**, including before waiting
for the per-owner read mutex. It checks that generation and slot state again
before DB IO and after readback. Synchronous revocation cannot be repaired by
capturing a newer generation after queued work wakes up. Late successful reads
cannot revive a cancelled or revoked slot.

The attempt is claimed before the read Task is created, rather than inside an
async function that might never start. Even cancellation before the task's first
step leaves a one-use tombstone. The owner retains the task until its completion
callback records cancellation/failure and retrieves any exception. That callback
never promotes a state or logs an exception; private DB errors are converted to
fixed rejection codes. Task-creation failure closes the unstarted coroutine and
tombstones the attempt. Callers await the returned Task directly; they need not
wrap it in `asyncio.create_task`.
Outstanding task cancellation is checked before read IO and after it returns;
even a dependency that catches `CancelledError` cannot bind a late success while
the owned task still has a cancellation request. Cancellation requests on the
invoking task also reject creation of a new read attempt.

Cancellation, duplicate binding and failed readback retain event tombstones.
Readback uncertainty permanently invalidates the owner; there is no automatic
read or submit retry. The registry keeps at most 64 invocations and never evicts
a cancelled event to make space. This is a local bound; durable cross-process
event uniqueness remains the existing PostgreSQL reservation ledger's job.

Slots cannot be constructed normally, subclassed, copied, deep-copied or
pickled. A forged object is not a registry member. A new owner cannot adopt an
old slot. PID, thread and async-loop changes reject continued binding; an
inherited registry after a process change is revoked. Diagnostic readback after
restart can inspect the durable journal through existing APIs, but cannot create
a dispatch permit. Python process compromise/reflection is not treated as a
security boundary against arbitrary code execution.

The D0 local revoke is **not** a durable EStop command and makes no claim about
instantaneous cross-process stopping. DB0019 control remains a separate durable
component. `bound_denied` proves only that the historical consumed intent was
read back; Arm, EStop and lease state can change after consume or during that
readback. D0 does not re-read current control, so an old armed control state in
the envelope cannot authorize dispatch. A future coordinator must connect both
lifetimes and recheck current control; copying an Arm display into D0 would not
establish authority. The future transport also needs a reviewed first-byte
guard and a precise DB/egress failure model. An HTTPX request hook or a check
before `await client.request` cannot supply it.

## Validation scope

`tests/unit/test_dispatch_ownership_contracts.py` and
`tests/unit/test_dispatch_control_bound_ownership.py` use genuine computational
intent replay from explicitly synthetic fixtures and repository read doubles
defined only in tests. They exercise queued revocation/cancellation, duplicate
bind, terminal replay, independent readback mismatch, changed lineage and fixed
geometry, UTC/monotonic expiry, historical/future consumption, legacy v1, DB
failure, request copying, foreign objects, process lifetime and DTO forgery.
No synthetic producer can promote a slot to READY because no such issuer exists.

Legacy containment regression must accompany this slice. Real PostgreSQL
readback/race acceptance remains a separate required integration run; unit
doubles do not satisfy it. No credential, external request or order is needed
for these D0 tests.
