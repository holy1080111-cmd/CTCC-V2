# OKX account API source review, 2026-09-30

This documentation review grants no source ownership or account completeness.
Actual raw responses, pinned region/session and complete page chains remain
required. It is not a byte-retained native documentation capture.

The [OKX API guide](https://www.okx.com/docs-v5/en/) currently documents
`account/config`, `account/account-position-risk`, `account/balance`,
`account/positions`, `trade/orders-pending`, `trade/orders-algo-pending`,
`trade/fills`, `trade/fills-history`, `account/bills`, `account/bills-archive`,
`trade/orders-history`, `trade/orders-history-archive` and
`account/leverage-info` under `/api/v5/`.

Private fills use `billId` pagination; ordinary orders use `ordId`; algo orders
use `algoId`. `after` selects older records, `before` newer records, excluding
the cursor. `fillTime` is execution time; fills `ts` describes record generation.
Order-history time filters use `cTime`. `uid` identifies the requested account;
`mainUid` describes its parent relationship. Demo requests require
`x-simulated-trading: 1`.

The guide advertises `openapi.okx.com` for global REST. OKX's own
[site compatibility record](https://github.com/okx/agent-trade-kit/blob/github-main/docs/site-compatibility.md)
also lists `www.okx.com` for global, `eea.okx.com`, `us.okx.com` and `tr.okx.com`.
It reports an EEA leverage-info limitation and untested TR compatibility.
Those published observations do not establish this operator's region or present
account support. No hostname fallback, redirect or source substitution is
authorized by this review. Existing versioned host profiles remain separate;
the actual account region/session must be verified before authenticated use.

The reviewed source cursor map in `account_capture.py` agrees with the endpoint
identities above. Historical window completeness, an empty-page terminator,
cross-page ordering, conflicts, identity and causal timestamp validation are
additional CTCC requirements. A short first page does not satisfy them.

## 2026-10-01 retained global documentation research

The global guide and [official changelog](https://www.okx.com/docs-v5/log_en/)
were fetched directly, without proxy discovery, redirects, retries or regional
fallback. Their original response bytes and receipt timings are retained in
`validation-results/okx-global-official-docs-research-20261001` outside the
release source. Guide SHA256: `9c990c466ed4ada889b63c0dac349f9cf7cd6bb5c5bf099f43d982779fa9529f`;
changelog SHA256: `dbd71e40a5a3aba256a18448ee1529f40a563318172f8856c163a1f39ca56796`.
This is documentation evidence, not native account or historical availability.

The guide defines `/api/v5/public/funding-rate-history` with a three-month
retention, a maximum/default limit of 400 and `fundingTime` cursors: `after`
selects older events, `before` newer events. `fundingTime` is the settlement
time and `realizedRate` the actual rate. This requires a separate versioned
raw event source; current forecast fields cannot supply past accrual times.

For account bills, `ts` records completed balance update; `fillTime` records
the fill. Funding subtypes 173/174 use signed `pnl`. These account subtypes
must not be interpreted using asset-bill enums or as fill/accrual timestamps.

Fee rates require instrument/group matching. `groupId` cannot be combined with
`instId` or `instFamily` in one request. For market-maker incentive accounts,
the instrument/family query returns applicable rates; an unqualified query may
return organic base rates. Legacy `makerU/takerU` fields are deprecated. A
fixed synthetic numeric fee policy remains unverified for production costs.
## 2026-10-05 regional REST endpoint update

The [official OKX TR API guide](https://tr.okx.com/docs-v5/en/) identifies
`https://tr.okx.com` as the Turkey REST API origin. The regional Demo account
plan permits `tr` only when the exact origin and registration-evidence hash are
supplied together. Region is not inferred from locale, network location, or
credential presence. Demo requests continue to require
`x-simulated-trading: 1`.
This is endpoint documentation and route validation only. The trusted bootstrap,
current-source and native account proof layers still require global region and
deny TR until their region-bound provenance path is implemented and validated.

## 2026-10-05 current pagination recheck

The current [official OKX V5 API guide](https://www.okx.com/docs-v5/en/) and
[pagination guide](https://www.okx.com/docs-v5/trick_en/) were rechecked against
the collector cursor map. Recent and historical fill pages use `billId`, order
history pages use `ordId`, and pending algo-order pages use `algoId`. OKX
defines `after` as older than the supplied ID and `before` as newer; the cursor
is excluded and results remain newest-first. The source plan therefore keeps
each endpoint's cursor type and page chain separate and requires an explicit
terminal page instead of inferring completeness from a short response.

The guide also distinguishes fill execution time (`fillTime`) from record
generation time (`ts`); order-history filters use `cTime`. These fields are
preserved independently in account evidence and are not substituted for one
another. This documentation recheck made no authenticated account request and
does not establish account completeness or source authenticity.

## 2026-10-05 since-2021 bills archive recheck

The current [official OKX API guide](https://www.okx.com/docs-v5/en/) documents
`POST /api/v5/account/bills-history-archive` to request one year/quarter and
`GET /api/v5/account/bills-history-archive` to read its generation state. The
archive starts on 2021-02-01, excludes the current quarter, and is available only
for unified accounts. A `result` of `false` means generation is in progress; the
guide says to check again after two hours and notes peak periods can take longer.
The returned `fileHref` expires after 5.5 hours, and the same quarter need not be
requested again within 30 days. The guide describes the CSV in reverse
chronological `billId` order and defines `ts` as the time the balance update was
completed. It is not fill execution time or a funding accrual timestamp.

The parser and its 13 unit cases were rerun on this source revision. This is
offline parser evidence only. The guide's example does not establish a production
download hostname or scheme for `fileHref`; the source currently discards the
URL after hashing it and has no generic-host downloader. Archive acquisition,
quarter coverage, authenticated account identity, and account completeness
therefore remain unverified. The documented post-2024-10-11 calendar-quarter
rule is also not evidence that older generated files share those exact interval
semantics. None of these archive rows can currently enter a complete
PortfolioRiskSnapshot or a trading decision.
