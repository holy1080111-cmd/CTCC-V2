# Prepared Demo request diagnostic V1

`compare_control_bound_prepared_request_v1` is a pure, versioned, always-DENY
comparison. It replays a DB0019 control-bound intent against its original
reservation request, then compares the proposed Demo FOK method, path, empty
query, logical JSON body, public Demo/expiry headers, client and protection
IDs, account UID, environment and historical credential-session hash against
an immutable, non-secret projection of a prepared HTTP request. The projection
rejects authentication headers and never sends or signs a request.

Even when every available proposed field matches, the result is
`wire_binding_unavailable`, `admission=DENY`, with execution and retry authority
false. The intent's `exchange_request_sha256` hashes canonical **logical JSON**.
It does not contain the selected URL origin or serialized body bytes. The
transport constructs those separately, adds a dynamic signature and may wait
inside HTTPX before the first byte. A matching projection is caller-supplied;
its credential-session hash does not attest which key the transport used.
Consequently the diagnostic explicitly reports missing origin, wire-byte,
signature, credential-session, current-control and first-byte bindings. A
different body serialization can have the same logical comparison result while
its byte SHA differs.

The private REST transport now also has a bounded, internal observation path.
For an exact `DispatchOwnership` slot whose control-bound intent was independently
read back, it builds one HTTPX `Request`, compares that object's exact UTF-8 body
bytes, method, URL and public headers against the historical logical intent, and
the final send branch is wired to use that **same object** after the hard-DENY
gates. The immutable diagnostic stores only
hashes, mismatch labels and blocker labels: no raw body, UID, API key, passphrase,
signature or authentication headers. HTTPX `MockTransport` tests confirm that a
visible match, body/header/origin drift, expiry, cancellation and revocation all
end with zero order sends. The direct write routes remain hard-DENY before
signing, and the independent owned-write gate remains hard-DENY even in tests
that deliberately bypass the earlier classifiers.

The present Demo transport does not yet populate the intent's `expTime` header
from a trusted dispatch producer. Tests add that header to a synthetic client
only to exercise the comparison, and verify that an absent header mismatches.
The order-create body uses one compact, sorted-key UTF-8 serialization for
signing, observation and HTTPX; equivalent JSON with different bytes mismatches.

The observed bytes are not precommitted in the durable logical intent. The
selected origin is not in that intent. In particular, copying a historical UID
or credential-session hash from the account readback cannot prove which UID or
credential signed this *write*: the present `DemoPinnedReadSession` is GET-only.
Current account/control revision and one-use dispatch claim are also unproved,
and HTTPX can await internally before its first byte. These are explicit
blockers in `TransportPreparedDispatchDiagnosticV1`; a visible match still says
`admission=DENY`, never READY or permission to retry an ambiguous submission.
Future admission requires authenticated write-side identity, source-owned
public/account producers, fresh Arm/EStop/revision/expiry checks, a durable
one-use claim, and a reviewed first-byte guard.

## Intent-derived request plan (2026-10-18)

`plan_demo_fok_from_control_bound_intent` replays the complete control-bound
durable intent and derives an immutable Demo FOK proposal: the same compact,
sorted-key UTF-8 body encoding used by the private REST order-create branch,
the reservation-derived `CTQ` order and `CTA` protection IDs, and the exact
`expTime` header from the intent. It rejects a changed intent hash, Demo/FOK
scope, ID or header. The plan contains no credential, signature or selected
origin, and is always `admission=DENY` with execution/retry authority false.

This pure plan is not an independently read-back DB claim or a transport
issuer. The current Demo service still generates its own order payload and
does not receive this plan's `expTime`; no order route consumes the plan.
Matching proposed bytes therefore does not close the write-side identity,
fresh-control, source-provenance or first-byte gaps above. Every existing
private-write and route hard DENY remains in force.
