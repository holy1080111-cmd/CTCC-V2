# V2 raw quote collection seam

This additive module connects raw quote observations to the v2 fixed diagnostic
profile. It does not extend the current runtime plan schema, issue a native
source, replace the full public-market collector, or enable qualification/orders.
Existing native v1 owners do not contain the required v2 plan pins and cannot
select this seam. All packet outputs remain diagnostic, `admission=DENY`.

## Reused transport, unchanged legacy behavior

`_collect_owned_quote_v2(source)` accepts only the existing private runtime source
handle. `public_source_runtime._state` checks its exact type, registry membership,
process/thread/event loop, cancellation and deadline. The collector obtains its
clock, report, instrument, environment and publication boundary from that state.
It accepts no client, clock callback, caller DTO, request URL, `passed` flag or
caller transport policy.

The plan must pin `quote_collector_schema`, `quote_profile_sha256` and
`quote_transport_policy_sha256`. Only a future explicitly versioned owner may
install that plan and establish the genuine scope. The collector never creates
or patches a state dictionary, registry entry, clock sample or publication
receipt. It records a one-use attempt in the existing state before IO; failure,
timeout or cancellation does not clear the attempt or retry.

After that boundary, it uses the unchanged `_new_owned_client(source, "quote")`
and `quote_collector._collect_one(..., _source=source)`. The latter performs the
same exact GET construction and calls the unchanged `_fetch_http`. That native
hook enforces actual TLS/client/URL/request scope and retains headers, chunks,
body completion and cleanup before parser acceptance. Clock-failure chunks,
failed responses and cancelled attempts remain in the original journal. Neither
this module nor packet replay deletes a journal or converts a late failure into
successful publication.

The fixed transport is funding → mark → ticker, two seconds per request, six
seconds per batch and 32,768 bytes per response, without retries, redirects or
proxies. All requests are single bounded attempts. The order keeps the fast
quote acquisition after the slower funding read; it does not claim REST funding
publication frequency or introduce a WS funding source implicitly. The borrowed
v1 policy's age field is unused by `_collect_one`; a v1 ExecutableQuote or
CollectedQuote is never constructed. Existing transport user-agent behavior and
v1 default serialization are unchanged.

Successful replay also enforces measured elapsed time: each request start through
`EndpointObservation.completed_at` is at most two seconds, and the entire
capture start through its final completion sample is at most six seconds. Every
request and response completion must fall inside that capture. Equality is
accepted; one microsecond over is rejected. These admission bounds include
response body consumption and response close, and the capture bound additionally
includes client creation/closure and gaps between requests.

The underlying native two-second IO timeout covers headers and body consumption;
response cleanup has a separate existing one-second shielded timeout. Cleanup
after an IO timeout is necessary to preserve resources and evidence, but never
provides extra time for a successful packet. The private v2 seam now also reuses
the full collector's one-second bounded shielded client close. Its final sample
is taken after closure. A successful IO call can therefore still be rejected by
the stricter measured elapsed-time admission limit. The two/six-second policy
bytes are unchanged; no timeout is lengthened to accept a slow observation.

Exchange timestamps may precede their request: a newly received exchange value
can have been published earlier. The v2 profile checks the real source time
against its own receipt and its separate age limit, without requiring source
generation after request start. Request/receipt/completion order and the actual
publication barrier remain strict. Funding `ts` is never rewritten.

## Raw packet and replay

`build_diagnostic_quote_packet_v2` requires exactly three existing
`EndpointObservation` records in the fixed request order. It strictly replays
their raw/canonical hashes, request parameters, timestamps, instrument and body
size, then constructs the ticker/mark values from their original response rows.
The original funding response is reparsed by `FundingObservationV2`, preserving
the forecast's actual `fundingTime` and separate following-settlement time.
The fixed v2 quote inspector must satisfy its diagnostic profile at capture
completion before a packet can be produced. It does not confer trading authority.

The packet retains all three raw response bodies with exact observation metadata,
both policy digests, the declared capture/barrier times and the recomputed
inspection receipt. `replay_quote_packet_v2` rebuilds these from raw sources and
requires exact canonical packet equality. Extra flags, changed prices, rewritten
funding times, modified source stamps and rehashed inconsistent receipts are
rejected. Packet hashes provide integrity only; arbitrary internally consistent
caller data still has no native source authenticity or owned invocation.

The private native seam additionally checks each returned observation against
the actual response bytes/query/timestamps retained in its live registry state.
It returns a diagnostic packet, not a new ownership carrier. Only the existing
runtime's future versioned owner can create a one-use handoff after before/after
clock checks, full market/component joins, no-clobber publication, journal seal
and independent readback.

## Required next integration and current limits

The old full-market packet and runtime journal plan schema remain unchanged.
A subsequent explicit v2 owner must pin the new quote profile/transport policy,
retain existing initial/post-G12 private invocation boundaries, compose the
other trusted market components, and add exact v2 packet raw joins to the
versioned journal replay. It must reject transfer to old plans or old consumers;
do not insert this new packet into the old schema or monkeypatch a collector.

The authored tests cover pure raw replay, retained responses larger than one
JSON field limit, older-than-request cached timestamps, original settlement
pairing, rehashed tampering, exact request order/barrier, stale funding and IO
rejection for caller packets/unregistered source handles. They do not fabricate
a registry state or claim successful native integration. Behavioral execution
is scheduled separately by root; only short lint is run during this source slice.
The initial 16-test validation did not cover elapsed-time replay. A subsequent
independent synthetic counterexample showed that the original frozen parser
accepted a 20-second request or capture despite its fixed transport policy.
That original source, result and counterexample remain preserved. Revised tests
cover before-header delay, body/response-close duration, exact limits, one
microsecond overruns, capture containment and rehashed overrun attempts; their
execution is separately scheduled by root. No new native sample, complete R5,
post-G12 acceptance or order permission is claimed by this change.
