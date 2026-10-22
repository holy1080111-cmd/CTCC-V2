# Owned native write seams v1

This component implements and tests two synchronous native boundaries. It does
not issue qualified Demo or Live order authority. Existing route DENY,
`private_write_authority`, and `DispatchOwnership.require_ready` remain intact.
`require_qualified_order_transport_v1` checks the exact existing owner/slot types,
calls the existing boundary, and still denies even if that boundary incorrectly
returns a truthy value. No legacy order payload is accepted here.

## Native path

`owned_write_seams_v1.py` owns an HTTPX transport, httpcore connection pool and
network backend for one invocation. It pins CPython 3.12, HTTPX 0.28.1, httpcore
1.0.9, AnyIO 4.15.1 and h11 0.16.0; proxies, redirects, HTTP/2, connection reuse,
alternate destinations, callbacks and transport retries are unavailable.

- **A:** a private TLS adapter passes its fixed guarded write method into the
  existing native AnyIO TLS retry loop. Immediately before **each** native
  `SSLObject.write`, it checks owning process/thread/task/loop, cancellation,
  effective AnyIO deadline, original monotonic deadline, generation, state and
  the exact next frozen HTTP plaintext bytes. A synchronous mutex covers the
  check, local attempted transition and native call. SSLWantRead/WantWrite does
  not restore a claim; after an intervening await the same continuation is
  checked again.
- **B:** only this connection's exact native AnyIO SocketStream transport is
  wrapped. Native SocketStream.send still runs its own cancellation checkpoint.
  Its subsequent synchronous transport.write enters the same mutex and repeats
  continuation/revocation checks before the real asyncio native write/queue.
  Thus a revoke after TLS encoding, during the raw checkpoint, emits no raw
  application request bytes. Pre-application TLS handshake traffic is separate.

Native class and instance-method checks reject injected stream/SSL write hooks.
No vendor source, global network method, TLS engine or strategy engine is
replaced. The adapter reuses native TCP, verified TLS handshake, SSL retry,
MemoryBIO, h11 serialization and asyncio write mechanics. On failure or
cancellation it aborts only its owned connection, without a close-handshake
flush or alternate connection. httpcore AnyIOStream.start_tls catches Exception;
the owned wrapper additionally aborts on BaseException cancellation.

The local claim is taken before the probe method's first await. An immutable
fixed body and exact h11 wire image are frozen and checked again when transport
is taken. Simultaneous or later reuse cannot claim another transport. Accepted
localhost ACK evidence is not overwritten by a rejected replay.

## Positive scope is deliberately localhost only

`LoopbackOwnedWriteProbe` is a credential-free transport exercise. Its only
destination is literal `127.0.0.1`, a nonprivileged port, one HTTPS POST to
`/ctcc-owned-write-seam-probe`, one fixed synthetic body and four fixed headers.
It requires hostname-verifying TLS with certificate validation. Its observation
always has `execution_authority=False` and `qualification_passed=False`.
The API accepts only bounded immutable public CA PEM bytes; it creates its own
native TLS context, with hostname verification, CERT_REQUIRED, fixed H1 ALPN and
no keylog/message callback. It never retains a caller-owned mutable SSLContext.
Request/userinfo/fragment/body/headers, credential headers, trace extensions and
external destinations are rejected. There is no persistence/restart/recovery
API for this local probe, and no constructor accepting a READY boolean or saved
qualification receipt.

This scope permits a real native transport test without installing a fake
qualified-order issuer. It is not a production order bridge. A future version
must consume an actually source-earned opaque owner and exact durable intent;
it must not turn this loopback capability into an exchange capability by a flag.

## Cancellation, ambiguity and persistence limits

After A is attempted, an SSL exception, cancellation or incomplete response
leaves an attempted/unknown local tombstone. Before A, cancellation/expiry/revoke
also prevents reuse. After B the bytes may already be queued; cancellation
cannot retract them. No second request is sent, including after a full request
reaches the local server and its ACK is lost.

These tests do not establish a durable reservation/intent, current account
revision or instantaneous cross-worker EStop. DB commit/readback and socket
write are not one transaction. The source-bound R7 design requires the existing
DB0017 control-bound intent chain plus a sole account write coordinator, enforced
writer domain, conservative restart/default disarm and no automatic ownership
takeover before a production issuer can be enabled. A source/account owner,
candidate/G12/recheck binding and controlled Demo/Live acceptance remain absent.
No caller DTO, local probe observation, successful TLS exchange or current Arm
display can satisfy those requirements.

## Tests and evidence scope

The unit module executes real localhost TCP and certificate-verifying TLS using
the actual pinned HTTPX/httpcore/AnyIO stack. It tests normal bytes/ACK,
simultaneous and later reuse, actual native connection-pool waiting, real TLS
handshake cancellation, post-handshake revoke/expiry, A→B raw-checkpoint
revoke/cancellation, ACK loss and cancellation after the server received the
request. Explicit test-only await instrumentation does not replace the native
positive TLS path or grant qualification. Separate synthetic SSLWantRead/Write
cuts exercise the pinned native retry loop and are labelled synthetic.

The fixture generates a temporary test-only key/cert with an existing OpenSSL
CLI, no custom crypto/dependency/system certificate store. Windows selects the
first existing executable at the two fixed Git installation paths,
`C:/Program Files/Git/mingw64/bin/openssl.exe` and
`C:/Program Files/Git/usr/bin/openssl.exe`; arbitrary PATH lookup is unavailable.
Tool profile, binary SHA and version are recorded in JUnit. The
reviewed local tool is OpenSSL 3.5.7, SHA256
`822034da8a01558c17cbe53f42f33985a6eaf7c89e21273779f9c6560d8c4d78`.
Other hosts record their own tool profile; an absent CLI is an explicit native
test skip/incomplete profile on POSIX; Windows requires one of the fixed tools
and fails if neither exists. No key or certificate
is committed or included in evidence reports.

Local Windows native results do not establish Linux/uvloop, PostgreSQL,
cross-worker fencing, qualified execution, Demo, Live or final CTCC acceptance.
