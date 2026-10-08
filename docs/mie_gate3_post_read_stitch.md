# Gate 3 DB0035 multi-batch computational replay

`app.mie.validation.post_read_stitch_v1` joins two to 32 contiguous V3 public
minute batches for one instrument and one Development or Validation partition.
The caller supplies the ordered batch identities and their SHA-256 chain from a
retained record outside the batch artifacts. Every freeze or verification call
passes each batch's original sources to `verify_post_read_minute_batch_v3`, which
replays the raw public journal, committed witness chain and DB0035 observation.
The stitch then checks all journal inventories again after the last batch.

Each batch has a complete UTC four-hour-aligned window, `contracts` volume,
and exact one-minute coordinates. The stitch rejects missing, repeated,
reordered, overlapping, gapped, cross-instrument or cross-partition batches and
source, capture or witness identities reused across batches. It concatenates
only source-reverified 15m, 1H or 4H bars. An aggregate is available at its
latest constituent's persisted DB0035 observation. If any bar due at the
requested cutoff was observed later, replay fails; no late bar is silently
discarded. The existing MIE feature engine must have at least 21 causal bars.
Six 960-minute batches can provide 24 four-hour bars; a single batch cannot
provide sufficient 1H or 4H history.

Before any journal or database read, the adapter requires each batch to carry
exactly its pinned minute count, enforces the V3 per-artifact byte limits, and
caps all batch and capture inputs together at 128 MiB. These are resource
limits, not evidence of source authenticity. The final per-journal inventory
reads are sequential rather than an atomic snapshot across journals; a real
promotion path needs independently protected custody and a consistent source
checkpoint.

The versioned output binds all batch identities, ordered-chain digest, due-row
digest, cutoff, exact feature-history length, and existing Gate 2 feature replay.
This adapter and the V3 volume aggregation fix the full Decimal arithmetic
context, including traps and exponent limits, so caller-local or modified
default settings cannot change their bytes. Two independent runs over
the same inputs must yield identical canonical bytes. Verification reads the
original sources again and compares rebuilt bytes to the frozen artifact.
Focused tests use synthetic V3 verifier substitutes to exercise the stitch;
the V3 module's own tests cover its original-source boundary. Real multi-batch
capture and PostgreSQL integration remain unexecuted.

This is **computational-only**. The batch pins are caller supplied and do not
establish independent custody. DB0035's clock has not been independently
certified; evaluator first access is unproven. Historical archives retrieved
after a purported decision cannot be made timely by this replay. Candidate
selection, sealed OOS, Gate 4, Demo and Live authority are unchanged. The
artifact fixes predictive, promotion and execution eligibility to false.
