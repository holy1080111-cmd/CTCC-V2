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

### Pre-window capture schedule pin (engineering seam)

`prospective_capture_schedule.py` materializes a versioned, complete OKX
one-minute coordinate plan before the candidate seal. Its hash must equal the
seal's `coordinate_plan_sha256`. After the seal and before the first minute
opens, it derives one exact `PublicMinuteCapturePlanV1` per sealed
minute/instrument coordinate. This ordering avoids pretending that a plan's
caller-supplied `created_ns` is an independent timestamp.

Migration `0026` and `Gate3CaptureSchedulePinRepository` provide a separate,
append-only PostgreSQL pin for the exact canonical schedule bytes and hash.
The insert guard writes `recorded_at` using the database's `clock_timestamp()`
and rejects an insert whose guard time reaches the first window event. That
timestamp alone does **not** prove that the transaction committed before the
event. A successful `publish` also requires a post-commit, separate-session
readback SELECT to return a database
`database_readback_at` strictly before that event. If the readback is late,
the immutable row remains auditable through `read`, but publication success is
denied. The readback timestamp is evidence of that database observation,
not independent custody or Gate 3 approval. The insert guard recomputes the
origin-independent source/instrument/calendar window identity from the schedule
contents, checks the separately hashed coordinate-plan bytes against the nested
JSON coordinates, and rejects a caller-supplied alternate key. Canonical
coordinate-plan bytes and the nested Pydantic contract are checked again on
independent repository readback; SQL JSON equivalence alone is not a canonical
byte proof. It checks the bounded complete minute/instrument plan grid before
inserting a row. A compromised restricted append login may still deny future
availability; it cannot make a malformed row eligible on repository readback.
The role may use only restricted
security-definer append/read functions, and a publication must be read back
from a new session. A second pin for the same seal, holdout ID, or
source/instrument/calendar window is rejected. Downgrade refuses nonempty
evidence. The original V1 public capture plans, receipts, journal and Gate 3
artifacts are unchanged.

This seam is **not deployed or independently protected yet**. The migration
grants no capture role. A real deployment needs an independently administered
PostgreSQL server, a restricted login, a checked server clock, OS-separated
database files/WAL/backups, and an uninterrupted acquisition owner. The local
SQLite seal ledger, a colocated test database, and a self-declared
`created_ns` cannot replace that custody. No future seal, real scheduled
minute acquisition, complete holdout, or first evaluator access proof is
created by this change. Its claim remains computational and Gate 3 remains
blocked. The narrow PostgreSQL test exercises 0024 and 0026 in a disposable
database; the supported 0025-to-0026 Alembic upgrade, full fresh migration,
schema-drift check, Windows/Linux runtime recovery and restricted production
role remain separate exact-source acceptance work.

### Durable schedule publication acknowledgement

Migration `0028` adds an immutable acknowledgement row and restricted
security-definer append/read functions. Its separate direct-login role is
forbidden from writing the `0026` pin table, calling the pin append function,
inheriting or switching to another role, or creating database objects. The SQL
insert guard locks and reads the exact schedule/seal/coordinate/window identity
with `NOWAIT`, then stamps `acknowledged_at` from the PostgreSQL server before
the first window event. A pin visible to that separate login was committed in
an earlier transaction. The ACK itself may commit after the window; its server
observation time is evidence of the **pin's** earlier commit, not the ACK's
earlier commit. A conflicting lock or missing/uncommitted pin fails closed.

`Gate3CaptureSchedulePublicationAckRepository` replays the original `0026`
schedule bytes through the canonical pin reader before append and again on
later ACK readback. Only that combined read establishes that the earlier
committed row is the exact canonical schedule. Raw SQL can acknowledge a
hash-valid but noncanonical `0026` pin; the combined read rejects it. The ACK
role receives only ACK append/read function grants, with no direct pin, ACK, or
`0024` witness table rights and no witness function grants. An exact committed
ACK can be replayed on retry even after the window without writing another row.
Migration `0028` provisions no role or credentials;
production must independently administer and check both restricted logins and
the server clock. The readback marks only a server-clock-ordered committed pin
observation; `trusted_clock_verified` remains false and cannot by itself
promote Gate 3 evidence.

The `0026` raw append still permits a hash-valid noncanonical payload to take
the unique seal, holdout, and window keys. That can prevent a later valid pin;
`0028` does not repair this availability risk or bypass the poisoned key. The
existing schedule-bound dataset V1 does not consume the ACK and continues to
record `pre_window_commit_proven=false`. Even a verified ACK has no independent
custody, first-access, predictive, promotion, or execution claim. The narrow
PostgreSQL `0028` test exercises `0024`, `0026`, and `0028` DDL in a disposable
database; a full migration chain, schema drift check, and production role and
clock acceptance remain separate work.

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

### Complete-window join to the schedule insert guard

`blind_window_schedule_binding.py` now verifies the complete original-byte
blind-window dataset against a separately read migration 0026 row. The caller
supplies the schedule SHA-256 from a retained record outside the dataset and
journal. For every ordinal, the verifier requires the journal-replayed capture
plan hash and minute/instrument coordinates to equal the exact plan whose
database insert guard ran before the first window event. A plan created during the window
for a later minute cannot satisfy this join, even if its individual
`created_ns` precedes that minute. The new binding artifact records the
database's immutable insert-guard `recorded_at` and the dataset/row hashes, and is rebuilt
from the database and original journal bytes on verification.

A later database readback proves the row eventually committed; it does not
prove a pre-window commit or a pre-window publication acknowledgement. The
binding fixes `pre_window_commit_proven=false`. It does not claim that
the database or journal has independent custody, that source bytes were first
observable at a historical decision time, or that the declared evaluator read
was the first read. All eligibility and authority flags remain false.

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
does not independently prove first access. This one-minute access timeline is
not connected to the complete-window schedule binding, independent checkpoint
custody, or a formal evaluation. The existing
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
