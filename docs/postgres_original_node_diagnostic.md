# Fixed PostgreSQL original-node diagnostics

The two closed profiles in `scripts.diagnose_runtime_custody_postgres` run the
existing original test identities, one per invocation and fresh isolated PG17
cluster. They are diagnostics, not PostgreSQL full-suite acceptance or native
schema admission:

| Profile | Original test identity |
| --- | --- |
| `partition-parent-writer-nowait-current` | `test_account_history_partition_repository.py::test_partition_parent_writer_nowait_refusal_retains_head_and_rows` |
| `submission-transport-ack-0041` | `test_submission_transport_denial_migration.py::test_new_acknowledged_response_is_retained_only_as_ineligible_evidence[0041]` |

The partition test includes the separately declared narrow setup repair. Its
invalid no-op scope UPDATE becomes a real ROW EXCLUSIVE table lock, proven
granted in `pg_locks`, with a distinct migration backend. The existing migration
refusal, native `55P03` and complete retained snapshot assertions remain. This
does not retrospectively recover the missing traceback from the old eff413 run.

The producer reuses exact Git archive/tree and dependency verification, a fresh
source COPY build, pinned PostgreSQL17 image, no host environment or credentials,
an internal-only runtime network and `scripts.hermetic_pytest` execution OFF.
The contained case Docker CLI has the existing 300-second deadline. No timeout
is increased. A timed-out CLI can leave its owned container alive until bounded
settlement; the producer does not claim a universal 300-second process lifetime.
Such a timeout can never become PASS even if a late JUnit appears.

The passive pytest plugin fsyncs node start and setup/call/teardown starts and
reports immediately. It does not replace test bodies, fixtures or SQL. The
existing pytest faulthandler produces a stack dump after30seconds. The original
case runs with long traceback output and no `--showlocals`. A final summary is
not required for the early journal to survive, but missing/partial final JUnit
cannot pass diagnostic completion.

A contained observer uses read-only PostgreSQL transactions every2seconds and
retains bounded native `pg_stat_activity`, `pg_locks` and `pg_blocking_pids()`
observations. The parent and generated fixture child databases are included.
Database OID/name and relation OID are retained together. Child relation OIDs
are never resolved as names in the parent database. Empty, unavailable, malformed
or oversized observations remain incomplete; no zero-filled result is invented.

After the case returns or times out, one final read-only observation and the
owned PostgreSQL server log are captured. The final observer and ALL resource
settlement share the existing single90second cleanup deadline. No new deadline
is allocated after failure, removal acknowledgement or timeout. Actual run-label
ownership is checked, resources are removed, and independent listings must
prove every owned writer/container/network absent. An `rm` acknowledgement is
not absence. A failed absence proof refuses all raw log/artifact inventory reads.

Only after that barrier are fixed artifact names inspected with the existing
single-link path/FD stable-read checks. Source migration identity, exact original
node, one-case JUnit, phases, distinct reports and lock snapshots must agree.
Recognizable private headers, token forms or credential-bearing URLs refuse raw
export. Safe original failure tracebacks remain forensic evidence with FAILED
outcome; missing reports remain INCOMPLETE. Export files are created without
replacement and read back; late mutations cannot receive a stable forensic
inventory. Partial private/source evidence is not deleted to make a run pass.

`full_postgresql_acceptance`, `native_baseline_admission`, `g6_qualified` and
`execution_authority` remain false. DB0050's existing seal is untouched, and
DB0051 remains None/UNSEALED. No schema, account configuration, trading route,
Arm or exchange POST is changed by these diagnostics.

The dedicated `postgres-original-node-diagnostic.yml` workflow runs only on
`work/prospective-event-cutover-pg51-catalog-20261025`, with two fixed matrix
entries. The existing catalog-capture workflow remains unchanged, so the same
reviewed source push can produce capture and diagnostic evidence. Only the
exact source TAR and checked `export/*` files are uploaded. Unchecked raw worker
directories and private logs are not uploaded.
