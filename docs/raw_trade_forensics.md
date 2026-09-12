# Retained raw post-trade forensics integration

`app.trade_evidence.raw_forensics` connects the existing canonical
`post_submit.DemoSubmissionReport` and the complete retained
`account_capture.DemoAccountPacket` to the existing FIFO trade-forensics evaluator.
It maps the supplied exchange fill rows, once-only fill fees, funding-bill claims
and independently pinned recorded last-trade OHLC intervals. It does not invent
fills, infer protection from an acknowledgement, or accept a caller-provided PASS,
PnL, stream-complete flag or computed forensic result.

This is an offline source integration, **not the running service lifecycle**.
There is no HTTP client, credential lookup, environment access, wall clock,
filesystem operation, submit/cancel callback, worker installation or Notion write.
The API returns canonical receipt bytes for the existing upstream owner to retain;
it does not introduce another journal or overwrite the immutable submission outbox.

## Entry and replay

`reconstruct_trade_forensics(submission_bytes, *, expected_submission_sha256,
account_bytes, expected_account_sha256, expected_account_plan_sha256,
attribution_bytes, expected_attribution_sha256, holding_path_bytes=None)` returns
`RawForensicsReplay`. Its `analysis` is the existing `TradeForensicsResult`;
`canonical_receipt` and `receipt_sha256` bind every input source and the reconstructed
analysis. Keep the raw inputs and independently retained original pins together.

`verify_raw_forensics_receipt(receipt_bytes, expected_receipt_sha256,
submission_bytes, **source_arguments)` reruns **all** original source parsing and
forensic computation, then compares exact canonical receipt bytes. Rehashing
fabricated metrics, flags, source links or funding claims is not sufficient.
The returned dataclass is not a security token and is never accepted as order
authority. Changing its fields does not change the independently verified bytes.

Public failures use only `raw_forensics_reconstruction_invalid` or
`raw_forensics_receipt_invalid`; raw account error messages are not interpolated.
Process-control exceptions are not caught. Bytes and pin types are exact, bounded
and checked before decoding; custom Python serializers/iterators are not invoked.

The submission must be an explicitly acknowledged Demo report. A previously
uncertain submission needs the upstream reconciliation workflow first; this module
does not rewrite its state. Replaying an acknowledgement still proves neither a
fill nor protection. The account UID/currency and retained history window must
match the original candidate and include submission completion. All original
account observations, pagination links, raw bodies and identity receipt bindings
are replayed using the existing account capture implementation.

## Pinned attribution schema

Attribution is canonical UTF-8 JSON: sorted keys, ASCII escaping, compact
separators, no duplicate keys, no JSON numbers/booleans, at most 65,536 bytes.
Its exact keys are:

| Field | Meaning |
| --- | --- |
| `schema_version` | `ctcc.raw_trade_attribution.v1` |
| `report_id`, `candidate_sha256` | Existing original candidate pins |
| `submission_report_sha256`, `account_packet_sha256` | Exact retained source bytes |
| `orders` | 1–32 `{order_id, client_order_id, role}` records |
| `funding_bill_ids` | Up to 1,024 distinct selected source bill IDs |
| `holding_path_sha256` | Exact path-envelope digest or JSON null for absent path |
| `purpose` | `synthetic_test` or `observed`; neither proves a real trade |

Order IDs and client-order IDs must be unique. Exactly one entry order must equal
the acknowledged order/client ID pair. Additional bindings are exits. The complete
set of **provided** fill rows for each bound order is mapped automatically, not a
caller-selected favorable subset. Matching `clOrdId`, instrument, position side,
actual `tradeId`, positive `fillSz`/`fillPx`, signed `fee`, `feeCcy`, `fillTime` and
generation `ts` are required. Missing values are errors, never zero/fallbacks.
Duplicate trade IDs, contradictory side/role, over-closes and equal-time mixed
entry/exit roles fail. Fill times must follow submit start and precede generation
and the retained query cutoff; receipt times remain the actual recorded page times.

