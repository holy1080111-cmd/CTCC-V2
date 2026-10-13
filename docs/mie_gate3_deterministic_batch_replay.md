# Gate 3 deterministic minute aggregation

`app.mie.validation.batch_replay` joins the existing archive batch verifier to
the existing MIE feature replay. It accepts complete Development or Validation
windows only, with UTC four-hour boundaries, exact ordered minute coverage, one
instrument, externally retained plan/source pins, and an availability record
bound to each source row. It never sorts, deduplicates, fills missing rows, or
accepts holdout input.

The output contains 15m, 1H, and 4H OHLCV series. Each aggregate keeps the ordered
constituent digest, receipt hashes and availability bases; its available time is
the latest constituent availability. Volume is summed with bounded exact Decimal
arithmetic under a fully pinned Decimal context, independently of the caller's
context and process `DefaultContext`. Feature replay also pins the Gate 2
Decimal context before calculation, so identical source rows and cutoffs yield
the same replay hash under different caller settings. The source volume unit
remains explicit; the archive adapter labels it `source_unspecified` until a
separate source-unit contract establishes a unit. It never calls volume USD or
contracts by inference.

Measured receipt, archive observation and assumed close are separate source
bases. None of these caller-supplied attestations independently authenticates
acquisition. Every plan and output therefore remains `computational`,
`predictive_oos_eligible=false`, `execution_authority=false`, and
`runtime_consumers=0`. Archive retrieval times remain unchanged. A historical
feature cutoff before those retrievals fails through the existing replay gate.
When a controlled public journal later records a conflicting revision for any
selected minute, the measured-minute adapter rejects even the earlier accepted
capture. The journal retains both observations for investigation; choosing the
old capture hash cannot erase the known contradiction. This is conservative
current-source admission, not independent proof of point-in-time custody.

`replay_aggregated_features_at` rebuilds aggregates from the original bound minute
records and independently retained hashes before invoking the existing MIE
feature engine. Matching output schema alone cannot pass original-source replay.
The feature cutoff must remain before the next expected bar close after the
last due bar. A missing trailing bar therefore fails instead of leaving a stale
feature snapshot apparently current; this also applies to the post-read V3
minute-row and stitched replay paths.
The existing purged walk-forward splitter consumes the aggregate close times and
continues enforcing its dependency, purge and embargo rules. No candidate,
probability model, fitted trial, cost schedule or economic threshold is selected
by this adapter.

## Measured-receipt forward labels

`ForwardDirectionLabelV2` preserves separate UTC times for the base bar close,
its measured availability, the decision, the exact outcome bar close, its
measured availability, and the outcome read. A decision must occur after the
base receipt and before the next bar closes. The complete outcome series must
be available by the read time. The original `ForwardDirectionLabel` and
`forward_direction_label` retain their existing bar-close semantics unchanged.

The V2 label also hashes the modeled base and outcome `FeatureBar` contents
separately. Its `outcome_window_rows_sha256` covers every validated
`PointInTimeBar` from the base through the exact outcome, inclusive, in original
chronological order. Each digest is SHA256 of UTF-8 JSON with sorted object
keys, compact separators, no NaN and the model's JSON serialization. This
detects a changed modeled price or intermediate bar even if a caller repeats
the old self-asserted `source_row_sha256`. It does not establish that the bytes
came from an exchange: a caller who controls both modeled rows and label can
regenerate these hashes. An independently anchored raw-row journal and
previously retained receipt are still required to detect source revision.

`forward_direction_label_v2` requires a separately retained SHA256 pin of
`FrozenFeatureReplayPlanV2`. That plan fixes the exact bar horizon, outcome
horizon, positive threshold, feature parameters and Decimal precision before
an outcome is read. The label function rejects any later change to those
definitions. It revalidates the source rows and replays their
features under the plan's fixed Decimal context, then compares the entire
recomputed snapshot and digest with the supplied snapshot. The snapshot must
have `as_of=decision_at` and `data_cutoff=base_bar_closed_at`; a changed source,
feature plan, instrument, missing bar, late base receipt or unrevealed outcome
is rejected. The resulting label binds the base and outcome row hashes, the
feature-source hash, replay hash, and plan hash. Neither the function nor a
caller-created plan hash authenticates raw exchange bytes or independent custody
of the pin. A controlled receipt journal and protected checkpoint remain
prerequisites for a real predictive dataset. The label is always offline-only,
has zero runtime consumers and grants no execution authority.

For a future fixed multi-instrument plan,
`grouped_purged_walk_forward_folds` splits complete event-time groups rather than
individual symbol rows. Every declared symbol must occur exactly once at each
UTC event time in canonical order; missing, duplicate, extra or reordered source
rows fail. The verifier recomputes exact row membership from the frozen plan,
including cross-symbol purge and embargo. This is an offline primitive only; the
current rehearsal has not fitted a candidate or evaluated a sealed holdout.

