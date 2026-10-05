# Owned initial and post-G12 public observations

`qualification_runtime.capture_initial_public_market` is the unmounted initial
public-only entry. It accepts an existing empty absolute journal directory, an
exact instrument in the reviewed Demo public universe, and an explicit bounded
`PublicMarketCollectionPolicy`. It accepts no market DTO, candidate, clock,
transport, report ID, receipt or caller-created scope. It reads no credentials.

Each invocation generates its own report identity and native UTC/monotonic start,
copies the collection policy, and creates a private one-use initial scope. The
scope's policy pin and task/process/thread/loop identity are checked before IO.
The initial journal plan is `ctcc.public.initial_runtime_plan.v1`, with
`stage=initial_public` and `invocation_started`. It has no candidate/event,
qualification-policy, G12 receipt or publication-barrier claim. Every collector
packet/component retains `barrier_completed_at=null` in this stage. The initial
expiry is bounded by the copied collection policy's total timeout (at most 60
seconds), including clock health and handoff; there is no expiry extension.

Initial acquisition and P1 share the same native clock/transport, raw retention,
no-clobber storage, complete readback, raw joins and typed semantic replay. Their
issuers and returned private capture types are distinct. Neither stage's scope or
capture is accepted by the other stage. The initial handoff is consumed once in
the same invocation, then `public_market_snapshot` builds a fresh market copy from
the readback packet. Offline replay cannot recreate that private handoff.

The diagnostic result has `admission=DENY`. A captured packet and market mean the
initial public capture and bridge finished, not that a trade is qualified.
`original_source_verified`, `candidate_created`, `g12_published`,
`metadata_complete`, `account_complete`, `atomic_risk_reserved`,
`execution_authority` and `order_submitted` remain false. Returned DTOs/hashes are
audit data only. This slice deliberately has no entry selection policy, strategy
selection, account/metadata producer, ledger access, D0 readiness or order route.
Those prerequisites must later be connected inside the owned invocation, not
accepted from the diagnostic result. Missing prerequisites cannot manufacture G12.

The native entry can be called after the operator's host-clock validation, using
an explicit reviewed capture policy, for example:

```python
import asyncio
from pathlib import Path

from app.trade_qualification.candle_collector import (
    CandleCollectionPolicy,
    FrameRequest,
    TIMEFRAMES,
)
from app.trade_qualification.market_aux_collector import MarketAuxCollectionPolicy
from app.trade_qualification.public_market_collector import PublicMarketCollectionPolicy
from app.trade_qualification.qualification_runtime import capture_initial_public_market
from app.trade_qualification.quote_collector import QuoteCollectionPolicy
from app.trade_qualification.ws_collector import WSCollectionPolicy

policy = PublicMarketCollectionPolicy(
    quote=QuoteCollectionPolicy(max_age_seconds=5),
    candles=CandleCollectionPolicy(
        requests=tuple(
            FrameRequest(timeframe=frame, requested_confirmed_bars=240)
            for frame in TIMEFRAMES
        )
    ),
    market_aux=MarketAuxCollectionPolicy(max_age_seconds=5),
    ws=WSCollectionPolicy(max_age_seconds=5),
)
# Replace this with a newly created, controlled, empty absolute directory.
result = asyncio.run(
    capture_initial_public_market(
        Path("C:/CTCC-Evidence/initial-attempt-unique"),
        instrument_id="BTC-USDT-SWAP",
        market_policy=policy,
    )
)
print(result.code, result.admission, result.journal_sha256)
```

This is an explicit network capture, never an import-time action or a retry loop.
The example's row counts and freshness bounds are collection settings, not a new
qualification or entry policy. Failure retains the attempted directory; never
erase its tail to repeat the attempt. Real clock/TLS/WS/storage acceptance requires
actual native evidence; synthetic tests are not that evidence.

## Post-G12 invocation

`publish_capture_public_recheck` is an unmounted diagnostic coordinator. It calls
the real G12 publisher, requires a newly written receipt and its readback, then
creates an opaque scope for that invocation. A loaded receipt, copied object,
caller timestamp or `passed=true` cannot create this scope. The original replay
inputs remain explicitly unauthenticated.

The coordinator uses this sequence:

1. Replay the original candidate and publish/read back G12 evidence.
2. Record a native UTC/monotonic publication barrier and unchanged expiry.
3. Retain native clock health before acquisition.
4. Acquire fresh ticker/mark/funding, four candle timeframes, books5/OI and WS
   ticker through owned connections with internally sampled receipt times.
