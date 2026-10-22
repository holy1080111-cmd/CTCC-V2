# Owned Stage1 original retention V6

V6 is an opt-in, separate finite source/availability owner. It retains the
original bounded native Stage1 health payloads and the actual typed SQL-driver
sample before the existing V4 acceptance checks. No production caller is
switched to V6. The existing V4 public API, DTO, source burn, leases, calculation
and internal carrier remain unchanged for callers without retention. V5 saved
schemas and readers remain unchanged; they reject a V6 store.

The unretained V4 driver is extracted behind an immediate same-task await. Its
effective unretained body and every other original V4 module node have an exact
AST comparison to the DC79 source. Existing V4 and V5 regressions remain separate
from the new V6 tests. Their synthetic/native seams do not prove a real Windows
clock, PostgreSQL deployment, public capture or historical availability.

## Original acquisition and retention

The finite owner exclusively publishes and reads its original ordinal claim
before its first await. It registers an opaque, nontransferable retention owner
bound to that exact claim, source service, original journal identity, task, native
root and original acquisition deadline. It can start only after the real native
V3 source carrier is consumed. It accepts no caller path, callback, saved DTO,
raw packet or claimed success flag as acquisition authority.

`native_post_read_clock_v4` has one fixed retained driver branch:

1. Acquire the original bounded health bytes once, retain and read them back,
   then run the original V4 health validator.
2. Verify the original witness and DB0035 observation through the existing
   privately cloned native sessions and fixed queries.
3. Stamp A, await only the fixed sample SQL, materialize at most two mappings,
   and attempt stamp B. No retention, filesystem check, extra await or caller
   callback is inserted inside this interval.
4. After the B attempt, retain A/B, the observed decoded cells and the exact
   phase/completeness facts. Then perform the original sample identity, time,
   health and V4 contract validation.
5. Retain the original second health bytes before their validation. Complete
   the forensic transcript only if the unchanged V4 contract validates.

Two observed rows mean `at_least_two` with `rows_may_be_truncated=true`; this is
a bounded invalid sample, never a complete valid one-row result. Zero rows,
missing columns, null, unsupported driver types, booleans in integer columns,
SQL failure and failed A/B stamps remain distinct. SQL-driver values are typed
decoded observations, not PostgreSQL wire bytes. No value is filled, coerced or
rendered through `repr`/`default=str`. Exception strings are never retained.

## Exact private inventory and failure behavior

A V6 slot has its original claim, a fixed `stage1/` directory and the existing
three V6 availability files. The only Stage1 names are:

- `attempt.json`
- `health-before.raw`
- `sample.json`
- `health-after.raw`
- `outcome.json`

Health bytes are bounded to 512 KiB each, the typed sample to 128 KiB and the
canonical originals pack to 2 MiB. Canonical replay rejects duplicate/unknown
keys, foreign schemas, type aliases, impossible phase/flag combinations,
unknown file names and missing success operands. Raw rejected health is retained
as raw bytes, not rewritten as a new accepted observation.

The original task may perform a narrow evidence-only finalizer after cancellation
or budget expiry. It cannot acquire another sample, refresh a lease, repair a
timestamp or issue a carrier. Failed or uncertain writes are never retried or
overwritten. Existing journals, successful files and tombstones are not removed.
`durability_unconfirmed_files` records original files whose write acknowledgement
was not returned. The existing publisher's platform behavior still applies:
Windows can retain a final-name partial file; POSIX can discard only its own
unpublished temporary name. This does not erase an accepted source journal.

If `outcome.json` bytes were written and a later flush/readback fails, the saved
document may describe a completed V4 calculation. That document is replayable
data only. The attempt remains failed/uncertain, no V4 or retained handoff is
issued, no V6 availability capability exists and no restart may refill it.
`inspect_partial_forensics_v6` reads an existing exact root without writing or
initializing it; its observations have no execution or replay authority.

## Successful custody and remaining scope

The V6 raw bundle binds the canonical originals-pack hash, all original bytes,
the unchanged V4 source/clock lineage and the actual ordinal claim. Its pure
validator cross-checks raw health, original A/B and typed decoded sample cells
against the original V4 contract. The exact bundle bytes go through the existing
DB0046 append and separate-session readback. The future-decision capability is
issued only after that custody, original local file-pin checks, fresh final
native health and the unchanged one-use consumer lease. The live pin read and
consumer re-read the fixed local receipt; a changed transcript burns access.

Failed Stage1 originals have local flush/readback evidence only, not DB0046
custody. V6 does not prove independent storage administration, host-binary
identity, absolute UTC, evaluator first access, historical bar-close availability,
Gate3/OOS eligibility, qualification or execution authority. Real native V6
acceptance is pending. The earlier V5 failed-raw forensic gap remains explicit
for V5; this new implementation does not retroactively repair old evidence.

Focused tests use explicit synthetic source/clock/catalog/SQL/DB0046 seams and
the real fixed native-owner/retention/file-publisher path. They cover successful
binding and one-use consumption, before/after rejected health, zero/missing/null/
wrong-identity/wrong-time/unsupported/duplicate samples, failed A/B, real task
cancellation, expiry, late publication and underlying flush failures, read-only
partial restart, forged owners, rehashed transcript damage and local tampering.
