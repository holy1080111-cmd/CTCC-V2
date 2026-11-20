# Native Live post-G12 staged public source V2

The V2 producer acquires immutable closed OHLC before the new account refresh,
then acquires mutable REST quote, books/open interest and WS ticker last. It
preserves the original candidate, event, zone, entry, SL, TP, policy, publication
receipt and expiry. It never retries an expired or rejected quote.

This is an explicit closed version. The old initial Global public policy and
the separate controlled-exit public policy keep their existing contracts. The
Global component packet remains the anonymous source DATA container with its
original report ID, environment and `openapi.okx.com` / `ws.okx.com` origins.
Its wire-policy hash describes component limits; it does not claim a schedule.

## Owned sequence

1. `begin_owned_live_post_g12_public_v2` consumes the genuine coordinator request.
   Its raw plan is `ctcc.public.post_g12_global_staged_runtime_plan.v2`, with
   `stage=post_g12_staged_public` and exact original-publication bindings.
2. The native clock and exchange-time pre-bracket precede the four OHLC frames.
   An append-only `ohlc_stage_complete` record binds exact component bytes,
   measured completion, native requests, headers, response bodies and closures.
3. `replay_owned_live_public_ohlc_stage_v2` returns incomplete DATA. It has no
   quote, MarketSnapshot, account completeness or execution authority. It reads
   through the original held publisher directory and validates its exact prefix;
   it does not acquire a second publisher lease.
4. The fixed coordinator refreshes its genuine account source after that measured
   OHLC completion. Its one-use `_consume_live_staged_account_ready_v2` callback
   must replay the exact new source, original invocation, credential/account
   session, source publication barrier and control revision.
5. `complete_owned_live_post_g12_public_v2` consumes that callback and burns the
   stage before attempting any mutable source. Every new quote/aux/WS request
   starts strictly after the measured account completion. The raw journal binds
   this separate `account_stage_ready` record and source revision hash.
6. The unchanged complete Global component validator checks all raw joins,
   freshness, complete frames and current closed-bar coverage. A bar rollover
   between phases refuses the original OHLC; it never refetches a replacement.
7. Exchange-time post-bracket, native clock, no-clobber journal publication and
   independent readback precede the complete private public-source owner. The
   raw publisher lease is retired before that independent readback.

The open stage belongs to its original task, process, thread, loop and invocation.
It cannot be constructed, copied, pickled, loaded from saved files or resumed
after context exit. Cancellation, wrong task, raw mutation, failed account
binding, timeout or incomplete completion burns the stage and retains its raw
failure journal. There is no second mutable attempt.

The original Arm and source deadlines are never extended. The native attempt is
bounded by the smaller of the original remaining deadline and 30 seconds. The
five-second mutable-source limits are unchanged. Full G1–G11/R7 mathematics and
the canonical reservation/intent/final authority boundary remain mandatory in
the coordinator; this producer grants none of them.

## Scoped engineering validation

The targeted source suite passed 85 tests with zero failures, errors or skips,
including old Global source/projection, old Live source, old ENTRY raw quote and
new staged flow. New cases cover rehashed phase changes, original bar rollover,
copy/task/serialization refusal, mutable timeout/cancellation, foreign callback
globals and caller mutation of returned OHLC objects. A native Windows temporary
directory case exercised actual publisher retirement and independent readback.

A declared synthetic transport test advances its semantic clock by at least real
native elapsed time. In the full target run, capture took 2.697 seconds, capture
plus complete replay took 3.383 seconds, and ticker age was 2.633 seconds. Storage,
clock admission, TLS and the account handoff in this timed case were synthetic.
It does not exercise the full coordinator, database, credentials, providers,
orders or configured tool acceptance. The integrated coordinator must be
measured independently under the original limits.
