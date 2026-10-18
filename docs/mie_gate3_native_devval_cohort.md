# Gate 3 native Development/Validation pilot (DENY only)

`app.mie.validation.native_devval_cohort_v1` joins exactly four source-owned
DB0035 V3 minute batches in fixed order: Development and Validation, each
with the same two pinned instruments. Each batch must cover one complete,
four-hour-aligned 240-minute window. The two symbols in a partition share the
same event-time grid; Development must precede Validation. This is a bounded
source-admission pilot, not a full research dataset or Candidate V1 seal.

The caller supplies a canonical cohort plan and its separately retained SHA-256
pin, each original batch artifact and its pin, all original capture inputs,
the controlled public journals, and the V3 observation repository. Before
source reads, the adapter checks exact group order, coordinates, unit, source
counts, duplicate pins and byte budgets. It invokes the existing V3 verifier
for every group; that verifier replays original journal, witness and DB0035
observation evidence. The cohort then rejects capture IDs, row identities and
witness revisions reused across groups, re-reads all journal inventories, and
records each group's source digest, terminal row hashes, latest persisted
observation and rows observed after the plan's fixed cutoff. A later readback
rebuilds the entire result from those original inputs and compares canonical
bytes. The adapter does not sort, deduplicate, fill, change a timestamp, or
accept a caller `point_in_time_provenance` flag.

Even a fully matching local replay has `status=DENY`,
`point_in_time_provenance_qualified=false`, no candidate-seal or formal
evaluation eligibility, no predictive or promotion eligibility, zero runtime
consumers, and no execution authority. DB0035 server-clock trust, independent
pin/journal custody and first evaluator access have not been established.
The fixed one-window-per-partition scope cannot fit or evaluate a candidate.
Late observations add an explicit denial reason; missing, reordered, repeated,
conflicting or changed source material fails closed.

Focused tests use synthetic V3 batches and a substituted verifier to exercise
the cohort boundary; existing V3 tests exercise its original-source verifier.
No real four-group native cohort, protected clock/custodian, sealed candidate,
unexposed OOS window or formal evaluation is created by this module.
