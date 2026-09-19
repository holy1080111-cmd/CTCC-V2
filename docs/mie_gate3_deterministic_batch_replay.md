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
arithmetic, independently of the caller's Decimal context. The source volume unit
remains explicit; the archive adapter labels it `source_unspecified` until a
separate source-unit contract establishes a unit. It never calls volume USD or
contracts by inference.

Measured receipt, archive observation and assumed close are separate source
bases. None of these caller-supplied attestations independently authenticates
acquisition. Every plan and output therefore remains `computational`,
`predictive_oos_eligible=false`, `execution_authority=false`, and
`runtime_consumers=0`. Archive retrieval times remain unchanged. A historical
feature cutoff before those retrievals fails through the existing replay gate.

`replay_aggregated_features_at` rebuilds aggregates from the original bound minute
records and independently retained hashes before invoking the existing MIE
feature engine. Matching output schema alone cannot pass original-source replay.
The existing purged walk-forward splitter consumes the aggregate close times and
continues enforcing its dependency, purge and embargo rules. No candidate,
probability model, fitted trial, cost schedule or economic threshold is selected
by this adapter.

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
