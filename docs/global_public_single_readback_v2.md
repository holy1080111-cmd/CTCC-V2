# One synchronous Global semantic replay

This engineering change removes the second complete semantic parse inside
`native_live_public_source_v1._audit`. It changes no market policy, source
timestamp, original candidate, geometry, deadline, account input or execution
authority. It does not establish normal-route latency or venue acceptance.

The private Global reader independently opens the original native directory,
checks its identity, reads **every** bounded original file into an immutable
byte-only view, then invokes the existing canonical
`public_source_runtime.replay_public_runtime` exactly once. That validator
continues to check the complete journal chain, raw HTTP/WS joins, clocks,
source plan, typed Global packet, candles, quote and auxiliary semantics.

Before returning, the reader independently reads **all** files again and
compares the complete filename set, raw bytes and inventory hashes, plus the
original directory identity. Missing/extra/case-alias files, changed bytes,
semantic failures or root changes are rejected. No filesystem/network fallback,
retry, sorting of source rows, substitution or repair is introduced. This is
one semantic replay, **not** the removal of independent byte readback.

The old public `read_global_public_capture_v1` still returns only the existing
`GlobalPublicCaptureV1` DATA with `admission=DENY`, no account requests, no order
writes and false authority/qualification flags. The private synchronous reader
also returns the plan, summary, events and raw bytes produced by that same
validation. `_audit` immediately uses those local DATA values for its original
Live stage, publication, route, instrument and causal-order checks. No reader
or parsed result is retained in an owner registry or reused across an await.
Caller-created/saved data cannot issue an opaque Live owner.

An owned-source replay still performs a new independent native read. The
subsequent qualification-math raw replay is unchanged. Thus the staged-complete
and immediate owned-source paths each remove one duplicate semantic parse,
without promoting an earlier packet or saved receipt into current eligibility.

Validation on the isolated prepared branch: **100 PASS, zero skips**, covering
unchanged Global, staged, Live and raw ENTRY-quote contracts, plus single-call
orchestration, complete late-byte/inventory/root mutations, capacity limits,
actual canonical replay and rehashed invalid source data. One source-only paced
fixture observed capture plus replay at 2.836 seconds and ticker age at 2.019
seconds. Its admission/storage/clock/TLS are synthetic; it exercises neither
the full entry worker nor a venue and cannot establish readiness.

The SHA-pinned fixture
`tests/fixtures/global_public_initial_synthetic_v2.json` is explicitly
**synthetic anonymous initial-public DATA**, not a staged bank, account evidence,
venue case or point-in-time availability claim. It retains original mock-wire
bytes and source timestamps. SHA256:
`1774898b1b9c78a92ae2b0e234cf480932eed0475258866c21dd01ce63c06ff0`.
It contains 108 raw files; the export checked for private authentication headers
and account identifier fields. The maintenance generator uses the original
mock-wire capture and canonical validators, writes with no-clobber semantics,
and is never imported by runtime code.
