# Post-submit reporting integration

`app.trade_evidence.post_submit` connects an explicit completed Demo submission
result to the existing local Notion outbox and, separately, its existing single-pass
worker. It does not submit, cancel, close, reconcile, or repeat exchange orders.
It adds no scheduler, server lifecycle hook, credentials lookup, alternative
outbox, or database schema. The runtime is **not installed or enabled** by importing
this module.

## Required order of operations

1. The upstream order/intent ledger durably records `report_id`, the stable client
   order ID and consumed reservation before an exchange submission can begin.
2. The upstream order component makes its own authorized submission. Reporting
   has no order callback, private HTTP client or other order capability.
3. Once that component has returned, raised or been interrupted, the caller uses
   `build_submission_report` to project only allowlisted result fields. The caller
   retains the canonical report in that **existing** upstream ledger.
4. `enqueue_post_submit` synchronously performs only local outbox IO. An
   acknowledged submission becomes the existing immutable
   `artifacts/notion_outbox/<report_id>.json` and append-only state journal.
5. A separately invoked `run_post_submit_pass` receives explicit report IDs and an
   explicitly constructed `NotionDeliveryAdapter`. It uses the existing worker;
   it never initiates an order or reconstructs a submission result.

Notion must not be awaited before or inside the exchange submit wait path. Calling
the separate pass in the same coroutine and awaiting it before returning the order
response would violate this integration boundary; the producer intentionally
neither accepts nor invokes a Notion adapter.

## API and source pins

`build_submission_report(candidate, *, expected_candidate_sha256,
reservation_before_submit, expected_reservation_sha256,
expected_original_event_key, strategy, client_order_id, evidence_completed_at,
submit_started_at, completed_at, write_result)` returns a frozen
`DemoSubmissionReport`.

The candidate is the existing `forensics.TradeCandidate`; its canonical hash and
original source/evidence hashes are reused. The exact consumed
`reservations.ReservationReceipt` is revalidated, including its coverage, external
hash and original event key. Report, instrument, direction, Demo account scope,
settlement currency and original entry/stop/contracts/contract value must match.
Receipt update time cannot be after submit start. Candidate recording, explicitly
supplied evidence completion, submit start and completion must be chronological.
No timestamp is substituted with the current time. Equivalent aware offsets are
normalized to UTC; naive or unsupported timezone objects are rejected.

This is not another qualification check. A submission that actually happened
after the entry deadline still needs to be reported, rather than erased by a
reporting gate. Current eligibility, fills, protection, PnL and order authority
are not established here. Full forensic calculations remain the separate existing
forensics API; this module does not fabricate fills, fees, funding or excursions
from an acknowledgement.

The legacy `OkxDemoWriteResult` is projected without serializing `exchange_data`,
`order`, `warnings`, protection fields or exchange messages. An acknowledged
projection requires exact `place_order`, `acknowledged=True`, exchange code `0`,
a nonempty bounded order ID and an exact matching client order ID. The result's
completion time must fall between the explicitly supplied start and observation
completion. Explicit `acknowledged=False` with a matching client ID, nonzero code
and empty order ID is a rejected **claim**. Missing, contradictory or incomplete
acknowledgement fields become `uncertain`, never success. Invalid model/identity
contracts fail with a static redacted `PostSubmitError`.

`freeze_submission_report(report)` returns bounded canonical bytes.
`verify_submission_report(bytes, expected_sha256)` checks the external digest,
canonical representation, nested records and all pins again. Neither method
writes a file or establishes authenticated exchange/disk evidence. Private model
state, subclasses, unknown fields, nonfinite/oversized decimals and authority flag
coercion are rejected before serialization at this boundary.

## Uncertainty and crash boundary

A post-submit-only API cannot preserve data that was never durably saved before
the process died. The pre-submit ledger is therefore an upstream prerequisite,
not something this module claims to have inspected or repaired. A caller-provided
receipt or hash alone is not proof of disk persistence: every submission/reporting
record has `durability_not_verified=True` and no execution authority.

For a submit that raises, times out or is interrupted after its network write may
have begun, pass `write_result=None`; do not infer rejection from an exception.
The report is `uncertain` and `requires_upstream_reconciliation=True`. No confirmed
submission outbox job is created. The existing upstream ledger must retain the
report/client-order identity, reconcile the order explicitly and forbid an
automatic duplicate submit. Reporting does not release reservations or mark an
unknown submit as not-created.

`enqueue_post_submit(root, report, *, outbox_policy=None, clock=actual_utc)` returns
`PostSubmitReportingResult`. Only an acknowledged report can return `enqueued` and
`durable_outbox_readback=True`. Rejected/uncertain submissions are `deferred` with
their report ID retained and no filesystem or clock access. A local write/readback
failure returns `failed`, preserving the submission status, report ID and report
hash. It does **not** prove that zero bytes were written: an immutable marker may
have committed before readback failed. All results have `order_retry_authority=False`
and `notion_delivery_verified=False`.

To recover an enqueue failure, retain and reuse the exact same canonical report
and outbox policy. A repeat of **local enqueue only** is idempotent only for an
identical existing job. Do not rebuild completion timestamps, silently replace a
conflict, retry an order, clear partial data or promise partial repair.

The synchronous producer has no await point and does not swallow process-control
exceptions. It cannot promise completion under process termination. The separate
worker preserves external cancellation; its existing durable dispatch fence means
a cancelled/failed/ambiguous remote create becomes uncertain, not a blind Notion
retry. Known not-created outcomes alone follow the existing bounded retry policy;
uncertain delivery requires the existing explicit reconciliation procedure. No
retention cleanup or deletion is added.

## Explicit one-pass entry and remaining configuration

`await run_post_submit_pass(root, *, report_ids, worker_id, adapter, policy,
clock=actual_utc)` delegates to `outbox_worker.run_outbox_pass` and returns its
`OutboxPassResult`. It accepts the exact existing `NotionDeliveryAdapter`, not an
arbitrary function, order callback or adapter subclass. It scans no directory,
discovers no report IDs and never reads an environment token. The adapter owns
responses; its explicitly supplied HTTP client remains caller-owned.

The existing adapter requires an explicitly supplied token, fixed Notion
origin/API version and a pinned database/data-source with four necessary property
pins (report ID, envelope hash, payload hash, metadata). Existing unrelated columns
are allowed; no schema is created or edited. Real credentials, destination pins,
user access, lifecycle scheduling, producer placement in the actual server and
the trusted upstream durable-intent implementation remain separate integration
requirements. No actual Notion write, private account API or server startup is
performed by the tests for this component.

## Filesystem and test boundary

The root must be a pre-existing absolute service-owned directory with trusted
ACLs. The existing outbox reuses the evidence storage's no-clobber, containment,
root lease, fsync and verified readback primitives without modification. POSIX
directory metadata fsync and Windows ancestor handle pinning retain their existing
support limits. A Windows ancestor permission denial fails closed; the tests do
not change ACLs, bypass handles or claim Windows publication success. Filesystem
power-loss atomicity and resistance to malicious same-user writers are not
promised.

Synthetic tests cover typed pin/identity/clock boundaries, JSON replay, acknowledged
versus filled/protected state, unknown and rejected outcomes with no outbox IO,
local idempotency/conflict/partial-readback failures, and the real outbox transition
API behind a memory filesystem seam. MockTransport integration exercises the
existing Notion adapter and worker without network access. Native POSIX cases are
separately marked and must be run on POSIX; memory tests are not Windows IO proof.
