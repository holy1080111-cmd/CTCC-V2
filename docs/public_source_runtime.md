# Owned public observations after G12

`publish_capture_public_recheck` is an unmounted diagnostic coordinator. It calls
the real G12 publisher, requires a newly written receipt and its readback, then
creates an opaque scope for that invocation. A loaded receipt, copied object,
caller timestamp or `passed=true` cannot create this scope. The original replay
inputs remain explicitly unauthenticated.

The coordinator uses this sequence:

1. Replay the original candidate and publish/read back G12 evidence.
2. Record a native UTC/monotonic publication barrier and unchanged expiry.
3. Retain native clock health before acquisition.
4. Acquire fresh ticker/mark/funding, four candle timeframes, books5/OI and WS
   ticker through owned connections with internally sampled receipt times.
5. Retain native clock health after acquisition and replay every component.
6. Publish the complete observation journal without replacement, read it back,
   recompute its raw joins and typed packet, then perform the pure current recheck.

The current fixed origins are `https://www.okx.com` and
`wss://ws.okx.com:8443/ws/v5/public`. They are public data origins; their use does
not establish account-region compatibility or authenticate a Demo account.
No credential, account request, Arm, reservation, intent or order endpoint is
available through this coordinator. Its result has no execution authority.

## Owned transport and clock

The producer creates three isolated HTTPX clients and one WS connection. HTTP
clients disable environment proxies, redirects, retries, auth and hooks. Native
SSL context, HTTP pool and negotiated SSL object identities are checked. The
observed TLS hostname, negotiated version and peer certificate hash are retained.
Only the pinned public GET routes and exact instrument queries are admitted.
For candles and books, the query binding is essential because the response does
not independently echo the requested instrument.

The WS connection disables proxy use and redirects. Its verified TLS object is
bound to the owned socket before subscription. Exact subscription, ack and ticker
application bytes and observed receipt times are retained. These are UTF-8 WS
application messages, not encrypted traffic, control frames or wire fragments.

All request/connection starts must occur strictly after the publication barrier.
Each valid sample retains native UTC nanoseconds and monotonic nanoseconds. Native
clock validation thresholds and the older minute-source formats are unchanged.
Exchange timestamps keep their endpoint semantics: candle open time is historical;
next funding time is scheduled. Neither is substituted for a measured receipt.

## Durable diagnostic journal

The separate `ctcc.public.runtime_* .v1` formats contain a pinned plan, chained
events, raw sidecars and a terminal summary. They cannot be relabelled as an older
minute-source receipt. Storage uses the existing native no-clobber publication and
readback primitives. Limits are 64 HTTP requests, 4096 events, 32 MiB per raw
sidecar and 64 MiB for the whole journal, with smaller component limits retained.

Received headers/chunks/WS messages are recorded before their parser advances.
If the next clock observation fails, their receipt time is explicitly absent;
actual returned bytes are retained rather than stamped with another time. An
oversized body/message fails, retaining the bounded prefix, actual observed size
and digest, and an explicit truncation flag. That prefix is never complete data.
Headers are restricted to bounded public content/date fields. Private credentials
and private account data are not inputs to this path.

A late publication error never deletes already durable raw files. It can leave
an incomplete tail without an accepted summary. Cancellation joins every owned
resource and cannot issue a capture even if a transport suppresses cancellation.
Missing readback, cleanup uncertainty, wrong TLS binding, clock failure or an
expired scope denies the result. There is no retry into the same or another root.

The low-level journal replay checks event inventory, predecessor hashes, clock
ordering, response status/headers, exact assembled raw body, page queries, packet
joins and WS sequence. The qualification-layer `replay_public_runtime` additionally
re-runs the existing typed component parsers, bundle digest and original collection
policy pin. Recomputing storage hashes cannot replace that semantic replay.
Successful runtime handoff uses these readback bytes, not a previously retained
in-memory packet. Serializable replay data never creates an opaque source carrier.

## Scope of acceptance

Synthetic contracts demonstrate source ownership boundaries, packet integrity,
failure retention and deterministic replay. They do not demonstrate native Windows
storage, healthy host time or a real OKX TLS/WS capture; those executions require
separate actual evidence. The old collector APIs and wire records are unchanged.

This producer does not authenticate the original candidate's source, complete the
account/portfolio producer, prove policy-required event survival that was never
observed, or grant `READY`. The final execution coordinator must combine those
requirements with fixed geometry, current economics/risk, durable controls,
reservation and intent. No future-price guarantee or universal continuous-tick
claim is made by this acquisition path.
