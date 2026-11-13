# Native Demo SWAP fee capture V1

`record_native_demo_swap_fee_v1` is an additive read-only capture of one
account-specific SWAP fee response. It accepts a still-active native V4 current
account owner and an instrument ID. The original V7 account-instruments row
provides the exact `instFamily` and `groupId`; the code sends exactly one signed
`GET /api/v5/account/trade-fee?instFamily=...&instType=SWAP` through the same
pinned Demo credential session. The pre-existing V1 parser selects only the
matching `feeGroup` and rejects a missing, duplicate or malformed group. The
[reviewed OKX fee guide](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-fee-rates)
requires `instType`, allows `instFamily` or `groupId` but not both, and warns
that zero-fee exceptions may not appear in the returned rate. Positive values
are rebates and negative values are commission. Deprecated flat rate fields
are retained in raw bytes but never selected.

The request inherits the existing isolated signed-GET transport: Demo header,
no proxy, redirect, retry, cookie or alternate host, bounded original bytes,
causal timestamps and native TLS peer evidence. The signed-GET byte ceiling
must match the reviewed fee ceiling; a future mismatch denies before sending.
The V4 current-owner lifetime and the fee parser's five-second request and
30-second post-account limits can
only shorten the attempt. One current owner permits at most one fee attempt,
including a failed attempt. The isolated HTTP client closes
before custody publication. Raw fee bytes, the observed status/media/redirect
envelope, observation clocks, current-source
hash, TLS certificate hash when available and the V1 mapping receipt are sealed
in DB0046, committed, and separately read back. A late failure retains already
committed evidence and denies this call; it never retries the GET or DB write.

The fee data-return `ts` is not trade matching time, account accrual time or
historical availability. The source packet and DB seal remain portable after
return, so `source_authenticity_verified`, `fee_cost_authority`, account
completeness and execution authority are always false. The collector is not
wired into G11/R7 execution or the trade cost formula. A verified account
registration site, actual native/provider GET, approved fee-cost policy,
zero-fee exception handling, source integration and matching full regression
are still required before any rate can affect qualification.

The focused tests use only synthetic MockTransport and in-memory custody. They
exercise the exact GET/header/query, same-session owner, original raw bytes,
custody readback, wrong-group and closed-lease denial, late expiry retention,
and non-authority flags. They do
not constitute OKX, native TLS, PostgreSQL or Demo acceptance.
