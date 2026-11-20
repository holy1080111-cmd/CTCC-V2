# Owned Live public source

`app.trade_qualification.native_live_public_source_v1` consumes one original,
same-task coordinator request from `app.okx_live.owned_qualification_v1`.
The coordinator owns the request registry and compiled source guard. It must
include this module and the original Global transport/projection and Live quote
dependencies in its reviewed source inventory. Missing coordinator admission
fails before the clock, directory or network is opened.

The coordinator calls `capture_owned_live_public_v1(request, key=...)` with its
private `_PUBLIC_SOURCE_KEY_V1`. The adapter calls the fixed coordinator function
`_consume_live_public_request_v1(request, key=...)` with the adapter's private
`_COORDINATOR_REQUEST_KEY_V1`. Only that original registry can return
`LivePublicCaptureParametersV1`. Constructing parameters, reading the saved
receipt or constructing a lookalike owner cannot admit a capture or replay.

The frozen parameters have these fields:

| Field | Meaning |
| --- | --- |
| `root` | Absolute, existing empty directory supplied by the original coordinator |
| `report_id`, `instrument_id` | Fixed external lineage and exact approved SWAP |
| `invocation` | Original coordinator identity object |
| `stage` | `initial` or `post_g12` |
| `expires_at`, `expires_monotonic_ns` | Original UTC and monotonic expiry bounds |
| `candidate_sha256` | Original candidate digest; required after G12 |
| `publication_barrier` | Original native `ClockStamp`; required after G12 |
| `publication_receipt_sha256` | Original immutable G12 receipt digest; required after G12 |
| `public_policy_sha256` | Exact `POLICY_SHA256` from this adapter |

Capture invokes the existing anonymous Global production REST/WS collector with
the fixed policy: four 240-bar OHLC frames, REST ticker/mark/funding/books/open
interest and WS ticker. Raw Global bytes keep their original environment,
origins, report identifier, TLS/clock observations, event chain and hashes.
There is no simulated-trading header or Demo lease. No credential is accepted.

The adapter creates `raw/` for that original journal and publishes a separate
`live-public.json` envelope in the parent with no-clobber write and readback.
The envelope binds Live consumption to the original invocation/candidate/G12
barrier and to the exact raw journal, plan, packet and complete file inventory.
Every HTTP request and WS connection must start strictly after this capture's
native invocation start. A post-G12 capture also requires that invocation to
start strictly after both components of the original publication barrier.
A retained pre-barrier packet cannot satisfy these checks.

`_replay_owned_live_public_v1(owner, key=...)` freshly reopens all retained raw
bytes and checks the full original journal before projecting `MarketSnapshot`,
`LiveExecutableQuoteV1` and the WS reference. The quote uses unchanged strict
timestamp and funding semantics. SWAP contract/base/quote volume units retain
their existing V2 meaning. Replay performs no network requests. The owner is
bound to its original task, loop, process, thread and both expiry clocks.
Rejection burns that owner and keeps the raw journal and accepted envelope.

The returned evaluation and saved evidence are DATA. They have no account,
reservation, submission, Arm, point-in-time historical availability or
execution authority. A coordinator must replay the private owner and bind its
lineage at its own final authority boundary. An initial capture cannot be
relabelled as post-G12. The original expiry can only be shortened.

Tests use synthetic TLS, clocks, directories and public payloads, including a
clearly synthetic coordinator registry. They cover fresh initial/post-G12
capture, complete readback, preserved units and raw environment, cache
substitution, stale/tampered/expired source, private request/key checks, task
transfer, cancellation and late filesystem failure. These tests make no claim
of an externally collected Live case, native storage acceptance, Gate 3 or Live
trading acceptance.
