# Public executable-quote capture

This is a prerequisite for actual qualification and post-render recheck, not
either complete evaluator. `app/trade_qualification/quote_collector.py` sends
only fixed, unauthenticated OKX public GET requests. It does not import settings,
private clients, account services or order execution, and has no runtime consumer.

## What the source times mean

| Component | Source field | Meaning retained in provenance |
| --- | --- | --- |
| Ticker | `ts` | Ticker data generation time |
| Mark price | `ts` | Exchange data-return observation |
| Funding rate | `ts` | Exchange data-return observation, not settlement time |

The [OKX public-data reference](https://app.okx.com/docs-v5/en/#public-data-rest-api-get-mark-price)
and [funding timestamp changelog](https://www.okx.com/docs-v5/log_en/#2023-11-22)
define these fields. Mark/funding data-return timestamps do not prove when the
underlying value last changed. `fundingTime` and `nextFundingTime` cannot replace
the funding response `ts`. Missing/empty rates are rejected, never defaulted to 0.
Bid/ask sizes retain native SWAP **contract** units, not base-asset quantities or
a guarantee of executable fill depth.

Each of the three records retains the exact endpoint/origin/parameters, raw body
bytes and SHA256, canonical JSON and a separate SHA256, original timestamp text,
request start, response-header receipt and completed-body/close times. UTC times
must be causal for each endpoint separately; a later merged receipt cannot hide
a component timestamp that followed its own receipt. All components are checked
again against the final collection time and an explicit maximum age of 1–60 s.
A source timestamp may precede request start if it is still fresh.

An optional publication-completion barrier requires **every** request start to
be strictly later than completion. Equal timestamps fail before network access.
The clock, injected HTTP transport, TLS and any explicit transport configuration
remain trusted runtime dependencies, not attestations that can be proven by a
hash or by constructing a data model. The collector rejects environment-proxy
inheritance, client credentials, **pre-existing** cookies, hooks and merged default
query values at capture entry. It builds fixed public requests and refuses
redirects. A server may set a cookie on an otherwise valid public JSON response;
HTTPX records it internally, but no subsequent request forwards it. Direct
`httpx.Request` objects are sent as-is, without `build_request` cookie merging.
No caller cookies are cleared or replayed. Callers must use a fresh dedicated
client per capture; reusing a now-cookie-bearing client is rejected at entry.

The first real public smoke attempt on 2026-09-12 exposed this distinction: OKX
returned HTTP 200, JSON, no content encoding and one Set-Cookie header. The initial
incoming-cookie rejection produced no quote. Stateless outgoing-header tests
were added to support that ordinary response without introducing cookie replay,
authentication, redirects or weakened freshness/identity checks. Later smoke
results are recorded separately; a mock pass is not live-source acceptance.

After that repair, the Windows public smoke remained rejected on a different
check: ticker source time `2026-09-11T18:14:07.267000Z` was 0.491220 s later than
the captured header receipt `2026-09-11T18:14:06.775780Z`. This establishes a source
versus host clock disagreement, not which clock is correct. The collector does
not change the system clock, wait artificially before recording receipt, replace
source time, or silently add a future-time allowance. Trusted clock alignment or
an explicitly designed uncertainty policy is a runtime prerequisite. No complete
valid quote, shadow observation or Demo sample was claimed from these attempts.

The final local focused collector suite has 165 passing tests. Two additional
pre-publication-barrier integration cases pass before any network IO; the four
full publication-to-capture/location cases require their own Linux acceptance.

## Bounded failures and downstream limits

The collector bounds response bytes while streaming, JSON nesting/size, numeric
text, exact response cardinality and instrument identity, independent request and
batch deadlines, and cleanup time. It does not retry, fall back to receipt time,
silently coerce a missing field, or return a quote on cleanup failure. Caller
cancellation must propagate even if cleanup also fails. A transport that cannot
close within the cleanup grace is not claimed to have released every resource;
there is no successful capture or execution authority in that case.

`validate_collected_quote` reconstructs observations from the original bytes,
checks the exact quote and all hashes, and bounds mutable-copy inputs before
serialization. The immutable result always has `execution_authority=false` and
`source_authenticity_verified=false`. It does not authenticate an injected mock
transport, detect cross-venue/WS conflicts, fetch candles/account risk, consume
an event, reserve capital, pass G12, or authorize an order.

The synthetic boundary test in
`tests/unit/test_qualification_collected_quote_chain.py` publishes an unchanged
packet, starts new mock HTTP requests afterward and passes that quote to the
existing location evaluator. Both long and short reject a price that left the
original zone; an inside-zone result still grants no order authority. This is
only part of the future recheck: original-event continuation, intervening OHLC
coverage, worst-executable economics, fresh portfolio/guards and the durable
submit boundary remain required. Synthetic tests are not shadow/Demo samples.
