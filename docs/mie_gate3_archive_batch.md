# MIE Gate 3 — Pinned development/validation archive batches

This is a bytes-only, computational rehearsal. It adds calendar-plan binding
above the [single-day archive adapter](mie_gate3_archive_rehearsal.md), whose
caller-supplied partition label alone cannot establish partition eligibility.
It does not download or qualify a real batch, fit a candidate, read a holdout,
or connect MIE to runtime execution.

## Frozen plan and exact input membership

`ArchiveBatchPlan` binds a `PurgedWalkForwardSplit`, sorted unique BTCUSDT /
ETHUSDT symbols, daily one-minute source format, exact artifact count, and
exact row count. All six split boundaries must be UTC midnight. Feature and
label dependencies, purge, and embargo must align to the minute cadence and
satisfy the existing split's separation rules.

The calendar determines every expected input coordinate, in this order:

1. Development, then validation.
2. Each source day in increasing order.
3. Each declared symbol in canonical order within that day.

Only those coordinates can be loaded. Missing, extra, duplicate, reordered,
wrong-symbol, wrong-partition, gap-day, and holdout-day inputs fail before any
ZIP is parsed. The holdout window is retained solely as an excluded calendar
boundary; it never contributes an input archive. Expansion is bounded to 256
daily artifacts, with existing per-archive size and 1,440-row limits.

Windows use **source-open** `[start, end)` semantics. The last source row opens
at 23:59; its normalized feature-bar close equals the next midnight and is
included. This does not loosen later replay cutoff or availability checks.

Freeze and independently retain the plan's SHA-256 before invoking batch
validation. Every load, freeze, verification, and conversion requires the
external `expected_plan_sha256`, rather than trusting a hash embedded in the
submitted manifest. Rehashing a replacement plan cannot satisfy the retained
original pin. A pin proves consistency with the selected plan, not the
identity or independence of its reviewer.

This plan can describe a retrospective rehearsal after acquisition. Its
`created_at` is not an independently witnessed preregistration timestamp and
must not be substituted for the separate prospective candidate/protocol seal.

## Original-byte verification, not a metadata-only assertion

`load_archive_batch_rehearsal` takes an exact tuple of `ArchiveBatchSource`
objects containing original ZIP bytes, an observation receipt, and its hash.
It first revalidates the plan and all metadata, then reconstructs every
single-day dataset using the existing strict ZIP/CSV parser.

The resulting `ArchiveBatchManifest` contains the plan, ordered receipts,
per-dataset hashes, total rows, and total archive bytes. Receipt hashes and
coordinates must be internally consistent and archive identities unique.
Schema validation alone does not prove that dataset hashes describe the ZIPs.

`freeze_archive_batch_manifest` and `verify_archive_batch_manifest` therefore
require **all original ZIP bytes** and the independent plan pin. They rebuild
the whole manifest and compare canonical bytes. A forged dataset digest is
rejected even when its enclosing JSON and hash have been fully recomputed.
There is no metadata-only verification or partial-batch acceptance path.

The contract includes no source URL fetcher, filesystem reader, exchange
connector, account access, or real archive fixture. Authenticating acquisition
receipts against an independent source remains a separate requirement.

## Conservative conversion and separate instruments

`conservative_batch_rows` requires both one explicit development/validation
partition and one declared instrument. It verifies the **entire batch** first,
even if corruption is in an unselected partition or instrument. It then returns
only the selected chronological source rows with unique receipt/ordinal IDs.

Every returned row preserves its own archive's `retrieved_at`. Retrieval order
may differ from source-day order; no shared earlier timestamp, assumed bar
close, or artificial historical first receipt is introduced. A historical
cutoff before retrieval still cannot see those rows. Different partitions or
instruments are not merged into one replay series.

Plans and manifests have `current_claim=computational`,
`predictive_oos_eligible=false`, `runtime_consumers=0`, and
`execution_authority=false`. Manifests additionally fix
`point_in_time_provenance=false`. No batch consistency result can raise these
claims or repair an already exposed holdout.

## Acceptance and remaining work

Tests use only in-memory synthetic daily ZIPs. They cover calendar bounds,
coordinate rejection before parsing, independent pins, a complete multi-day /
multi-symbol round trip, original-byte tampering, fully rehashed forgeries,
canonical JSON, authority escalation, and per-receipt availability retention.

The offline verifier includes both batch test modules and the zero-authority
contract probe. A local synthetic test pass is not a Docker deployment result.

Still pending: independently qualified row availability/acquisition evidence,
review and actual integration of a real development/validation batch, candidate
fitting and trial accounting, a real frozen prospective seal, fresh holdout,
one formal evaluation, and independent review. The existing exposed public
holdout remains permanently computational-only. Gate 4 and all execution
promotion remain separate, unavailable gates until their evidence is accepted.
