# Native Live ENTRY whole-wire buffering

The entry transport retains its original complete h11 FOK request. Its size is
limited to 16 KiB. At most 16 nonempty vendor chunks may match successive exact
prefixes. The private record stores offsets and counts into those original
immutable bytes, not caller-provided buffers or eligibility results. An invalid,
reordered, duplicated, extra or oversized chunk burns the collection state.

No SSL application plaintext or corresponding socket bytes are emitted until
the complete original request has arrived. Collection is consumed before that
first SSL call. The existing complete authority check remains inside every SSL
and native socket write, including after cancellation checkpoints and vendor
WantRead/WantWrite continuations. Partial SSL acceptance advances only the
remaining original suffix; it never resubmits accepted bytes. This makes no
claim that operating-system packets are atomic.

Incomplete requests cannot begin a response read. Failure or cancellation drops
the unsent collection state and retains UNKNOWN/risk hold. Successful vendor
flushing is recorded only after the original helper returns. Closing a completed
stream preserves its DATA counters, aborts the native transport and grants no
further authority. The controlled-close transport keeps its existing chunk path.

The focused synthetic tests cover prefix/chunk/size bounds, cancellation and
current-liveness rejection, partial SSL progress, genuine AnyIO WantRead and
WantWrite control flow, failure after a native checkpoint, incomplete-read
refusal and the unchanged close-family dispatch. Their authority/SSL ingress is
explicitly inert. The separate loopback harness retains the actual original
qualification worker, native SSL, vendor HTTP/TLS code and socket-write bodies.
The final focused receipt is `N319/N388_WHOLE_WIRE_SEALED_FOCUSED.xml`: 169
passing tests, zero skips, including the unchanged physical and source-closure
regressions. The completed-marker cleanup adjustment after the native timing
run changes retained DATA only; these final tests cover it explicitly.

## Scoped native-clock result

`N319/N388_WHOLE_WIRE_FAITHFUL443.xml` contains three passing cases, zero skips,
in 47.123 seconds. The two normal cases each deliver exactly one 779-byte
synthetic FOK request and validate the synthetic reply and independent disarm
successor readback. The stale-instrument case refuses before TCP and intent.
The unobserved first local POST byte is 4.567 seconds after the new mutable
quote; the observed value is 4.653 seconds.

The observed path performs one application SSL write and three native writes
(including two TLS handshake writes), seven full physical checks and two held
database-readback calls. It observes 62 inert source-guard calls after intent,
seven after application enqueue. These are actual call counters, not inferred
from an artificial replay loop.

The source/account/credential/SQL admission and private CA/DNS ingress are
declared synthetic, the database-connect seam is positively fenced, and venue
writes are zero. The result is an optimistic vendor-path lower bound. It excludes
real source-guard, configured runtime, account, SQL and venue/network costs and
does not establish usable configured-Live timing or trading acceptance.

The independent clean source-guard receipt
`N319/N388_CLEAN_POPULATED_Q_GUARD_20_CALLS.json` measures twenty genuine guards
before/after normal API-cache population: mean 15.276 ms, maximum 18.414 ms,
clean import 9.922 seconds before any Arm. It is a different scoped measurement;
it is not added to another counter to manufacture a whole-route PASS.

Response reads still use the preexisting full gate in this slice. Any bounded
response-only DATA factor needs its own source-owned flushed-state checks and
negative tests. It must never relax an actual write gate or authorize a retry.
