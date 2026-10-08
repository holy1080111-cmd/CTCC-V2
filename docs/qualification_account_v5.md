# Versioned standard-product account capture v5

The current `AllProductDemoAccountCapturePlan` requires
`ctcc.demo_account_plan.v5`; its packet schema is
`ctcc.demo_account_capture.v5`. It binds a new plan digest and the 34-stream
inventory. The v2/v3 packet contracts and v4 packet bytes remain replayable as
historical evidence. Only a v5 plan can use the normal owned private HTTPS
transport. Older plan contracts are confined to explicit `MockTransport`
synthetic regression and are rejected before any real private request. The v4
plan is a historical replay-only type and cannot be relabeled as v5.

## Current query inventory

The six product families remain `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`,
and `EVENTS`, with separate required chains for fills history, recent order
history, and archived order history. Current balances, positions, ordinary
pending orders, recent fills, bills, and algo pending queries are unfiltered.
Instrument and leverage metadata remain explicitly scoped as documented; that
scope is not a claim of complete account metadata.

| Endpoint | Query scope | Cursor |
| --- | --- | --- |
| `/trade/orders-pending` | No instrument, state, or order-type filter | `ordId` |
| `/trade/orders-algo-pending` | One request per documented `ordType`: `conditional`, `oco`, `trigger`, `move_order_stop` | `algoId` |
| `/trade/fills` | All returned product types, no instrument filter | `billId` |
| `/trade/fills-history` | Six independent `instType` chains | `billId`; time filtering remains on source `ts`, not fill time |
| `/trade/orders-history` | Six independent `instType` chains | `ordId`; `cTime` is not fill time or realized PnL |
| `/trade/orders-history-archive` | Six independent `instType` chains | `ordId`; `cTime` is not fill time or realized PnL |
| `/account/bills`, `/account/bills-archive` | Unfiltered | `billId`; bill generation time is not funding accrual time |

The current OKX Algo order list contract documents exactly those four `ordType`
values and `algoId` pagination. `chase` is documented as an `advanceOrdType`
value on an algo order, not a separate pending-list `ordType`; `iceberg`,
`twap`, and `smart_iceberg` are not accepted as request types for this endpoint.
The all-incomplete ordinary order query stays unfiltered so it does not narrow
coverage to a requested order subtype. Raw unknown order fields remain source
data and are never synthesized into a separate query.

OKX pagination is exclusive: `after` returns older records than its ID and
`before` newer records; IDs are endpoint-specific, and page size is capped at
100. The collector preserves the exact cursor family, page index, previous-page
hash, request, first/last identity, and explicit empty terminal. Repeated,
non-advancing, conflicting, missing, or over-limit chains fail closed.

The v4 `ctcc.demo_account_plan.v4` contract previously attempted eight algo
types, four beyond the current endpoint's documented request enum. V4 plans are
therefore accepted only for byte-pinned historical replay; the owned HTTP
collector refuses them. The v5 plan has a distinct marker and digest so the old
receipt cannot gain the corrected request inventory by relabeling.

This correction establishes only endpoint/query identity. A verified packet
still has `account_complete=false`, `source_authenticity_verified=false`, and
`execution_authority=false`. It does not prove authenticated account identity,
complete pagination in a live account, whole-account revision consistency,
full risk materialization, or Demo/Live readiness. At this version's initial
implementation, no account request or order was sent; the tests used synthetic
data only.

On 2026-10-08, a separate diagnostic at exact source
`b72c21025a0f9bcce8cfb0c7109a32b4d14af45f` used locally stored Demo
credentials for signed, read-only capture of a declared seven-day window.
All 34 V5 streams were captured and replayed in one invocation with TLS peer
evidence. Packet SHA256:
`f9aec48bcb285024d84faf49210a255f1cbd867b7724d60f5542fd74f8cd2bb6`.
The redacted diagnostic is
`../validation-results/demo-v5-history-diagnostic-b72c210-20261008.json`
(SHA256 `5a0ffcd1ff50ba80c6499179c8220d93ca9bc839af02182f4a618f0c5f802112`).
It did not retain private raw bytes, prove the account's registration region,
prove older retention beyond the declared window, or bind the separate V7
current capture under one DB lock. It granted no account completeness or
execution authority, and no order was sent.

