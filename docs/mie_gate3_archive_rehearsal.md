# MIE Gate 3 — Archive availability and offline rehearsal

This module makes the difference between event time and observed availability
explicit. It provides a computational-only archive rehearsal, not a new
predictive result or a historical first-receipt dataset.

## Implemented boundary

- Immutable, canonical availability metadata distinguishes measured row
  receipt, archive observation, and assumed bar-close availability. Declaring
  a basis is an attestation, not independent proof of that basis.
- An `ArchiveObservationReceipt` binds exact archive bytes, symbol, day,
  member, development/validation partition, and UTC observation/retrieval
  timestamps. Its supplied SHA-256 must match its canonical bytes.
- `load_binance_archive_rehearsal` accepts bytes only. It has no filesystem,
  HTTP, exchange, account, scheduler, or holdout acquisition path.
- The supported scope is one complete Binance USD-M daily 1-minute CSV for
  BTCUSDT or ETHUSDT, using millisecond timestamps and exactly 1,440 rows.
  The contract rejects holdout partition labels. These are caller-supplied
  labels, not a verified split plan: the loader cannot detect a holdout day
  falsely labelled development. No real batch may use this attestation alone
  as proof of partition eligibility.
  The separate [pinned batch adapter](mie_gate3_archive_batch.md) now checks
  exact development/validation calendar coordinates against an external plan
  hash before parsing any ZIP. It does not independently qualify acquisition.
- The loader checks ZIP safety and size, exact member identity, all numeric
  fields, OHLC/volume geometry, and the full ordered minute sequence. Binance
  inclusive close timestamps become end-exclusive feature-bar boundaries;
  raw input and source coordinates remain hash-bound.
- Each row is bound to its archive, member, ordinal, raw row contents, receipt,
  and availability. Dataset freeze, verification, and conversion additionally
  require the original archive bytes, rebuild the rows, and compare the whole
  canonical dataset. Schema consistency alone is not proof of ZIP membership.

Receipt and dataset freeze/verify helpers require exact canonical bytes and
SHA-256. Hash consistency detects changed inputs; it does not prove that a
caller-supplied observation timestamp is an independently recorded fact.

## Conservative availability, not fabricated historical timing

`conservative_archive_rows(dataset, archive_bytes=...)` first revalidates the
original ZIP and uses the archive retrieval timestamp for every row. A replay
cutoff before retrieval therefore rejects those rows. It never
substitutes the historical bar close for an unrecorded first-receipt time.

There is no assumed-close simulation conversion in this release. The
availability schema can record that assumption for audit, but the archive
adapter accepts only archive-observation provenance. The existing generic
replay engine remains usable with synthetic or separately qualified inputs;
it is not connected to a predictive promotion by this change.

Every new receipt, availability record, and dataset remains
`predictive_oos_eligible=false`, `runtime_consumers=0`, and
`execution_authority=false`. Canonical serialization cannot raise that claim.
No order geometry, API route, database migration, or runtime import is added.

## Acceptance and remaining work

Tests construct synthetic ZIP bytes in memory. No real public archive or
retrospective/future holdout is opened by these tests. Coverage includes
hash/count/time/member mismatches, malformed numerics, archive hazards,
partition rejection, tampering, conservative cutoff rejection, and canonical
round trips.

This remains a deliberately narrow single-day input adapter. Separate
computational modules now bind multiple archives to a pinned calendar plan
and link a prospective seal/receipt/report. Neither authenticates the external
acquisition chain, fits a real candidate, or supplies real prospective
evaluated evidence. The existing 180-artifact batch still lacks historical
per-row first-receipt proof and its exposed retrospective holdout remains
permanently computational-only.

Next: establish an independently qualified observation source and use a
reviewed development/validation batch plan for actual inputs, then candidate
fitting and real prospective evidence. Gate 4 stays blocked until the full
real evidence and independent review requirements pass.