Descending account pages are explicitly converted into chronological FIFO order;
equal-time same-role rows use namespaced bill IDs, the existing evaluator's
documented accounting convention, not a claim about exchange intratimestamp order.
Each fill retains page-body/observation pins and original trade/bill IDs. Same-
instrument fills without an order binding remain listed as unattributed and cannot
silently contribute to this report.

Exit reasons stay `unknown`; no stop/target label or exit reference price is
inferred from an order ID, observed price or candidate target. `fillPnl`, order
aggregate PnL, and mirrored trading-bill fee/PnL fields are not used to create cash
profits. Each fill's signed fee enters the existing evaluator exactly once;
foreign fee currency is preserved without an invented conversion.

## Funding and path are not fabricated coverage

Only selected raw type `8`, subtype `173`/`174` funding bills for the candidate
instrument are retained. Their signed `balChg`, currency, source `ts` and original
page pins are recorded separately. Source generation time must follow submission
and not exceed the account cutoff. Bill `ts` is **not** silently interpreted as
effective funding accrual time, and no claim of exclusive account-position
attribution is made. Consequently these rows are not yet `CashFlowEvent` funding
events: `effective_accrual_at` remains null and `funding_accrual_verified=False`.
Unselected same-instrument funding bill IDs remain visible in the receipt. An
upstream effective-accrual and exclusive holding attribution source is still needed.

The optional path is a canonical, bounded (1 MiB) **local recorder envelope**, not
a claim that OKX natively returns this format. Its exact keys are:

- `schema_version`: `ctcc.recorded_holding_path.v1`.
- `instrument_id`: the original candidate instrument.
- `price_basis`: `last_trade`, never mark/index price.
- `received_at_ms`: decimal-string Unix milliseconds of the actual recording.
- `intervals`: up to 4,096 arrays
  `[start_ms, end_ms, open, high, low, close]`, all decimal strings.

Intervals remain in supplied order, with no clipping, interpolation, repair,
resampling or timestamp substitution. OHLC geometry, non-overlap, recording time
and containment between candidate recording and account history cutoff are checked.
Gaps and intervals crossing a fill do not establish intrabar extrema. A consumer
still must retain the actual producer's original raw source and establish its
provenance; merely writing this local envelope does not authenticate an exchange
price path.

The existing account packet explicitly lacks authenticated ingestion/retention
and atomic-cutoff proof. A terminal empty page is not complete account history.
Therefore **all four forensics coverage streams remain partial**. The mapped raw
events and original risk are available for inspection, but final position state,
PnL, MFE/MAE and costs are unknown when the existing evaluator requires complete
streams. No empty input becomes a verified unfilled trade or zero cost.

All receipt flags for execution, authentication, ingestion completeness,
protection, effective funding accrual and verified real trade sample remain false.
`purpose="observed"` is only a source label; it cannot increment real Shadow/Demo
acceptance samples. This increment does not complete authenticated account scope,
real fill/bill capture, storage lifecycle, Notion delivery or live deployment.

## Local verification

`tests/unit/test_trade_evidence_raw_forensics.py` uses only synthetic retained raw
account packets and submission records. It covers both directions, partial fill
mapping, fee rebates and mirrored-fee exclusion, duplicate trades, account/order
pins, ambiguous timing, bill-source provenance, missing accrual time, bounded
canonical JSON, malformed/hostile input, path gaps/cutoffs, low-precision Decimal
contexts, and rehashed result forgery. No actual exchange or Notion calls occur.
These are unit/integration-of-pure-components results, not authentic exchange
fills, service deployment or Windows filesystem acceptance.

2026-09-12 local verification: this module's 113 tests passed; the combined run
with existing forensics, post-submit reporting and account capture was 784 passed,
1 skipped in 9.68 seconds. The skip is the existing POSIX-only post-submit
filesystem case on Windows. Ruff check and format check passed for the new module
and its tests. These overlapping counts must not be summed and are not a full
new-image/system acceptance run.
