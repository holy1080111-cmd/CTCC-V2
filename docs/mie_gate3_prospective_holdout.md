# MIE Gate 3 — Prospective holdout seal

Gate 3 now has a fail-closed contract for the period before a genuinely fresh
holdout exists. The seal freezes the selected candidate, every declared trial,
feature parameters, development/validation windows, costs, metrics, future
holdout coordinates, expected file and row counts, publication lag, and first
permitted access time before the first holdout event.

This closes a gap in the original preregistration contract. That contract binds
a complete dataset after it has been materialized and can represent an unread
dataset, but it cannot prove that candidate design predates a future data
window. The prospective contract does not invent content hashes for data that
does not yet exist. Instead, a later acquisition receipt must bind the exact
materialized dataset back to the earlier canonical seal.

## State transition

```text
past development/validation data frozen
  -> candidate, trials, costs, metrics, and future coordinates sealed
  -> holdout window occurs with no access
  -> declared publication lag completes
  -> sealed automation acquires and verifies exact artifacts without summaries
  -> acquisition receipt remains computational until one reviewed evaluation
```

The first contract has
`schema_version=ctcc.mie.gate3.prospective_preregistration.v1` and the second
has `schema_version=ctcc.mie.gate3.prospective_holdout_receipt.v1`.

## Machine-enforced rules

- The preregistration timestamp must precede the first holdout event.
- The candidate must select exactly one declared frozen trial and use the same
  configuration hash.
- Baseline IDs must be unique and must differ from the candidate ID, so
  evidence requirements cannot collapse distinct evaluation subjects.
- Training data and the validation window must end outside the declared
  holdout embargo.
- Feature, label, cost, purge, embargo, and holdout durations must align to the
  frozen bar interval.
- Holdout start/end times must align to complete source artifacts.
- Expected artifacts and rows are derived from the exact duration, bar cadence,
  artifact cadence, and sorted instrument set; caller-supplied mismatches are
  rejected.
- First permitted access is exactly holdout end plus the declared publication
  lag. The receipt independently records whether actual timing complied.
- The receipt must reproduce the preregistration hash, acquisition-plan hash,
  source/version, instruments, first/last event, row count, and artifact count.
- Predictive eligibility is true only when timing complied, every artifact was
  verified, no human-facing descriptive summary was exposed, and the candidate
  did not change after preregistration.
- Early or exposed access can still be recorded for audit, but the schema
  requires `predictive_oos_eligible=false`.

Both contracts are immutable, canonical JSON/SHA-256 artifacts. Freeze and
verification revalidate the exact schema before accepting bytes, so nested,
copy, subclass, noncanonical-JSON, and hash tampering fail closed.

## Offline publication and one-attempt ledger

`app.mie.validation.seal_ledger.Gate3SealLedger` provides a dedicated local
SQLite publication store. `publish_seal` accepts the exact prospective schema,
freezes canonical bytes, requires the local publication time to precede the
future holdout window, and commits the seal under unique digest,
preregistration ID, holdout ID, and source/instrument/calendar window. A second
publication, including a changed candidate or renamed ID for the same window,
is rejected. It then verifies the stored
bytes and timestamp through a separate database connection. The same
no-clobber/readback rule applies to a later matching acquisition receipt.

`reserve_formal_evaluation` is called **before** evaluation data is read by a
consumer using this interface. A database transaction consumes the seal's
single evaluation slot and retains the reservation across process restart or
crash. Two workers racing for the same seal yield at most one reservation.
Neither a caller-provided eligibility flag nor the receipt's own
`predictive_oos_eligible` claim can change the publisher or reservation's
fixed `computational`/`predictive_oos_eligible=false` result. The ledger stores
no market data, credential, account, or order authority.

This is a local coordination mechanism, **not** independent seal-time
attestation or proof of first evaluator access. The host clock and SQLite file
must be protected and backed up as one source of evidence; an evaluator could
read data outside this interface. A real controlled access service, independent
time/hash pins, complete source-qualified holdout, and actual candidate
evaluation remain necessary. No real seal or receipt has been published by
this change.

## Current evidence status

This change implements and tests the seal and receipt machinery and its local
publication ledger; it does not
declare a real future window, freeze a real candidate, acquire a new artifact,
or evaluate a holdout. The current claim remains `computational`.

The already downloaded 2026-07-23 through 2026-08-21 retrospective partition
remains permanently ineligible for a predictive claim because its descriptive
summary was exposed before candidate preregistration. It is not relabelled or
reused as a prospective holdout.

