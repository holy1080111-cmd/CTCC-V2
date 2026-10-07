# Full public runtime v2

This source slice adds the native initial and post-publication acquisition path
for `ctcc.collected_public_market.v2`. Its diagnostic output remains `DENY`.
Behavioral tests, real native acquisition, complete R5/R7 acceptance and trading
acceptance require separately recorded executions. This document is not a PASS
record.

## One native owner

`public_source_runtime._SOURCES` remains the single transport owner. There is no
v2 collector registry and no packet-to-capability import. The initial entry
`capture_initial_public_market_v2` accepts an absolute controlled output root, a
reviewed instrument and a bounded collection policy. It generates its own
invocation/report, native clock samples, TLS clients and socket. It does not
accept a caller clock, HTTP client, packet, receipt, candidate or report ID.

The private initial helper `_capture_initial_lineage_v2` returns a one-use
carrier to the same task and invocation. The diagnostic entry immediately
consumes it and constructs a disposable `PublicMarketContextV2`; neither returned
context nor packet can restore that carrier. A future candidate coordinator must
call the private acquisition helper during its own invocation rather than accept
the public diagnostic's saved output as an original-source permit.

The post-publication entry `publish_capture_public_v2` replays the unchanged
original G1–G11 inputs and calls the fixed
`publish_qualification_evidence_liquidity_v2` publisher. The private issuer
`_publish_lineage_v2` requires a newly written publication with full readback,
freezes the candidate/event/zone/entry/SL/TP/policy/expiry, samples a native
publication barrier and then registers an exact `_PublicationV2`. A previously
written receipt, failed/expired publication or caller barrier cannot create it.
The existing native runtime consumes that capability before any new IO. The
same-task coordinator consumes the resulting post-publication carrier once.

Both stages bind task, process, thread, event loop, invocation and expiry. Wrong
stage/version, foreign task, second consumption, cancellation or expiry burns or
rejects the capability. Copying, deep copying and pickling are rejected. Persisted
journals are audit data and cannot resume either stage.

The diagnostic post entry accepts original DTOs for replay, so
`original_source_verified` remains false. Current v2 G1/gates, original candidate
ownership, complete account/risk, reservation, intent and final execution
authority are separate requirements. A full native public packet does not make
them true.

## Full acquisition and packet

The fixed schedule is historical candles first, followed by quote, books/OI and
WS concurrently. Quote acquisition remains funding → mark → ticker. Every
required component must succeed; none can be replaced by a cached or caller
source. The producer reuses the existing per-endpoint HTTP transport, native
clock/TLS admission, candle and auxiliary collectors, WS collector and shielded
resource cleanup. Quote v2 owns exactly one client. The three HTTP clients and
one socket must close before a successful handoff.

The full packet embeds the original quote-v2 raw observations, all candle pages,
books/OI observations and exact WS application messages. It pins the stage,
invocation, report, instrument, environment, public origins, full collection
policy, quote profile and transport policy. Every successful raw member must
match exactly one request/response or WS event in the same native journal, with
no unmatched events. Raw bodies, canonical representations and SHA256 values are
retained by their existing contracts. Replay reconstructs the entire canonical
packet from these bytes; recomputing hashes around changed derived values cannot
override that reconstruction.

The full policy has a bounded overall deadline and bounded component deadlines.
The fixed quote policy enforces each request start through response close ≤ 2 s
and quote capture start through client close/final sample ≤ 6 s. Full completion
rechecks the fixed quote profile: ticker/mark source age ≤ 5 s, each quote receipt
age ≤ 5 s, funding data-return age ≤ 90 s, settled state and upcoming settlement
outside the 95 s guard. These are CTCC admission rules, not claimed OKX REST
publication guarantees. Books/OI/WS retain their own age bounds. Candle tails
must still match the full completion cutoff; crossing a close boundary with a
missing new closed candle fails the attempt.

Context construction checks component freshness again at its supplied evaluation
time and rejects any later missing candle tail. A journal may therefore be a
complete acquisition record while a subsequently constructed context is denied.
There is no automatic reacquisition to rescue that attempt.

## Causal ordering and funding semantics

Every post-G12 HTTP request and WS connection/subscription begins strictly after
the actual publication completion and its native UTC/monotonic barrier. Request,
headers/body receipt, response close, client close and validation ordering are
measured, never substituted with a derived exchange time. REST source timestamps
may predate a fresh request if causal and within their endpoint-specific ages.
Future-source timestamps remain rejected with zero tolerance.

