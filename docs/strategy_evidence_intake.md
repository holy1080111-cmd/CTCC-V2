# Strategy Evidence Pack — offline metadata intake

This input boundary implements the first research-intake step from the
[123-project core specification](ctcc_core_master.md). It records what an
external strategy author claims and what supporting material is asserted to
be available. It does not calculate performance, verify an author's identity,
fetch a URL, execute strategy code, or establish CTCC validation.

## Separate records, explicit unknowns

The intake record separates a strategy profile, passive logic description,
source assertions, author performance claims, and a CTCC comparison placeholder.
Missing information remains null with a reason. An unknown source lead does
not require an invented URL, receipt timestamp, hash, trade count, or win rate.
The time a lead is recorded is separate from the time source material was
actually retrieved.

Known performance values require their metric definition, unit, test type,
sample window, sample count/unit, cost context, and a reference to a complete
source assertion. This is a structural requirement for recording the claim,
not a finding that the number is correct. Source and metric IDs must be unique;
references must resolve. Claimed samples cannot extend beyond source retrieval,
and retrieval cannot be later than the intake's recording time.

Trade win rate and profitable walk-forward-window rate have different sample
units and denominators. Promotion thresholds are not accepted as observed
performance. Costs retain their stated basis rather than silently treating
unreported fees, funding, spread, or slippage as zero. No cost-adjusted result
is computed by this module.

## E0–E6 means availability claims only

These labels have their own enum and do not map to MIE validation levels or
the earlier benchmark reproducibility enum. They are not a cumulative ladder
of truth, credibility, prediction quality, or execution permission.

| Label | Required material claim |
| --- | --- |
| E0 | A research lead or author claim; incomplete provenance is allowed with reasons |
| E1 | Backtest report |
| E2 | Trade records |
| E3 | Both reproduction code and input data |
| E4 | OOS report |
| E5 | Forward or paper records |
| E6 | Live records |

Non-E0 labels require complete source assertions for the indicated material
roles. Neither a hash supplied by the caller nor the presence of an E6 label
authenticates the original file, account, time, license, or author. Source
license status remains an assertion requiring its own review.

The CTCC comparison stays `not_performed`. Recomputed values, CTCC-E,
independent verification, predictive eligibility, automatic promotion, and
execution authority cannot be introduced through this intake. Real CTCC
reproduction will require a separate, source-bound evidence contract.

## Canonical metadata, not an executable five-file package

The intended roles `strategy_profile.json`, `logic.yaml`, `performance.json`,
`source_manifest.json`, and `ctcc_comparison.json` are descriptive roles.
`trades.csv` remains optional source material. This milestone does not create
those files, assert that they exist, parse YAML, or execute any downloaded code.
The passive logic text cannot become a runtime strategy by being ingested.

`freeze_strategy_evidence_pack` revalidates the exact immutable model and
returns canonical metadata bytes plus their SHA-256. Verification requires
those bounded bytes and an independently retained expected hash, rejects
noncanonical/duplicate-key encodings, and revalidates the model. The metadata
payload is capped at 256 KiB; collections and text fields are also bounded.

The metadata hash identifies this intake record only. It is not an archive
hash, proof of source-byte membership, acquisition receipt, proof of a license,
or proof of performance. No filesystem or network ingestion route is added.

## Acceptance boundary

Synthetic tests exercise valid unknown and complete records, tier/material
requirements, source references, metric context, finite numbers, chronology,
canonical serialization, tampering, and the fixed zero-authority fields.
No real research lead, original report, trade log, account, or holdout is opened.

Actual source qualification, license review, bytes-bound package assembly,
independent reproduction, cost reconciliation, OOS review, and any Demo/Live
promotion remain separate unfinished work. An intake pass alone grants none
of those qualifications.
