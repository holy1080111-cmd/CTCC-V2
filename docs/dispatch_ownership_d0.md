# D0: same-invocation lineage, with dispatch denied

`app/trade_qualification/dispatch_ownership.py` adds a process-local registry for
an original reservation request and its independently read v2 submission intent.
It deliberately has **no production issuer, READY transition, send function or
transport integration**. All existing entry write routes remain denied by
`execution_authority.py` and `exchange/okx/private_rest.py`.

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
3. `bind_intent(slot, committed_record)` synchronously claims the attempt and
   validates the exact existing v2 intent against that frozen request. It returns
   an owner-held `asyncio.Task`, which the caller can await or cancel. That task
   then invokes the existing repository's
   separate-session `read_submission_intent` and compares the entire replayed
   canonical record. A successful result is **`bound_denied`**, a diagnostic
   lineage observation with both execution and retry authority false.
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
leverage, exact FOK request, isolated mode, position side, expiry headers,
protection IDs and triggers, account packet/plan, evidence, recheck, execution
binding and economics digests. All of those are replayed by existing intent
code; D0 does not add a parallel order builder or accept a changed rehashed body.

An invocation must start no later than the recorded consumption time. That
consumption must occur no later than binding starts; independent readback must
finish before the original deadline. UTC and monotonic samples cannot regress.
Monotonic elapsed time also expires the slot when a wall clock merely stalls.
These comparisons are local chronology checks, not host-clock attestation or
proof that caller-supplied inputs came from trusted B/C producers.

There is no v2 control owner/epoch/config pin that D0 can honestly manufacture.
The independent durable control observation, authenticated credential session,
verified current account revision, elapsed market path and price-bound risk
proof must come from future controlled B/C producers. Existing v2 false fields
remain false. Their absence is why production READY remains unavailable.

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
component. A future coordinator must connect both lifetimes; copying its Arm
display property into D0 would not establish authority. The future transport
also needs a reviewed first-byte guard and a precise DB/egress failure model.
An HTTPX request hook or a check before `await client.request` cannot supply it.

## Validation scope

`tests/unit/test_dispatch_ownership_contracts.py` uses genuine computational
intent replay from explicitly synthetic fixtures and a repository read double
defined only in tests. It exercises queued revocation/cancellation, duplicate
bind, terminal replay, independent readback mismatch, changed lineage and fixed
geometry, UTC/monotonic expiry, historical/future consumption, legacy v1, DB
failure, request copying, foreign objects, process lifetime and DTO forgery.
No synthetic producer can promote a slot to READY because no such issuer exists.

Legacy containment regression must accompany this slice. Real PostgreSQL
readback/race acceptance remains a separate required integration run; unit
doubles do not satisfy it. No credential, external request or order is needed
for these D0 tests.
