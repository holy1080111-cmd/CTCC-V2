# Native Live callgraph to a synthetic loopback peer

This is an engineering regression harness, not configured-runtime, account,
database, venue, fill, protection, profitability, or Live acceptance evidence.

`tests/unit/test_native_live_physical_callgraph_v1.py` runs the original owned
worker through its raw public producer, G1–G11, G12 publication/readback, original
candidate R7, intent construction, and original physical submit function. It
retains the original HTTPX/HTTPCore/AnyIO/native socket/TLS implementations.
There is no artificial 32-iteration guard replay loop.

The following admission boundaries are deliberately synthetic:

- Operator/runtime/account/source ownership and complete account facts.
- SQL role/catalog admission and declared rows, using a real AsyncEngine,
  AsyncSession transaction and independent-session control readback shape.
- Dummy signing material and a fresh pytest-private test CA.
- A DNS seam accepting only `openapi.okx.com:443` and returning the ephemeral
  loopback listener. The original client still uses hostname verification.

Every attempted actual database connection is positively rejected. The harness
does not load configured credentials, establish a real Arm, or contact OKX. Its
local synthetic HTTP POST count and zero venue-write count are separate fields.
Successful synthetic acknowledgments would still retain risk: no reconciliation
or resolution API is exercised.

Historical synthetic UTC advances by at least actual native elapsed time. All
phase measurements use separate actual native timestamps. The original five
second data/cash bounds, candidate, event, SL/TP, policy and Arm expiry remain
unchanged. The stale-instrument negative waits six real seconds before its new
account refresh and retains the original spec; it never retimes old data.

The normal timing case has no global profiler. The observation case uses CPython
3.12 local LINE events at the original refusal helper's raise line, and starts
local PY_START guard counts only after the synthetic committed-intent readback.
Coroutine resumes are not counted as fresh calls. Native socket-write return
and server first-byte observation are separate measurements; the server
observation includes event-loop scheduling delay. No function wrapper replaces
the original worker, guard, transport or native write body.

## Retained baseline, before the reader optimization

`N319/N377_PHYSICAL_EXACT_SQL.xml` has two failing normal-path budget cases and
one passing stale-instrument refusal. Its SHA256 is
`072b388b7f32d84a5ff1120ccaa849566856aabe7a188a10cc40170717bcbb56`.
This is a DENY baseline, not an acceptance result.

The corrected declared account response reaches durable intent and the actual
physical held-session readback. In the observed case, real native time after the
earliest new mutable quote was:

| Original code entry | Seconds |
| --- | ---: |
| First `_qualified` | 4.014 |
| First physical `_facts` | 4.137 |
| First `_held_readback` | 4.227 |
| First transport/physical gate | 4.509 |
| Later transport/physical gates | 4.602 / 4.695 / 4.786 |

The first actual refusal is `entry_owned_seal_v1._time`, at the unchanged
`expires_at` / `cash_cutoff` check. The fresh account observation predates the
quote by approximately 0.17 seconds. The physical record remains `prepared`,
with `attempted=false`, zero local TLS connections and zero local POSTs.
Observed counts are nine `_qualified`, seven `_facts`, four physical-gate,
three final-clock and one held-readback calls. These are actual normal-path
entries, not the earlier artificial stress-loop counts.

Earlier retained diagnostics are not budget results: the first fixture nested
`asyncio.run`, a whole-worker global profiler distorted initial capture, the
inherited account seam retained a stale initial instrument spec, and a synthetic
SQL substring matcher incorrectly classified the legacy negative predicate as a
clock query. The harness fixes only those declared test ingress defects; the
production negative fence and freshness checks remain intact.

The current baseline therefore establishes a real normal-path latency refusal,
even with synthetic account/SQL admission. A faster producer must pass the same
unchanged harness and complete the native path before any readiness claim.
