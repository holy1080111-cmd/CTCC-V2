# MIE Gate 3 — Prospective evidence chain

The offline engineering path now has a distinct
`ctcc.mie.gate3.prospective_evidence.v1` schema. It binds a complete synthetic
evaluation report to both the original future-window seal and the later
acquisition receipt. The existing retrospective evidence schema is unchanged;
it does not accept a union of retrospective and prospective preregistrations.

This is a **computational-only contract**, not a real candidate evaluation or
proof of predictive value. No real future window was sealed or acquired by
this implementation. No market, account, database, runtime, or order interface
was added or invoked.

## Chain and trusted pins

```text
independently retained seal SHA-256
  -> acquisition receipt embeds that exact canonical seal
  -> independently retained receipt SHA-256
  -> report embeds both stages and binds holdout dataset + replay provenance
```

Both `freeze_prospective_evidence_artifact` and
`verify_prospective_evidence_artifact` require
`expected_preregistration_sha256` and `expected_holdout_receipt_sha256`.
Verification additionally requires the report's `expected_sha256`.
Callers must retrieve these pins from their independently retained prior-stage
records, not derive them from the report being verified. Rewriting the whole
embedded chain and recomputing every internal hash does not satisfy the
original external pins.

The report verifies canonical equality between its seal and the receipt's
embedded seal. Provenance must match the seal's timestamp and source-tree hash,
the receipt's canonical hash, and the receipt's **holdout** dataset content and
manifest hashes. Substituting the training dataset is rejected.

These checks detect inconsistent or substituted attestations relative to
trusted pins. They do not authenticate who recorded a timestamp, independently
prove when a seal was first published, or recompute replay/cohort hashes from
raw rows. Those acquisition and row-level availability checks remain pending.

## Evaluation timeline

Acquisition access and evaluator access are different events. The receipt is
an immutable pre-evaluation record with `evaluation_started=false`. The report
therefore requires this ordering:

```text
dataset frozen <= receipt recorded <= evaluator first read
  <= evaluation started <= report generated <= report reviewed
```

The receipt retains its actual first-acquisition timestamp and eligibility
outcome; it is not rewritten to pretend evaluation began during acquisition.
Equality is allowed for sequential events recorded at the same timestamp
resolution. The schemas cannot enforce a global one-evaluation limit or prevent
an operator from running code outside the recorded workflow.

## Complete common-cohort reporting

Prospective metrics and reliability bins use their own narrow
`prospective_holdout` partition. Development, validation, and retrospective
partitions cannot be relabelled by supplying them to the new schema; the
existing `DatasetPartition` enum has not been expanded.

Even a computational report must contain:

- every frozen candidate/baseline subject;
- all probability metrics for each subject, plus the candidate's declared
  economic metrics;
- structured reliability bins with the frozen count and equal widths;
- confidence intervals for every non-count metric;
- every declared trial with correctly recomputed Holm adjustment;
- consistent cost-applied flags and a single common sample cohort;
- a canonical exclusion ledger whose row count plus the common included
  sample count equals the receipt's expected source rows.

One source row represents one potential evaluation opportunity. Warmup,
unavailable labels, and any other dropped opportunity must be included in the
common exclusion ledger, rather than silently giving baselines different
sample populations. `evaluation_cohort_sha256` identifies that common cohort
but is not, on its own, proof of its row membership. Reliability-bin width
validation uses fixed 50-digit decimal precision, including nonterminating
widths such as thirds, independently of the ambient decimal context.

## Fail-closed claim boundary

The outer report fixes `validation_claim=computational`,
`predictive_oos_eligible=false`, `promotion_eligible=false`,
`authority=offline_shadow_only`, `runtime_consumers=0`, and
`execution_authority=false`.

A receipt's `predictive_oos_eligible=true` concerns its recorded acquisition
conditions only. It does not provide independently qualified per-row timing.
Neither that flag nor a reviewer marking every check passed can raise the
report's claim. Early/exposed/changed-candidate/unverified receipts may still
be retained in computational audit reports; they cannot be promoted.

Freeze and verification reconstruct the exact schema, rejecting invalid nested
copies and constructed instances. Subclass canonical serializers cannot replace
the schema's canonical bytes. Wrong-schema, duplicate-key/noncanonical JSON,
wrong pins, and mismatched hashes fail closed.

## Verification and remaining work

Focused synthetic tests are in
`tests/unit/mie/test_gate3_prospective_evidence.py`; the structural package
boundary tests include this report's zero runtime/execution authority.
The fixtures' dates, scores, hashes, and reviewer identities are synthetic and
must not be treated as a real seal, evaluation, or independent review.

Remaining Gate 3 evidence work includes independent source/row-availability
qualification, real batch/plan linkage, candidate fitting on approved past
partitions, a genuinely precommitted fresh window, reviewed single evaluation,
and independent uncertainty/cost/leakage review. A separately reviewed later
contract would be needed before a prospective predictive claim can be made.
Gate 4 and Demo/Live activation remain blocked; Docker deployment verification
is a separate unfinished operational task.
