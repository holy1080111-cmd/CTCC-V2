# Funding observation v2: source semantics only

`app.trade_qualification.funding_observation_v2` adds a pure parser for one exact
SWAP funding row from retained REST bytes or a funding-rate WS message. It does
not perform acquisition, authenticate caller timestamps, approve freshness,
recompute gates, grant account completeness or issue execution authority.
Every parsed/replayed result has `admission=DENY`. Existing v1 collectors,
parsers, quote models, journals and wire formats are unchanged.

## Exact source meanings

The forecast is an immutable `FundingForecastV2(rate, settlement_at)` pair:
`fundingRate` belongs to `fundingTime`. `nextFundingTime` is retained separately
as the following forecast settlement. Raw `ts` is the exchange data-return
time. Rate-generation and historical first-availability times remain unknown.
Supplied acquisition/receipt/completion times are retained as unverified input;
only a future owned native runtime can establish measured availability.

`settFundingRate` is separate from the forecast. Its interpretation follows
`settState`: current processing settlement or previous settled period. A
processing record can be decoded for evidence while remaining DENY. Missing
required rates, limits or time fields are rejected, never filled with zero.
The current parser supports the documented current-period mechanism and
recognized old/new formula names; it rejects an unknown mechanism, formula,
state or populated deprecated next-period rate. FUTURES, ANY, multiple rows,
wrong WS channels and acknowledgements are outside this exact SWAP scope.

These semantics follow the official [REST funding reference](https://my.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate)
and [funding WS reference](https://app.okx.com/docs-v5/en/#public-data-websocket-funding-rate-channel),
checked 2026-09-28. The WS section documents 30–90 second funding pushes; it does
not establish a REST cache SLA. This parser does not adopt any age threshold.

Raw bytes and their SHA256 remain available alongside canonical JSON and its
SHA256. Duplicate JSON keys, nonfinite values, malformed UTF-8, oversized bodies,
future exchange-return timestamps and reversed receipt order fail closed.
Additional source fields are retained in the full raw/canonical payload, not
silently mapped into trusted meanings. Replay reconstructs exact typed fields
from bytes, including the immutable rate/time pair and all false authority flags.
An internally consistent caller payload still provides no source authenticity.

## Explicit future integration boundary

The v1 `MarketSnapshot.next_funding_time` currently retains raw
`nextFundingTime`; v2 must not use it as the nearest settlement. A distinct v2
bridge should carry the typed observation and both named settlement times.
No v1 stored report or replay is relabeled by this additive module.

The existing `location.inspect_executable_quote` applies one age to ticker,
mark, funding and receipt. Its direct consumers include qualification `data`,
`fixed_protection`, `economics`, `location`, and `trade_evidence.service`.
Qualification ledger/intent formats also bind existing quote timestamps.
Consequently changing only the collector or passing a larger common age would
not implement safe per-component freshness.

The necessary next profile is explicit and additive: a versioned funding
acquisition/observation contract, a v2 quote carrier, and a matching inspector
with separate ticker/mark/receipt and funding age rules. Each adopting consumer
must replay the same pinned profile; old consumers continue to reject incompatible
inputs. Caller-provided `passed`, report hashes or parser objects cannot select
the new profile or become an owned capture. Native acquisition must retain the
same invocation scope, post-G12 request barrier, cancellation bytes, causal
clocks, no-retry/no-proxy policy and immutable readback.

Qualification economics presently projects adverse rate across explicit holding
periods with a configured buffer and excludes favorable funding from net-RR
rescue. MIE offline costs use their frozen interval and proration model. Neither
model is a realized settlement proof. Any later change to scheduled-cost
evaluation must be preregistered and versioned; preserve prior reports and their
original assumptions. Account bills and settled funding history remain necessary
for realized forensics, with missing accrual identity still unknown.

## Validation status

Focused tests cover exact REST/WS rate-time pairing, signed and zero rates,
processing versus previous-settled semantics, unknown availability, missing and
malformed data, conflicting times/bounds, duplicate records, timezone conversion,
forced mutation, subclass and nested-type forgery, and authority flags. Fixtures
are synthetic. No native funding capture or production sample is provided by
these tests. Test execution is pending the host's controlled thermal-recovery
test schedule; a lint result does not constitute behavioral acceptance.