5. Retain native clock health after acquisition and replay every component.
6. Publish the complete observation journal without replacement, read it back,
   recompute its raw joins and typed packet, then perform the pure current recheck.

The current fixed origins are `https://www.okx.com` and
`wss://ws.okx.com:443/ws/v5/public`. They are public data origins; their use does
not establish account-region compatibility or authenticate a Demo account.
New socket captures use port 443. The exact former `:8443` origin is accepted only
when replaying an already sealed historical receipt; it is never selected for a
new capture. The [OKX API changelog](https://www.okx.com/docs-v5/log_en/) says
port 8443 stops accepting WebSocket connections on 2026-10-31 and explicitly
allows the same URLs on port 443.
No credential, account request, Arm, reservation, intent or order endpoint is
available through this coordinator. Its result has no execution authority.

## Owned transport and clock

The producer creates three isolated HTTPX clients and one WS connection. HTTP
clients disable environment proxies, redirects, retries, auth and hooks. Native
SSL context, HTTP pool and negotiated SSL object identities are checked. The
observed TLS hostname, negotiated version and peer certificate hash are retained.
Only the pinned public GET routes and exact instrument queries are admitted.
For candles and books, the query binding is essential because the response does
not independently echo the requested instrument.

The WS connection disables proxy use and redirects. Its verified TLS object is
bound to the owned socket before subscription. Exact subscription, ack and ticker
application bytes and observed receipt times are retained. These are UTF-8 WS
application messages, not encrypted traffic, control frames or wire fragments.

All request/connection starts must occur strictly after the publication barrier.
Each valid sample retains native UTC nanoseconds and monotonic nanoseconds. Native
clock validation thresholds and the older minute-source formats are unchanged.
Exchange timestamps keep their endpoint semantics: candle open time is historical;
next funding time is scheduled. Neither is substituted for a measured receipt.

## Durable diagnostic journal

The separate `ctcc.public.runtime_* .v1` formats contain a pinned plan, chained
events, raw sidecars and a terminal summary. They cannot be relabelled as an older
minute-source receipt. Storage uses the existing native no-clobber publication and
readback primitives. Limits are 64 HTTP requests, 4096 events, 32 MiB per raw
sidecar and 64 MiB for the whole journal, with smaller component limits retained.

Received headers/chunks/WS messages are recorded before their parser advances.
If the next clock observation fails, their receipt time is explicitly absent;
actual returned bytes are retained rather than stamped with another time. An
oversized body/message fails, retaining the bounded prefix, actual observed size
and digest, and an explicit truncation flag. That prefix is never complete data.
Headers are restricted to bounded public content/date fields. Private credentials
and private account data are not inputs to this path.

A late publication error never deletes already durable raw files. It can leave
an incomplete tail without an accepted summary. Cancellation joins every owned
resource and cannot issue a capture even if a transport suppresses cancellation.
Missing readback, cleanup uncertainty, wrong TLS binding, clock failure or an
expired scope denies the result. There is no retry into the same or another root.

The low-level journal replay checks event inventory, predecessor hashes, clock
ordering, response status/headers, exact assembled raw body, page queries, packet
joins and WS sequence. The qualification-layer `replay_public_runtime` additionally
re-runs the existing typed component parsers, bundle digest and original collection
policy pin. Recomputing storage hashes cannot replace that semantic replay.
Successful runtime handoff uses these readback bytes, not a previously retained
in-memory packet. Serializable replay data never creates an opaque source carrier.

## Scope of acceptance

Synthetic contracts demonstrate source ownership boundaries, packet integrity,
failure retention and deterministic replay. They do not demonstrate native Windows
storage, healthy host time or a real OKX TLS/WS capture; those executions require
separate actual evidence. The old collector APIs and wire records are unchanged.

This producer does not authenticate the original candidate's source, complete the
account/portfolio producer, prove policy-required event survival that was never
observed, or grant `READY`. The final execution coordinator must combine those
requirements with fixed geometry, current economics/risk, durable controls,
reservation and intent. No future-price guarantee or universal continuous-tick
claim is made by this acquisition path.

The dated native v2 public-only captures and independent offline replay are
recorded in [the v2 runtime evidence](public_source_runtime_v2.md) and
[the final validation ledger](final_completion_validation.md). They supersede
the earlier statement that no actual public capture had occurred; neither
capture grants original-candidate, account, recheck, reservation or execution
authority.
