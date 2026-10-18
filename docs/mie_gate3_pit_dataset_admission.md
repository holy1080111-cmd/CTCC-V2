# Gate 3 historical archive research admission (DENY only)

`app.mie.validation.pit_dataset_admission` checks the original bytes of a
Development or Validation Binance archive batch before comparing any submitted
minute rows or a caller-created `DatasetIdentity`. It requires the retained
plan and manifest SHA-256 pins, rebuilds every ZIP member, verifies the source
row/receipt/availability chain, and records the exact partition-end cutoff.
The deterministic receipt is reread by rebuilding from the original ZIPs.
Holdout coordinates are excluded.

This audit cannot certify historical row availability. The archive adapter
uses each archive's actual retrieval time, not its bar close or a provider's
historical modification time. The 2024/2025 reference archives were retrieved
in 2026. A caller-supplied `DatasetIdentity.point_in_time_provenance=True`,
rehashed ZIP and manifest, or matching caller-supplied digest does not turn
those rows into measured historical receipts. Independent custody of the
retained pins is still unverified. The receipt therefore fixes `status=DENY`,
`candidate_seal_eligible=false`, `formal_evaluation_eligible=false`,
`predictive_oos_eligible=false`, and `execution_authority=false` even when
source bytes and submitted rows match exactly.

Source replay rejects changed ZIPs and plan/manifest pins. Comparison with the
replayed minutes records assumed-close substitution, absent/reordered/changed
rows, duplicate identities, a mismatched dataset identity, and observations
after the historical partition cutoff. It never sorts, deduplicates, fills a
row, alters a source timestamp, opens the holdout, or fits a candidate.

This is one partition/instrument audit and is **not** wired as a positive
candidate-seal or evaluator authority. The existing local prospective ledger
and controlled reader publish only computational artifacts with predictive
eligibility fixed false; they do not use this receipt to promote anything.
Before a real Candidate V1 seal, a future source-specific admission version
must replay complete two-symbol Development/Validation coverage from measured,
post-publication availability with independently protected clock, receipt and
first-access custody. The existing 180-artifact reference batch remains
computational-only and its exposed retrospective holdout remains ineligible.

The focused tests use synthetic ZIPs. They prove fail-closed behavior of this
engineering boundary, not data-source acceptance or a Gate 3 PASS.
