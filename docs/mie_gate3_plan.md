# MIE Gate 3 — Offline replay and calibration plan

Gate 3 turns the deterministic Gate 2 feature core into a reproducible offline
validation experiment. It remains shadow-only: no runtime consumer, API route,
database migration, risk budget, leverage, order geometry, Demo write, or Live
write may be added by this Gate.

## Objective

Produce one immutable artifact that can answer whether any Gate 2 feature adds
out-of-sample predictive information beyond simple baselines. A deterministic
implementation or an attractive retrospective chart is not sufficient. Gate 3
does not itself authorize a decision gate or claim economic value.

## Current implementation status

The automation-safe engineering foundation is implemented and documented in
`docs/mie_gate3_foundation.md`. The 180-artifact public batch also completed
on 2026-09-02 and is bound by the fail-closed receipt documented in
`docs/mie_gate3_batch_qualification.md`: 180 artifacts, 259,200 minute rows,
six summaries, and zero overlaps passed with no strategy, costs, runtime
consumer, or execution authority.

The probe exposed a descriptive summary of its retrospective holdout before a
candidate was preregistered. Candidate design did not predate that access, so
the partition is ineligible for a `predictive_oos` claim and may only rehearse
the replay pipeline at the `computational` level. No fitted candidate result or
promotion review has been executed.

The prospective-seal machinery is now implemented and documented in
`docs/mie_gate3_prospective_holdout.md`. It freezes candidate/protocol choices
before a future window and makes any early, exposed, changed-candidate, hash,
coordinate, artifact-count, or row-count mismatch ineligible. No real future
window or candidate has yet been sealed.

## Work packages

1. Freeze contracts for dataset identity, bar construction, outcome labels,
   feature parameters, train/validation/holdout boundaries, purge and embargo,
   baselines, costs, metrics, trial count, and artifact provenance.
2. Build a point-in-time replay engine that consumes only confirmed data at the
   declared cutoff. Add synthetic leakage traps, timestamp-boundary tests,
   missing-bar rejection, and deterministic replay hashes.
3. Add purged walk-forward evaluation. Parameters and model selection must be
   frozen from development/validation partitions before the retrospective
   holdout is read.
4. Compare against constant-prevalence, no-skill, and frozen legacy-score
   baselines. Probability outputs must report Brier score, log loss,
   calibration error, reliability bins, and sample counts.
5. Report block-bootstrap uncertainty, multiple-testing correction, turnover,
   drawdown, CVaR, fees, funding, spread, and slippage under a declared cost
   model. Cost-free results must be labelled descriptive only.
6. Write a canonical JSON evidence artifact containing source/model versions,
   dataset and replay hashes, all boundaries, parameters, exclusions, metrics,
   uncertainty, trial accounting, and reviewer metadata.

## Automation boundary

The following work may be automated without market or exchange authority:

- contract and serializer implementation;
- synthetic and committed-fixture replay tests;
- purged/embargoed splitter tests and leakage sentinels;
- deterministic baseline, metric, bootstrap, and cost calculations;
- static checks proving no imports from API, Demo, Live, risk, or execution
  packages;
- hermetic CI, manifest generation, and schema-drift verification.

The completed data-qualification step was explicit and separately reviewed:

- run the 180-artifact public reference-only batch probe and verify its pinned
  identities and 259,200 expected rows — completed 2026-09-02;
- bind its hashes and exposed-holdout state in a machine-verifiable,
  fail-closed qualification receipt — completed 2026-09-02.
- implement canonical prospective preregistration and post-acquisition receipt
  contracts, including fail-closed timing and eligibility rules — completed.

The following evidence steps remain explicit and separately reviewable:

- freeze the candidate, parameters, costs, trials, and preregistration before
  any access to a fresh holdout;
- execute the real replay once against that newly sealed holdout;
- independently review leakage, trial accounting, uncertainty, and costs;
- decide whether the maximum validation claim is still `computational`, can
  become `predictive_oos`, or fails closed.

No evidence step in this list requires or permits an exchange order.

## Acceptance criteria

Gate 3 is complete only when all of the following are true:

- every input row and derived bar has point-in-time provenance;
- replay is byte-for-byte deterministic from the same manifest and plan;
- purge and embargo cover the largest feature and label dependency windows;
- the holdout is untouched until the preregistration hash is frozen; the v3
  retrospective partition no longer meets this criterion and cannot support
  the predictive acceptance path;
- all baselines, metrics, confidence intervals, costs, and trials are reported;
- invalid/missing data and non-finite calculations fail closed;
- an immutable evidence artifact passes schema and hash verification;
- runtime consumers and execution authority both remain exactly zero;
- the full hermetic regression, Alembic `0016`, schema drift, and canonical
  manifest pass from the reviewed tree.

## Promotion boundary

A successful Gate 3 artifact may attest at most `predictive_oos`. Gate 4 may
propose a read-only runtime shadow consumer only after independent review. An
`economic_oos`, `demo_execution`, or `production_eligible` claim requires a
later frozen Gate with additional evidence. The Safety Kernel remains the only
component that can authorize execution, and no validation level grants
exchange-write authority.
