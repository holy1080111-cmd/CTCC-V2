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

## Current evidence status

This change implements and tests the seal and receipt machinery; it does not
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
leverage, margin, exchange payload, API route, database write, or runtime
consumer. Creating a seal never downloads market data, and creating an
acquisition receipt never evaluates a strategy or authorizes an order.

## Remaining evidence work

1. Build and validate a deterministic candidate using only declared past
   development/validation data.
2. Commit its exact source/configuration hashes and a real prospective seal
   before the chosen future window starts.
3. After the window and publication lag, use a no-summary acquisition path and
   freeze the matching receipt.
4. Run the one declared holdout evaluation, construct the evidence artifact,
   and obtain independent leakage/trial/uncertainty/cost review.

Until all four steps pass, Gate 4 remains blocked and no decision or execution
authority changes.
