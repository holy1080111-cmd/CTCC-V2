# Versioned standard-product account capture

`AllProductDemoAccountCapturePlan` requires the explicit marker
`ctcc.demo_account_plan.v4` and a new plan SHA256. The new packet schema is
`ctcc.demo_account_capture.v4`. Historical v2/v3 plan, observation and frozen
packet bytes remain unchanged, verified against checkpoint `ecda5214`.

Official OKX endpoint contracts were reviewed on 2026-09-19. v4 requests 38 fixed
streams, keeps every raw response and page receipt, and rejects missing queries,
non-advancing cursors, unexpected product types, repeated/conflicting IDs,
incomplete pages, source clocks or resource-budget failures.

| Source | Query scope | Cursor / time filter |
| --- | --- | --- |
| `/trade/fills` | No instrument filter; all returned standard products | `billId`; `begin/end` filter `ts`, distinct from `fillTime` |
| `/trade/fills-history` | Six independent required `instType` chains | `billId`; `ts` |
| `/trade/orders-history` | Six independent required `instType` chains | `ordId`; `cTime`, never realized-PnL or fill-time evidence |
| `/trade/orders-history-archive` | Six independent required `instType` chains | `ordId`; `cTime` |
| `/account/bills`, `/account/bills-archive` | Unfiltered | `billId`; `ts` cash movement, not funding accrual |
| `/trade/orders-pending` | No instrument/state filter | `ordId`; explicit empty terminal |
| `/trade/orders-algo-pending` | Each of eight documented `ordType` values, no instrument filter | `algoId`; explicit empty terminal |
| Config, balance, positions, account-position-risk | Unfiltered, exact UID/mainUID/config before/after | Raw source semantics retained |
| Account instruments, leverage info | Existing SWAP instrument metadata and pinned leverage instruments | Scope remains incomplete for other products |

The six required types are `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`, `EVENTS`.
The eight algo types remain conditional, oco, trigger, move_order_stop, iceberg,
twap, chase and smart_iceberg. Chase applies only to FUTURES/SWAP. A required
endpoint's rejection in a region or Demo environment aborts acquisition; it is
never replaced by an empty set. No automatic retry, redirect, proxy or source
substitution is introduced. Registration origin remains explicitly pinned:
global `openapi.okx.com`, US/AU `us.okx.com`, EEA `eea.okx.com`, Turkey
`tr.okx.com`; locale does not select it. Demo keeps `x-simulated-trading: 1`;
this collector cannot acquire Live.

Each product chain must end with an explicit empty response even when its previous
page had fewer than 100 rows. Each page retains its exact request, page index,
previous page hash, first/last row identities and cursor. Cross-product reuse of
an ID within one history endpoint family is rejected. Overlapping recent/archive
history still uses the existing exact-row conflict check; raw receipts are never
sorted, deduplicated or repaired to hide malformed source.

The v4 parser retains documented empty `posSide` values for non-derivative order
records without inventing a position side. Positions still require a documented
side. SPOT market-order `sz` can use quote currency while `accFillSz` uses base
currency; those values are not compared as identical units. Missing `tgtCcy` is
recorded as unknown. The materializer retains unsupported non-SWAP current
exposures with unknown risk, margin and contract quantity; it cannot mislabel a
base/quote quantity as contracts or omit a non-SWAP history row.

The recent/archive fill parser also preserves documented exceptional IDs, reviewed
against the official transaction references on 2026-09-23. Negative `tradeId` is
accepted only with liquidation subtypes 100–107 or ADL subtypes 125–128, using the
existing bounded decimal magnitude. An explicitly empty `ordId` is accepted only
for block subtypes 204–209. Missing IDs remain missing and rejected; no replacement
ID or undocumented mandatory block ID is invented. Transfer subtypes 110/111 do
not establish the documented negative-ID category and remain rejected when paired
with a negative trade ID. Unknown or contradictory exception context fails closed.

This applies to the existing v2/v3/v4 parsers without changing their wire format.
Ordinary frozen packets remain byte-identical. Fill identity and pagination remain
positive `billId`; neither exceptional field becomes a cursor. Raw strings remain
in the canonical row. Acceptance of a source row does not prove attribution to a
local order: block fills with no order ID require independent lineage evidence,
and account completeness and execution authority remain false.

This closes the missing standard-product history-query gap only. Empty terminals
do not prove API retention, ingestion watermarks, continuous peaks, earlier loss
streak seeds, funding accrual or account-wide product coverage. Recent fills have
a three-day window; recent bills/orders seven days; archives three months. Certain
unfilled canceled orders remain available for only two hours. Requested time
bounds cannot extend those retention limits.

Metadata still requires source-backed family/series catalogs: OPTION instrument
queries require `instFamily`, EVENTS require `seriesId`. Advanced products,
liabilities, historical metadata and currency/economic mapping remain separate
work. Non-SWAP outcomes currently produce `history_product_mapping_unsupported`.
The bootstrap/source-authenticity and whole-account consistency verifiers in the
[runtime gap table](controlled_demo_account_runtime.md) remain required.
Runtime admission stays `DENY`; all account-complete/submit-authority flags remain
false. No actual account API or order call was used in this validation.

Primary references: [recent fills](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-days),
[fills history](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months),
[recent orders](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-history-last-7-days),
[order archive](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-history-last-3-months),
[pending orders](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list),
[pending algos](https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-list),
[registration routing](https://www.okx.com/docs-v5/en/#overview),
[pagination](https://www.okx.com/docs-v5/trick_en/#pagination).