## Authority boundary

The prospective models have
`authority=offline_shadow_only` where applicable,
`runtime_consumers=0`, `execution_authority=false`, `reference_only=true`, and
`promotion_eligible=false`. They contain no account, order, quantity, contract,
leverage, margin, exchange payload, API route, or runtime consumer. The
separate offline publisher writes only the local seal/receipt/reservation
database. Creating a seal never downloads market data, and creating an
acquisition receipt never evaluates a strategy or authorizes an order.

## Remaining evidence work

### Blind in-window minute acquisition seam

`app.mie.validation.blind_window_capture.bind_blind_window_minute` now binds **one**
prospective OKX 1m observation to a pre-existing seal and the complete owned
public-source journal. It requires independently retained hashes for the seal,
preselected capture plan, accepted capture receipt, and latest journal
checkpoint. The original bytes and attempt chain are replayed through the
journal; a legacy receipt without a bound acquisition attempt is rejected. The
minute must be planned before its opening, requested after it closes, validated
within one minute of close, and lie inside the sealed future coordinates.
A later duplicate capture cannot borrow the first observation's earlier
availability timestamp. The
result stores only row identity/hash, source-minute hash, causal timestamps,
and source/journal hashes; it contains no OHLCV values or strategy output.

This is a **computational-only acquisition audit**. The existing prospective
receipt's `first_accessed_at` still means post-window acquisition and requires
the declared publication lag. This new in-window record is **not** passed to
that receipt or relabelled as compliant with it. The separate timeline below
records automated acquisition and a declared evaluator read, but does not prove
that the declaration was the first read. Gate 3 must also prove complete
multi-instrument coverage,
independent checkpoint retention and rotation, and an original-byte-derived
dataset identity before any predictive claim is reviewed. There is currently
no scheduler, no real prospective seal, no real in-window capture, and no
candidate evaluation. The focused test uses synthetic owned-labelled source
bytes and does not establish native TLS, clock, or operator non-access.
The record keeps both in-process validation and later durable readback times;
a future decision-time dataset builder must choose a reviewed conservative
availability cutoff and must not infer that the receipt was durable earlier.

### Separate automated acquisition and evaluator read

`app.mie.validation.prospective_access_timeline` records one blind in-window
minute and one separately declared evaluator read. It binds the already replayed
`BlindWindowMinuteCapture` and the read declaration by their canonical SHA-256
values. Freeze and readback require independently retained preregistration,
capture, declaration and final artifact hashes. The read cannot be declared
before the captured bytes were retained or before the seal's post-window
publication lag. Changing and rehashing a nested declaration cannot satisfy
the original external pin.

This is a computational audit of **distinct times**, not proof that the
declaration was the first human or model read. The evaluator access basis is
fixed to `caller_declared_unverified`; `first_read_independently_verified`,
`evaluator_first_read_proven`, `complete_dataset_proven`, and
`predictive_oos_eligible` are fixed false. The local one-attempt ledger above
does not independently prove first access. No complete multi-instrument dataset, prospective collector schedule,
independent checkpoint store, or formal evaluation is connected. The existing
`Gate3ProspectiveHoldoutReceipt` retains its post-window acquisition semantics;
an in-window minute is not inserted into it or relabelled as post-window
acquisition. A reviewed future receipt must represent the two access events
separately before any predictive claim can be considered.

1. Qualify row-level availability provenance and implement the offline replay
   adapter. Archive publication/observation timestamps do not prove when each
   historical bar was first observable. Assumed bar-close availability must
   remain computational-only.
2. Build and validate a deterministic candidate using only declared past
   development/validation data.
3. Commit its exact source/configuration hashes and a real prospective seal
   before the chosen future window starts.
4. After the window and publication lag, use a no-summary acquisition path and
   freeze the matching receipt.
5. Bind evaluated evidence to the original seal and acquisition receipt — the
   computational-only engineering contract and synthetic tests are implemented
   in `docs/mie_gate3_prospective_evidence.md`. External seal/receipt pins are
   mandatory; actual row-level source qualification and predictive evidence
   remain pending. `Gate3EvidenceArtifact` stays retrospective-only.
6. Run the one declared holdout evaluation, construct the evidence artifact,
   and obtain independent leakage/trial/uncertainty/cost review.

Until all evidence steps pass, Gate 4 remains blocked and no decision or
execution authority changes. Completing step 5's synthetic contract does not
substitute for any of the real evidence steps.