Primary documentation: [OKX Algo order list](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list), [OKX ordinary order list](https://app.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list), and [OKX pagination semantics](https://www.okx.com/docs-v5/trick_en/#pagination).


## 2026-10-05 official routing and pagination recheck

The official API changelog dated 2026-05-20 says `openapi.okx.com` is the
recommended REST domain for Global users, `www.okx.com` remains usable, and
regional `us.okx.com`, `eea.okx.com`, and `tr.okx.com` remain unchanged. The API
overview directs US/AU users registered on `app.okx.com` to `us.okx.com`, and
EU users registered on `my.okx.com` to `eea.okx.com`. The v5 regional plan
therefore pins Global→`openapi`, US/AU→`us`, EEA→`eea`, and Turkey→`tr`; it
does not infer registration from local settings, redirects, or a successful
response. The externally supplied registration evidence hash is still only a
pin until authenticated. [OKX API changelog](https://www.okx.com/docs-v5/log_en/#2026-05-20) ·
[regional API overview](https://www.okx.com/docs-v5/en/#overview).

The source mapping was checked against the current endpoint contracts: ordinary
orders use `ordId`; fills and account bills use `billId`; each of the four
documented pending algo types uses `algoId`. Those identifier families remain
separate. OKX specifies a maximum page size of 100 and exclusive `after` /
`before` cursors; the collector binds each endpoint's own cursor and requires
an explicit empty terminal page. A short non-empty page is not completion. Fill
`tradeId` and `fillTime` are preserved as distinct source fields; `ts` remains
system record-generation time and is not substituted for trade time. [OKX
pagination guide](https://www.okx.com/docs-v5/trick_en/#pagination) ·
[algo order list](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-algo-order-list) ·
[fills history](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months).

Regional packet acquisition and final admission are not equivalent. The current
source verifier, native account proof, bootstrap collector, and portfolio
component policy still accept only the Global region. The Live REST client and
settings allow only Global `openapi` and EEA `eea`; US/AU and Turkey Live routing
is not implemented. No account region has been authenticated for this machine,
so no regional account request is authorized.

The official API also exposes a separate since-2021 bills archive generation
flow, while the current 34-stream v5 account plan contains only the recent and
three-month bills endpoints. The current history-query policy is bounded to 28
days. Thus the verified query plan is not a since-inception cashflow or loss-seed
source; missing lifecycle, funding-accrual, streak, or high-water history stays
unknown and prevents a complete portfolio-risk snapshot. [OKX since-2021 bills
archive](https://www.okx.com/docs-v5/en/).

## 2026-10-05 since-2021 bill archive protocol gap

The official since-2021 endpoint is a quarterly archive workflow, not another
paginated `billId` GET chain. A Read-permission request to
`POST /api/v5/account/bills-history-archive` requests one year/quarter (and
optional bill `type`); the current quarter is excluded, and the API says the
workflow is for unified-account data. A corresponding GET reports `state`, `fileHref`, and
request-receipt `ts`. The documented states are `finished`, `ongoing`, and
`failed`; the download link expires after 5.5 hours, and a quarter does not need
to be reapplied within 30 days. The documented limits differ by stage: POST is
1 request per 10 seconds, while GET is 10 requests per 2 seconds. [OKX current
API reference](https://www.okx.com/docs-v5/en/) · [2026-03-24 API changelog](https://www.okx.com/docs-v5/log_en/#2026-03-24).

The completed export is a downloadable archive containing a decompressed CSV
schema, so it requires its own raw-download capture, archive integrity check,
CSV schema/row identity validation, quarter coverage manifest, and receipt
chain. The apply response `ts` is server receipt time; CSV row `ts` is a balance
update time. Neither is funding accrual time. CSV `billId` is the record
identity, but does not replace fill IDs, fill times, or position-level realized
outcomes. The current v5 collector has none of these acquisition or readback
stages. This is a confirmed engineering gap; no archive is treated as complete,
no account credentials were used, and the risk snapshot remains incomplete.

The official response semantics add two fail-closed conditions: `result=false`
means generation is still in progress, with the first status check documented
after two hours; if a link is still unavailable after three hours, OKX directs
the user to support. It is not an empty quarter. The generated-file description
says `billId` is descending and gives exact half-open quarter coverage for files
generated after 2024-10-11, but its Q2 example labels `[2024-07-01,
2024-10-01)`, which is Q3 by calendar convention. Until source behavior is
resolved, a downloader must bind each requested quarter to explicit expected
UTC bounds, validate the returned rows against those bounds, and reject the
contradictory example rather than infer a mapping from it. The expiring
`fileHref` is sensitive transport material: it must stay in process memory,
never in logs, durable evidence, or release bundles; persist only its digest and
the request/status/download receipt chain. None of this establishes that the
endpoint works for Demo credentials; environment support must be verified
before an authenticated request is considered.

## 2026-10-05 offline archive validator

`app/trade_qualification/account_bill_archive.py` now validates supplied apply
and status response bytes, hashes a temporary `fileHref` without returning it,
and checks a supplied one-CSV ZIP for integrity, schema, unique descending
`billId`, currency, exact balance-change text, and the pinned calendar-quarter
time window. Unknown CSV columns remain in each row. The Q2 documentation
conflict is exposed as a mismatch (for example, July rows are rejected for a
Q2 request). This parser always returns `quarter_coverage_verified=false`,
`account_complete=false`, and `execution_authority=false`.

This is an offline parser only. The owned account collector still has no
quarterly POST/apply, status polling, download transport, durable raw archive
receipt, or reconciliation into lifecycle history. The endpoint's unified
account and Demo applicability still require authenticated source evidence;
the parser does not verify either. No account or order request was sent.

## Funding bill source-field consistency V2

The additive `ctcc.demo_account_funding_bill_consistency.v2` diagnostic replays
the exact original V5 B1 raw bill pages and the existing V1 funding candidate
audit. For each source-backed account funding candidate, it records the original
row and page locators plus whether numeric `pnl` equals the account-level
`balChg`, and whether `fee` is observed as zero. Malformed or missing values
remain `null`; a mismatch adds an explicit blocker. It does not alter the
sealed V1 receipt or silently count overlapping recent/archive rows twice.

Agreement between these fields is only a bounded observation of one bill,
not proof of funding settlement time, account-wide cashflow completeness, or
fee attribution. An empty candidate set does not prove zero funding. The V2
receipt preserves `funding_accrual_at=null`, `net_funding_cashflow=null`,
`account_complete=false`, and `execution_authority=false`; no credential or
order route is involved. OKX documents `pnl` as the funding payment field for
account bill subtype 173/174, `balChg` as account balance change, and `ts` as
balance update time in its [account bills reference](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-3-months).