The reused **v1 WS component also requires ticker source time strictly after the
publication barrier**. This is an additional conservative post-G12 limitation
in this v2 slice. A newly received WS ticker whose exchange source time predates
that barrier is denied even if otherwise fresh. The v1 contract has not been
weakened or silently relabelled; any future WS semantic change needs an explicit
versioned implementation and independent evidence.

`PublicMarketContextV2.quote.funding.forecast` pairs the raw rate with its actual
upcoming `fundingTime`. `nextFundingTime` remains the separate following
settlement forecast. The shared `MarketSnapshot.next_funding_time` preserves
that raw following time; v2 economics must use the explicit funding observation
instead of inferring the rate pairing from the shared projection. Funding `ts`
is an exchange data-return time, not proof of historical availability.

The bridge uses `parse_swap_ticker_v2`, preserving contracts and base-currency
volume semantics. Missing quote-currency turnover remains unknown. There is no
v1 `CollectedQuote` or `ExecutableQuote` manufactured from v2 data.

## Versioned journal and failures

The exact plan schemas are `ctcc.public.initial_runtime_plan.v2` and
`ctcc.public.runtime_plan.v2`. Initial plans contain no G12, candidate or event
claims. Post-publication plans pin the frozen original identity and actual
receipt/barrier. Both include explicit public packet, quote profile and transport
versions. The existing event/summary format remains v1 because its storage and
native observation semantics did not change.

The earlier V2 implementation pinned `wss://ws.okx.com:443/ws/v5/public` for
new acquisitions. That is a Production socket and cannot establish an exact
Demo source. New native V2 acquisition is now refused before journal creation
or network I/O. Exact historical receipts using `:8443` remain replayable as
`DENY` diagnostics; the old port is never used for a new capture.

Native no-clobber storage retains actual attempted HTTP/WS bytes even on parser,
clock, TLS, timeout or cancellation failure. A clock-failed receipt remains
explicitly absent. An oversized response retains only a marked bounded prefix,
actual observed size and digest; that prefix cannot become complete data. A late
publication failure never deletes already durable artifacts. The failed attempt
is not retried into the same or a replacement root.

The successful carrier is built from independent sealed readback, not the
pre-publication in-memory packet. Offline replay returns data only and checks
both native raw inventory and typed component semantics. Old v1 packet/plan
contracts retain their exact version and do not accept v2 packets. Compatibility
execution must compare old P1/P2a fixture bytes at identical logical publisher
roots; changing the logical root changes receipt/evaluation identity.

## Remaining gate integration

`data.evaluate_data` still validates an exact v1 `CollectedQuote`. This slice does
not alter G1 mathematics or build a parallel strategy engine. The next adapter
must validate the full v2 raw packet and fixed profile, then feed shared private
numeric/source/reference arithmetic through a separately versioned G1 result.
Location, history, fixed protection, economics, current conditions and recheck
need the corresponding explicit adapters before full G1–G12/R7 acceptance.

Synthetic tests replace clock/TLS/storage/IO to exercise owner lifetime, actual
issuer control flow, raw joins, failure retention and replay. They do not prove
native Windows storage, healthy time, real exchange IO, account identity, Demo
execution or Micro Live acceptance. Those outcomes are recorded separately by
the final-completion coordinator.

## 2026-10-05 native initial public diagnostic

One native Windows run acquired `BTC-USDT-SWAP` under the `demo` public plan at
`2026-10-05T10:58:04Z`. It retained a 463,652-byte v2 packet and a 126-event
journal in `../validation-results/ctcc-public-v2-native-initial-after-clock-r2-20261005/`.
The packet contains 240 confirmed bars each for 4H, 1H, 15m and 5m, two pages
per frame; three quote requests for funding/mark/ticker; two market-aux requests
for books/open interest; and a WS connect, subscribe, acknowledgement, ticker
and close sequence. Native journal replay and separate packet semantic replay
both passed. Packet SHA256 is
`77f752b39dc1e969da7198cabe577fde244e9649254d83522d18f6ab1075e256`; journal
SHA256 is `56a9c6b279c023298bba11a7ba03d10b633ffc1686bdf517c4c762bec2e57af9`.

