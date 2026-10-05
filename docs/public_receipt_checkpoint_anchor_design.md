# Public minute journal: independently protected checkpoint design

**Status: migration/repository/opt-in service seam implemented but not deployed
or PostgreSQL-role accepted. Independent protection is unverified.**
Restricted-role SQL execution, WAL/backup durability, collector-versus-database
OS isolation, native restart behavior, and cross-platform crash recovery have
not passed actual deployment acceptance. The service result therefore keeps
all three authority/protection flags false.
The existing `ControlledPublicReceiptJournal` validates a caller-supplied
`PublicJournalCheckpointV1` against the complete local directory. Its SHA-256
chain detects changed bytes when the latest checkpoint is already trusted. A
checkpoint copied beside the journal, saved in the same user's profile, or
encrypted with that same user's DPAPI key is not an independent trust anchor:
the old checkpoint and the journal tail could be rolled back together. The
paper-state `recovery_checkpoints` singleton is mutable and has another owner;
it cannot be reused as the public-source witness.

## Proposed minimal witness

Use a dedicated PostgreSQL **append-only witness**, on an independently
administered database and credential boundary. Migration `0024` creates the
table, immutable guards, CAS trigger, and two narrow security-definer
functions. It creates or grants **no** capture role. A deployment must use a
separate capture-service login with only `EXECUTE` on
`public_receipt_witness_append(jsonb)` and
`public_receipt_witness_read(text)`, schema `USAGE`, and no direct `SELECT`,
`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER`, `ALTER`, `CREATE`, or
ownership right on the witness table/schema. The function owner must not be
the collector's login; a separately administered `NOLOGIN` owner is preferred.
Migration/admin
credentials must not be present in the collector process. PostgreSQL durable
commit, WAL, backup/restore policy, role grants, and the effective connection
role need an actual deployment check; a test database using a superuser does
not prove this separation. A DBA or compromised host administrator remains
outside this witness's threat model.

The database files, WAL, and backups must be inaccessible for write to the
collector's OS principal. A PostgreSQL container launched with trust
authentication and a host user who can rewrite its volume is a test fixture,
not an independently protected production witness. The security-definer
functions need a fixed `search_path` and explicit grants with `PUBLIC`
execution revoked; otherwise search-path substitution or broad default grants
would defeat the role boundary.

Each immutable witness revision must bind the journal genesis SHA-256 and
`journal_id`, exact root device/inode, canonical checkpoint bytes and SHA-256,
previous witness-revision hash, monotonically increasing revision, operation
ID, transition kind, and committed state. The current checkpoint is the unique
highest contiguous revision, not a mutable sibling JSON file. A transaction
must take the journal-scoped PostgreSQL advisory lock, compare the expected
revision/hash, enforce the transition below, insert one new row, commit, then
read it back in a **new session and transaction**. The connection pool may
reuse the physical connection. An uncertain commit is resolved by
readback; until then the collector denies further acquisition. The capture
role must not be able to delete or replace historical revisions. Database
retention, restore, and WAL replay must preserve the complete witness chain;
restoring an older database alone makes a newer journal tail fail closed.

The insert guard accepts only these transitions:

| Transition | Required prior state | Allowed checkpoint change |
| --- | --- | --- |
| `open_attempt` | idle, exact journal replay against latest witness | none; persist one operation ID and pinned plan before public I/O |
| `append_attempt` | same open operation | attempt sequence exactly +1 and new attempt head; capture sequence/head unchanged |
| `append_capture` | completed attempt anchored for same operation | capture sequence exactly +1 and new capture head; attempt sequence/head unchanged; return idle |
| `close_rejected_attempt` | anchored rejected/incomplete terminal for same operation | checkpoint unchanged; return idle only when no unanchored files exist |

The guard also pins genesis/root identity throughout and rejects concurrent
operations, repeated operation IDs, revision gaps, stale expected hashes,
sequence jumps, and a return to an earlier state. Checking a new SHA-256 alone
is insufficient: the service must replay all original journal bytes before
asking the witness to advance. The witness prevents rollback or accidental
replacement of the *latest checkpoint*; journal replay remains responsible for
source-byte integrity and native-capture binding. Neither proves that an
exchange row was observable at a historical decision time without the row's
own measured causal receipt.

## Required journal integration

The opt-in `PublicCheckpointCaptureService` uses phase hooks in the existing
`collect_and_publish_public_minutes` call. It advances the independent witness
after the attempt and again after the capture, with separate session readback.
It is not wired to API startup, a scheduler, or a trading route. The service
must still be checked under a restricted production role and OS boundary:

1. Re-read the latest witness in a new session and replay the complete
   journal against it before public I/O. Reserve one operation with
   `open_attempt`; expose no caller-supplied checkpoint or DB callback as an
   authority token.
2. Run the owned attempt. After its terminal files and chain entry are
   published and read back, replay the proposed next checkpoint; commit and
   independently read back `append_attempt` **before** publishing a capture.
3. After the capture entry and all original bytes are published and read back,
   replay the next checkpoint; commit and independently read back
   `append_capture` before returning an accepted result. For a rejected
   terminal attempt, use `close_rejected_attempt` only after exact inventory
   replay.
4. Keep an in-process journal owner and a cross-process DB operation guard so
   two workers cannot write the same root concurrently. On restart, read the
   witness first, require the configured root to have its pinned identity, and
   call `resume_public_receipt_journal` with the witnessed checkpoint. Never
   discover a new head by scanning the journal directory.

No service startup, scheduler, Demo issuer, or order path invokes this
integration. The result fixes `independently_protected=false`,
`predictive_oos_eligible=false`, and `execution_authority=false`; reviewed
deployment evidence is needed before any stronger claim. A failed or unknown
DB commit never becomes a capture success.

## Crash and uncertainty rules

- If `open_attempt` committed but no journal bytes were written, the operation
  remains pending. A reviewed recovery may mark it abandoned only after a
  fresh exact replay finds no tail; this is an audit transition, not automatic
  retry.
- If files were written but an attempt/capture entry or witness revision is
  missing, retain **all** files. The inventory or witness mismatch blocks
  resume and future captures. Never remove an incomplete tail to make an old
  checkpoint appear valid.
- If a DB commit response is unknown, use a new session to read the exact
  operation and revision. A matching committed revision plus complete journal
  replay may be recognized as recorded; absent or conflicting proof remains
  unresolved. Do not repeat public acquisition under the same operation ID.
- If the DB is unavailable, its role/grants changed, the witness chain has a
  gap, the root identity changed, or the clock/source replay is untrusted,
  deny acquisition and predictive use. Preserve the prior evidence.

## Acceptance needed before use

Test fresh provisioning, migration upgrade/downgrade policy, effective role
grants, exact genesis/root pin, every transition, concurrent workers, stale
revision, duplicate operation, partial filesystem writes, DB timeout before
and after commit, process/container restart at each stage, witness backup and
restore, and a copied/truncated journal. Run the same tests against the final
PostgreSQL image with the **restricted capture role**, plus Windows and Linux
native journal replay. Inspect the exact committed source, manifest, migration
head, and service configuration. A local mock/store test is not deployment
acceptance.

Even after this witness is implemented, the existing historical Binance
downloads lack decision-time availability proof. Gate 3, sealed OOS, Demo
execution, and Live authorization remain separate fail-closed gates.
