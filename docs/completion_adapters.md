# Demo completion adapters — implementation checkpoint

2026-09-12. This increment extends the [runtime foundations](runtime_foundations.md).
Implementation, isolated acceptance, deployment and genuine observation are four
different statuses. The new modules do not grant trading authority. Live remains off.

## Demo write boundary

`OkxDemoService` now repeats write readiness and symbol checks after acquiring its
async lock. Cancel, close-position and leverage changes also retain the applicable
checks immediately before the private write, after any intervening account read.
Leverage changes recheck the current cap at both boundaries. Existing order entry
retains its final preflight. Valid cancellation, closing and protection workflows
are not intentionally removed.

This fixes stale permission/cap state during an await; it is **not** a G1–G12
admission mechanism and does not authenticate account claims. Shared transports
and Live order paths have not been redesigned by this change.

The targeted regression first reproduced two leverage-cap failures, then passed
239 tests. Cross-review added 13 failing cases for tightened entry size/position
caps, protection requirements and callback revocation; the final boundary checks
passed 252 tests across the new cases and existing Demo service, automation and
dynamic-leverage suites. These are synthetic tests, not orders.
The touched legacy service retains 22 pre-existing Ruff findings; the change
introduced none. New test files are linted separately.

## Account records collector

`app.trade_qualification.account_collector.collect_demo_account_records` owns a
single fixed-origin HTTPS client and one immutable Demo credential handle. It
uses only the fixed GET inventory defined in `account_capture`, with simulation
headers, exact external plan and account pins, bounded pagination/bytes/time,
raw response replay, secret-echo rejection and bounded cleanup. No automatic
retry, proxy, redirect, Live fallback or order endpoint is provided.

The caller must supply a reviewed `DemoAccountCapturePlan`, its independently
retained SHA256, a same-session `DemoAccountCredentials` and the actual preceding
barrier time. A diagnostic barrier must never be labelled an actual G12 barrier.
The returned packet must pass canonical freezing/replay before it is returned.

`AccountCollectionError` preserves the existing outer error code. Its optional
immutable `diagnostic` contains only a fixed stage, stream/page index and reviewed
local `capture_reason`. Only the real parse/replay/freeze boundaries may supply
it. Unknown, secret-bearing, hidden or non-exact exceptions do not expose their
contents; no body, provider message, account ID, headers or traceback is included.
The original 09:49 UTC read-only diagnostic was rejected without a packet and is
retained separately; the coarse error alone does not identify the failing source.

Successful collection still returns `records_verified_incomplete_account`:
`account_complete`, `source_authenticity_verified` and `execution_authority` remain
false. Complete ingestion history, seed/peak, non-SWAP scope, same-time account
materialization, local uncertain reservations, specifications and protection
mapping remain separate requirements. Do not feed this packet directly into G11
as a complete portfolio snapshot.

## Notion delivery and one-pass worker

`app.trade_evidence.notion_adapter` implements the existing outbox delivery
interface. A caller explicitly supplies an isolated client, secret token and
reviewed destination: exact database ID, data-source ID and the four necessary
property ID/name/type pins. Additional database fields are not edited. The
adapter does not discover credentials, create a database or change its schema.

The four roles are `report_id` (title), `envelope_sha256`, `payload_sha256` and
`metadata_json` (rich text). Delivery validates the destination schema, queries
the exact report ID, verifies an existing matching page or creates at most one
page, then requires readback. Conflicts, duplicate pages, 429 responses and
ambiguous create outcomes stay uncertain. Only clearly pre-create transient
read failures can be classified as known-not-created/retryable. An uncertain
page must never trigger another trade or a blind second create.

`outbox_worker.run_outbox_pass` processes only an explicit tuple of at most 16
report IDs under a bounded cooperative deadline. It uses existing durable
recovery/dispatch, skips terminal, uncertain, leased and not-yet-due records, and
does not scan directories or run a scheduler. Native storage permission failures
remain failures; there is no ACL fallback. Independent outbox roots and external
Notion writers are outside the local fencing guarantee; remote exactly-once is
not claimed.