The packet preserves the ordered volume units `contracts`, `base_currency`,
`quote_currency`. The wire mapping is `vol` to contracts,
`volCcy` to base-currency volume, and `volCcyQuote` to quote-currency volume;
for this exact `BTC-USDT-SWAP` instrument the quote currency is USDT. This is
consistent with the [OKX API guide](https://www.okx.com/docs-v5/en/). The raw
pages and instrument identity remain bound in the packet; no USD/USDT or
contract/base conversion is inferred from a missing field.

This remains a diagnostic, not an admission: `source_authenticity_verified`,
measured-availability eligibility, metadata completeness, account completeness,
and execution authority are all false, and admission is `DENY`. The successful
status is `initial_public_v2_captured_g1_metadata_account_required`. No account
request or order write occurred. The preceding attempt returned DENY because
the controlled output root had not been created; it made no network request.
After preserving that result and correcting only the output-root setup, the
single bounded R2 run completed; no automatic network retry was used.

## 2026-10-06 fresh public-only diagnostic and independent replay

After a current unauthenticated OKX server-time sample passed strict measured
request/source/receipt ordering, a new native Windows initial capture of
`BTC-USDT-SWAP` completed in 8.1 seconds. It recorded 125 events under
`../validation-results/ctcc-v2-r5-live-public-v2-20261006-r1/`; the packet
SHA256 is
`565955243c65bdffe63b9555a3cfb5f24a9e72ede3fb3ec0298ab9271d4dddc6`.
An independent process reopened the native journal and passed full raw-inventory
and typed packet replay against plan SHA256
`791cf882047616ecfdddfc279b765c6bcb3eaf6c230f6432327865bbfb9b252a`.
The summary SHA256 is
`ab8671ee1a43fefbb627ba07d80ba2b72dac8401343fec57c633a8432a304767`.
The standalone replay record is retained beside the journal. The returned
admission remained `DENY`; source authenticity, measured availability,
account completeness and execution authority remain false. No credentialed
account request or order write occurred. This is one current acquisition and
offline integrity check, not continuous-feed or trading acceptance.

## 2026-10-06 Demo-origin correction

The two native captures above used the Production public WebSocket while their
plans said `demo`. Their raw bytes and replay evidence remain useful for
diagnosis, but neither capture proves Demo-source authenticity. The official
[OKX API overview](https://www.okx.com/docs-v5/) distinguishes Production
`ws.okx.com` from Demo `wspap.okx.com`; its
[WebSocket change log](https://www.okx.com/docs-v5/log_en/) confirms that port
443 is available for the Demo domain. The regional overviews separately name
the US/AU and EEA Demo domains.

`demo_public_origin.py` now pins the reviewed global, US/AU and EEA Demo REST,
WebSocket and TLS hosts and requires `x-simulated-trading: 1` on public REST
requests. At this October 6 checkpoint it was a transport policy only: no
verified account-registration region or credential-session issuer fed the native
V2 collector, and the collector was not yet wired to these routes. Every new
native V2 Demo capture remained `DENY` before I/O.

## 2026-10-07 regional route wiring, still denied

The controlled Demo account session can now make a one-use, same-task route
declaration before either V2 issuer samples a clock or publishes G12. The
declaration pins its account plan hash, reviewed registration-region route and
frozen policy hash; `registration_region_authenticated` remains false. A signed
request on a regional private origin does not independently prove the account's
registration site. Both issuers and the native collector retain the hard
pre-clock/pre-I/O refusal until that proof is obtained and reviewed.

The dormant V2 route-specific packet policy has a new version and hash. It
binds every quote, candle and auxiliary REST observation and the WS endpoint to
one regional Demo route, and replays those fields and exact request headers
through the journal. Pure builders construct only the reviewed HTTPS GETs with
`x-simulated-trading: 1` and the reviewed 443 Demo socket. Runtime request,
redirect, TLS hostname and replay checks use that route behind the unchanged
refusal. Legacy Production packets retain their previous bytes and hash; legacy
quote and aggregate public validators explicitly reject regional provenance,
while historical Production `:8443` WS receipts remain replayable as diagnostics.

Synthetic region, cross-region, forged direct-call, header, proxy, redirect,
TLS and historical replay tests passed locally. No authenticated registration
proof, real Demo V2 capture or trading authority was produced; source
authenticity and R5 trusted Demo-source acceptance remain **DENY**. Exact-source
Windows, PostgreSQL and Docker CI must run again after these changes are
committed.
