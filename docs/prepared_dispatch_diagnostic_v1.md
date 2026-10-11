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

This module is not connected to a route, owner READY transition or transport.
Future admission requires a source-owned prepared request, authenticated account
and public producers, a durable one-use dispatch claim, fresh Arm/EStop/expiry
checks, and a reviewed first-byte guard. No historical readback can substitute
for those checks or permit a retry after an unknown exchange response.