CTCC's connected Notion tool can update project documentation, but that does not
automatically provision a REST token or destination pins inside the CTCC process.
An actual post-submit producer, configured runtime adapter and worker lifecycle
are still needed before marking automatic Notion reporting complete.

The existing CTCC report data source
`cbbdc739-2723-4e07-a5d7-7d4d1395658e` (database
`13d5e61fce534184a42e0b01c4f372d3`) now has three additional rich-text fields:
`Outbox Envelope SHA256`, `Outbox Payload SHA256`, `Outbox Metadata JSON`.
Readback verified 46 original property definitions unchanged and 49 total.
No existing trade rows were edited. The existing title is `報告名稱`; the
runtime property-ID pins must still be read through the reviewed REST connection,
not guessed from display names. No automatic delivery is claimed by this schema edit.

## Trade forensics

`app.trade_evidence.forensics` is a pure, source-bound analyzer for linear,
quote-settled Demo contracts. It binds per-fill and fee/funding events, explicit
coverage and price intervals to the immutable original candidate. Existing
aggregate order PnL is not silently converted into complete per-fill history.

FIFO partial-close accounting uses exact rational arithmetic. Decimal values
are presentations; numerator/denominator retain the exact result. Signed fees
and funding are credits/debits and require explicit coverage. Missing costs,
unconverted currencies, incomplete fills or price-path gaps remain unknown.

MFE/MAE mean gross trade cash excursions: realized FIFO PnL plus the remaining
inventory marked during fully covered holding intervals. R uses the original
planned contracts, contract value and original entry-to-stop risk, never an
improved post-trade denominator. Price intervals crossing fill boundaries cannot
establish intrabar extrema ordering.

The eleven requested categories are observations or attributed source claims,
not proven causes. Entry timing, original entry-zone violations and adverse
slippage may be computed from supplied records. One loss does not establish
Strategy Edge Failure; profitability, calibration, account authenticity and
execution authority remain unproven. Frozen results must be independently pinned
and replayed through the verifier.

## Targeted validation before source freeze

Initial account collector/capture scope: 525 passed. The final diagnostic increment
passed 602 tests (311 collector tests and 291 unchanged raw-capture tests), including
77 new static-diagnostic and secret/exception-injection cases.
The adapter and worker together passed 207 tests
on Windows, with two actual POSIX filesystem workflows reserved for Linux.
Forensics initially passed 202 synthetic tests. Independent review reproduced
missing entry-only cash accounting and a terminal partial-exit excursion error;
the fixes passed 214 tests. Complete fills now establish zero realized gross
before any exit, while known entry fees/funding still affect cash-to-date. Final
PnL remains unknown until flat. Price intervals are explicitly half-open, and a
terminal fill marks all remaining inventory for gross cash excursions.
These scopes are separate from the 252-test
Demo service scope and do not constitute a full regression or genuine samples.
All nine new Python module/test files pass Ruff check and format check.

Cross-review required equal-timestamp fill handling: opposite roles are rejected;
equal-role FIFO uses an explicit lexical evidence-ID accounting convention and
does not assert exchange ordering. Their path excursions remain unknown.

## Remaining acceptance gates

- Trusted account materialization and complete history/peak/ledger evidence.
- Current G12 publication → newly captured sources → unchanged candidate recheck
  → atomic reservation → single submit across the actual runtime.
- Real post-submit Notion configuration/lifecycle and observed delivery receipts.
- Genuine new-pipeline Shadow/Demo samples and source-backed realized forensics.
- Exact new-source full regression, new-image/deployment acceptance and same-source
  GitHub CI. A previous commit's test count is not transferred to this increment.

GitHub and Notion plugin permission modes were changed to `full_access` with
project-scoped user consent. GitHub's installation list remains empty and the
browser is at sign-in: local permission mode does not replace account login or
repository installation grants. No alternate credential path was used to bypass
the prior GitHub rejection. Source, manifests, archives and test receipts remain
locally backed up until normal repository authorization is restored.
