# Settled funding-history raw-page diagnostic V1

`app/trade_qualification/settled_funding_history_source.py` is a pure, bounded
replay of supplied original public funding-history pages. It makes no network,
credential, database, clock, or trading call. The scope is one exact reviewed
Demo route claim and one SWAP instrument. The request/receive UTC and monotonic
times are retained as supplied measurements; this module cannot authenticate
their origin, the account's registration region, or applicability to a Demo
position. Only the existing `global`, `us_au`, and `eea` Demo public route
declarations are accepted; this does not establish any account's region. Every
result is `DENY`, with no account snapshot or funding amount.

The [official OKX funding-history endpoint](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history)
is `GET /api/v5/public/funding-rate-history`. Its `fundingTime` is settlement
time, `realizedRate` is the actual public rate, and `after` requests older
records while `before` requests newer records. The documented history reaches
only the last three months; the [official changelog](https://www.okx.com/docs-v5/log_en/)
raised the maximum and default page limit to 400 on 2025-06-19. OKX also
documents that funding intervals may change, so the replay never synthesizes
events from an assumed eight-hour schedule.

The V1 query contract fixes `instId`, `limit=400`, and then `after` equal to the
previous page's oldest `fundingTime`. It requires descending unique settlement
times, exact cursor exclusion, an explicit final empty page, original response
bytes and SHA256, row hashes and locators, exact reviewed route origin, the
Demo header claim, HTTP 200, and ordered UTC/monotonic request/receive pairs.
Duplicate or conflicting settlement identities, wrong instruments or regions,
future events, malformed responses, nonexclusive cursors, missing termination,
or invalid clocks deny the replay. A short page does not terminate it.
The 16-page, 256 KiB-per-page, and 400-row-per-page caps are engineering
bounds: exceeding them denies rather than truncating a required history.

`source_pages_sha256` binds the supplied bytes and recorded metadata so replay
can reject changed evidence, but a self-consistent hash is not exchange or
clock authenticity. `raw_pages` retains the exact original bytes in memory;
there is no durable collector or native route proof in this slice. The receipt
labels the presented terminal page separately from account completeness and
always leaves `settled_schedule_complete=false`,
`no_applicable_funding_proven=false`, and
`applicable_account_funding_amount=null`. A required start more than 93 days
before the last response is definitely outside any three-calendar-month span
and gets a separate retention blocker. A newer start still does not prove
complete coverage or zero funding.

The next source step must use an owned native public capture with authenticated
Demo registration-region provenance, measured clocks, retained raw pages, and
the same replay contract. A separate reviewed join must compare each settled
event with original held inventory and private account funding bills, including
late or absent payments, before any net outcome can exist. Current
`account_funding_bill_audit.py` identifies signed private payment candidates
only; account bill `ts` is a balance-update time, not settlement time. Neither
that audit nor the forecast parser in `funding_observation_v2.py` can supply
this missing join. Nothing here changes `account_lifecycle_history.py`,
`account_materializer.py`, risk, account revision, or R7 authority.

Synthetic focused tests cover two data pages plus a terminal empty page,
signed/zero rates without account-funding inference, byte/clock replay, policy
and source pins, scope and direction errors, duplicates/conflicts, malformed
JSON, future events, missing termination, and retention gaps. They are source
boundary tests, not authenticated Demo acceptance.
