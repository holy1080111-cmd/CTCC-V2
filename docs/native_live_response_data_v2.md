# Native Live response DATA after completed entry flushing

The execution deadline continues to apply inside every SSL and native socket
write. It is not reused as a deadline for retaining an acknowledgement to an
already submitted request.

Only the original complete request, fully accepted by SSL and successfully
flushed by the unchanged vendor helper, may enter the response-read path. The
private transport records a measured flush stamp and permits reads for at most
ten seconds from that stamp, within the existing outer client timeout (at most
ten seconds). Partial acceptance, failed socket flushing, cancellation and an
incomplete prefix cannot open this path. No caller boolean or saved receipt can
make it an execution permit.

The response scope retains the original gate registry, task/loop/PID/thread,
ContextVar, consumed qualification and exact held transaction. The original
canonical non-current session verifies source integrity, runtime/settings,
credential lease, account/source binding and causal clocks. API, vendor, public
owner and immutable intent/request/readback lineage remain pinned. The exact
TLS object, SSL context, original SocketStream and raw native owner are checked
before and after the receive await, including method-shadowing refusals.

The spent order's mathematical eligibility, Arm, active flag and quote freshness
are not execution requirements for response DATA. A revoked or stopped owner
can retain a late safe acknowledgement while all actual write paths remain
closed. Changed source, runtime, credential, account or vendor identities still
fail closed. A TLS read that attempts a protocol write still reaches the existing
full native write boundary and may be refused; no write exception is introduced.

An acknowledgement is DATA only. It cannot retry a request, restore Arm, create
a second order, release a reservation or prove a position/protection/outcome.
Those require their existing independent exchange reconciliation and durable
lineage. Unknown or interrupted outcomes continue to retain risk.

Focused tests use declared synthetic ingress and disconnected native types.
They are not credential, database, exchange or real-money acceptance.

## Retained engineering verification

`N319/N388_RESPONSE_DATA_FOCUSED.xml` records 202 passing tests, zero skips,
including the unchanged physical, preparation, continuation and genuine source
closure tests. `N319/N388_RESPONSE_DATA_FAITHFUL443.xml` records three passing
cases, zero skips, in 48.248 seconds. Each normal case delivers one complete
779-byte synthetic FOK request, validates its acknowledgement and reads back the
synthetic disarm successor; stale instrument metadata denies before TCP/intent.

In the observed native-clock case, first application ciphertext enqueue is
4.494 seconds after the new quote and first local server byte is 4.497 seconds.
There are 55 inert source-guard calls before enqueue and three after it; full
physical checks remain at five, SSL application writes at one, native writes at
three (including two handshake writes), and held readbacks at two.

The actual vendor/SSL/socket bodies are unchanged. Source/runtime/account,
credential and SQL admission remain explicit synthetic seams and actual database
connections are positively fenced. Venue writes are zero. These measurements
exclude genuine configured source/SQL/network costs and remain optimistic lower
bounds, not configured-Live or whole-project acceptance.