The standalone walk-forward splitter accepts caller-declared dependency lengths;
it remains computational-only and cannot authorize a predictive claim. A caller
could otherwise declare a 15-minute label dependency while using a frozen
four-hour outcome: the last training label would mature inside validation.
`replay_plan_bound_walk_forward_folds` requires the exact
`FrozenFeatureReplayPlanV2` and an independently retained canonical plan SHA256.
Before creating folds it requires the declared feature dependency to cover
`history_bars * bar_horizon.seconds`, the label dependency to cover the frozen
outcome horizon, and both purge and embargo to cover the larger requirement.
The immutable result binds the plan SHA256 and canonical source-timestamp SHA256
to the fold membership, while retaining `predictive_oos_eligible=false`, zero
runtime consumers and no execution authority. No candidate-selection or Gate 3
promotion caller currently consumes this result; a future promotion path must
enforce this binding and independently verify plan custody and source provenance.

## Actual public archive rehearsal

The script below verifies the previously frozen public preparation identities,
then opens only the 120 BTC/ETH Development and Validation archives. It never
opens a holdout ZIP or reads daily descriptive summaries. It binds original
acquisition-receipt hashes to derived conservative observation receipts, builds
the complete batch, aggregates every partition/instrument twice, and checks that
historical feature cutoffs fail because the source was retrieved later. It
publishes canonical artifacts with exclusive file creation and actual readback;
the final summary is written last. A failed late publication leaves earlier
evidence available for diagnosis.

```powershell
python -B -m scripts.replay_gate3_development_validation `
  --dataset-root <existing-public-dataset-root> `
  --output <new-output-directory>
```

The newly created `archive-plan.json` records its actual creation time and is a
computational rehearsal plan. It is not backdated or described as a historical
candidate preregistration. Re-run exactly the same plan with its independently
retained canonical SHA256:

```powershell
python -B -m scripts.replay_gate3_development_validation `
  --dataset-root <existing-public-dataset-root> `
  --plan <first-output-directory>/archive-plan.json `
  --expected-plan-sha256 <retained-plan-sha256> `
  --output <another-new-output-directory>
```

The already exposed July 23–August 21, 2026 partition stays ineligible for
predictive OOS. Its calendar boundary appears in the plan solely to preserve the
existing split contract. Independent historical row-availability evidence, actual
candidate development/trial records, a candidate seal before fresh heldout access,
and formal OOS acceptance remain required before Gate 4.

## Bounded Development/Validation replay after an interrupted full run

The full 120-archive rehearsal previously stopped after publishing its plan and
manifest under a laptop thermal precaution. A retained plan and manifest may be
reused only with their separately recorded canonical SHA-256 pins. The one-group
mode still checks all 120 original archive bytes and rebuilds the **whole**
Development/Validation manifest, then aggregates just one selected partition
and symbol in a new output directory. It never opens a holdout archive or fits
a candidate. A changed source, plan, or manifest stops before output creation.
Run one group at a time, allowing the host to cool between invocations:

```powershell
python -B -m scripts.replay_gate3_development_validation `
  --dataset-root <existing-public-dataset-root> `
  --plan <retained-archive-plan.json> `
  --expected-plan-sha256 <independently-retained-plan-sha256> `
  --expected-manifest-sha256 <independently-retained-manifest-sha256> `
  --group-partition development --group-symbol BTCUSDT `
  --output <new-development-BTCUSDT-directory>
```

Repeat with `development/ETHUSDT`, `validation/BTCUSDT`, and
`validation/ETHUSDT`, each to a different new directory. Every group repeats
aggregation from the same source rows and records both output hashes; all three
timeframes must reject the old historical cutoff because the archives were
observed later. A crash leaves that group's partial directory unresolved;
do not overwrite it or interpret other groups as a complete batch.

After retaining each `summary.json` SHA-256 **outside** its output directory,
the read-only four-group verifier checks exact group coverage, external plan,
manifest, and summary pins, source/aggregate file hashes, bar coordinates,
each bar's ordered constituent receipt hashes and maximum retrieval time
against the pinned manifest, late-cutoff rejection, and zero authority. Its
final report uses exclusive file creation and readback:

```powershell
python -B -m scripts.verify_gate3_development_validation_groups `
  --plan <retained-archive-plan.json> `
  --expected-plan-sha256 <independently-retained-plan-sha256> `
  --manifest <retained-archive-manifest.json> `
  --expected-manifest-sha256 <independently-retained-manifest-sha256> `
  --group-output <development-BTCUSDT-directory> `
  --expected-summary-sha256 <retained-development-BTCUSDT-summary-sha256> `
  --group-output <development-ETHUSDT-directory> `
  --expected-summary-sha256 <retained-development-ETHUSDT-summary-sha256> `
  --group-output <validation-BTCUSDT-directory> `
  --expected-summary-sha256 <retained-validation-BTCUSDT-summary-sha256> `
  --group-output <validation-ETHUSDT-directory> `
  --expected-summary-sha256 <retained-validation-ETHUSDT-summary-sha256> `
  --output <new-four-group-report.json>
```

This verifier checks pinned outputs and their manifest receipt coordinates; it
does not reopen the source ZIPs or independently authenticate the receipts.
This is a reproducible **computational rehearsal** of already retrieved
archives. The old 2024/2025 archive rows have no measured historical first
receipt, and the retrospective holdout was exposed before candidate sealing.
The verifier does not reauthenticate source transport or establish independent
custody of pins. Point-in-time predictive eligibility, sealed OOS, Gate 4,
execution, and Live acceptance remain false. Focused tests use synthetic
manifests and aggregate outputs; no full historical replay is part of CI.
