# Independent Demo public-time route diagnostic

`probe_reviewed_demo_public_time_routes` is a read-only, unauthenticated diagnostic
that reuses the three exact Demo REST origins already reviewed in
`demo_public_origin.py`: global `openapi.okx.com`, US/AU `us.okx.com`, and EEA
`eea.okx.com`. Each route receives **at most one** `GET /api/v5/public/time` with
`x-simulated-trading: 1`. The client has no account credential, cookie, ambient
proxy, redirect, retry, or order method. The native W32Time/UTC/monotonic clock
check occurs before any request. The request, response-header, body-completion,
and validation timestamps are recorded separately. A server timestamp is
coherent only if it lies between this request's start and header receipt; no
tolerance or substitute timestamp is introduced.

An absolute, pre-existing, empty private output directory is required. The
publisher writes a plan before requests and a per-route start record before
that route's only send. It retains bounded raw response bytes, their SHA256,
selected safe response headers, exact TLS hostname/version/peer certificate
hash, and a fixed rejection code. Each file is created without replacement and
read back. An interrupted directory is preserved and is not resumed. A complete
diagnostic can be independently read back with its summary SHA256. The summary
pins the exact plan, both native-clock observations, and every route's start and
result record; each result pins its raw bytes. Changed records or extra files
are rejected. `coherent_public_time_observation` is false if any route or the
before/after native clock fails, even when an individual route was observed.
The first 2026-10-13 pinned summary predates this explicit overall field;
readback derives it from the unchanged pinned clock and per-route records.
A Windows readback is not a claim of atomic power-loss durability.

An HTTP 200 response can establish only that this public endpoint was reachable
with coherent time and verified transport for that request. It does **not**
identify which site the account was registered on, prove the controlled Demo
credential's permissions, authenticate the account, or qualify market data.
Every result has `registration_region_authenticated=false`,
`source_authenticity_verified=false`, `execution_authority=false`, and
`admission=DENY`. This module does not connect to `_SOURCES`, `MarketSnapshot`,
G12, Arm, or any order path; the existing pre-I/O V2/V3 origin refusal stays in
place. A failed host clock starts zero network requests.

The [global OKX API overview](https://www.okx.com/docs-v5/) names the global,
US/AU and EEA registration-site API domains. The [global](https://www.okx.com/docs-v5/en/),
[US/AU](https://app.okx.com/docs-v5/en/) and [EEA](https://my.okx.com/docs-v5/en/)
API guides document the public system-time endpoint and Demo service origins.
These documents define a route policy, not evidence of this account's site.

Synthetic low-load tests cover one send per route, fixed public headers, strict
future-time rejection, raw readback tampering, no-clobber rerun refusal and
unhealthy pre-clock no-I/O. They also cover a redirect and changed after-clock.
Synthetic transport/TLS and clocks are not a live OKX acceptance result.

On 2026-10-13, the unauthenticated, public-only diagnostic was run once against
each reviewed route. The resulting no-clobber, hash-pinned directory is
`../../validation-results/demo-public-time-route-diagnostic-20261013-r1` and its
summary SHA256 is
`fb4d9239b26ae7758187786d8a683a12782854d89eab369450ab8c909031ba88`.
Pinned readback verified all files and all three HTTP 200 observations; the
native before/after clock was coherent. All three regions nevertheless retained
`registration_region_authenticated=false` and `admission=DENY`. The three
successful replies demonstrate why probing URLs cannot identify which site
registered this particular account. No credential, account read, market-source
admission, or order write was used.
