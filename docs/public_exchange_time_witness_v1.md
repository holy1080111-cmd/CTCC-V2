# Public exchange-time witness V1 (offline diagnostic)

`app/trade_qualification/public_exchange_time_witness_v1.py` replays one supplied
OKX `GET /api/v5/public/time` response. It retains the exact response bytes,
canonical JSON bytes, both SHA256 hashes, the system-time `ts`, and caller-supplied
request start, header receipt, body completion, and validation stamps. Each
stamp has UTC nanoseconds and monotonic nanoseconds. The shared clock validator
rejects reversed clocks or a wall/monotonic drift over 5 ms; this witness also
rejects a total observation over 5 seconds. Server `ts` must fall between
request start and body completion with no added future tolerance.

The route claim is pinned to a reviewed Demo REST origin for the declared
registration region. Exact public-only headers, response URL, status 200, zero
redirects, TLS hostname/version and peer digest are checked as **claims supplied
by the caller**. `GET`, `/api/v5/public/time`, and the empty query are fixed
diagnostic labels in this receipt; no actual request is observed. A matching
digest does not
prove that a request occurred, that the TLS peer was OKX, that the account uses
that region, or that the stamp came from the native host clock. The receipt has
`transport_claim_scope=caller_supplied_unverified`; authenticity, native capture
binding, point-in-time eligibility and execution authority remain false, and
admission is always `DENY`. This module performs no network calls, writes or
trading actions and is not wired to production source admission.

To use this as eventual source evidence, a separately reviewed native owner
must make bounded, no-proxy, no-redirect time requests on the account-verified
region route before and after the public market capture, retain actual TLS and
raw response evidence, and bind both observations to that capture's invocation
and monotonic clock domain. That owner must still enforce each component's own
timestamp semantics. OKX candle `ts` is the bar opening time, REST/WS ticker
and book `ts` are generation times, mark/open-interest/funding `ts` are exchange
return times, and funding `fundingTime`/`nextFundingTime` are settlement times.
Consequently a blanket `request_start <= source_ts` check would reject valid
cached and historical data; it must not replace the existing fixed freshness
and post-publication generation checks.

Sources: [OKX API V5 system time](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-system-time),
[candlesticks](https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks),
and [funding rate](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate).
