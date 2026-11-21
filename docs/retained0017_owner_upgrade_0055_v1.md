# Retained 0017 to stopped 0055 owner path

`scripts.retained0017_owner_upgrade_0055_v1` is a new, explicit owner-maintenance
step before the existing canonical 55→56→57→58 installers. It never selects an
empty replacement database, resolves an old intent, changes leverage, grants a
runtime role, starts a service, clears Stop, Arms, or calls a venue.

The original source17 catalog literal is **SEALED** from two independently
reviewed actual PG17 references. That distinct-owner V1 purpose remains
unchanged. The separately reviewed bootstrap-owner10 V2 catalog purpose is
read-only DATA and does not silently replace the V1 reader or grant migration
authority. Two equal artifacts, caller-provided hashes and saved receipts
cannot install a new source seal or select a different reference purpose.
An explicitly pending/empty source seal still refuses before any connection.

Apply also has an independent **LINEAGE PENDING** hard refusal at both its
public entry and internal commit boundary, before opening a connection. The
backup hash, selected SQL system identifier, database/owner OIDs and honest
`backup_to_database_lineage_verified=false` receipt cannot prove ancestry from
the preserved cold backup. Until the actual full-file copy/control/selected
datadir/controlled-TLS-bootstrap custody chain is wired and independently
reviewed, even a sealed schema reference cannot enable DDL. There is no caller
flag or report that enables this boundary. Read-only DATA behavior is unchanged.

The private v1 input embeds the existing read-only retained17 pins and adds the
exact `actor_physical.maintenance_settings_sha256` from the reviewed raw source17
catalog. It binds source/archive/manifest, actual cluster/database/owner route,
CA and a separately preserved backup identifier. The backup identifier is DATA;
this tool cannot prove file-copy custody or backup-to-database lineage merely
from that hash. Actual deployment must retain that independent custody proof.
No password, DSN or raw retained row is printed.
If the embedded read-only pins include their optional legacy diagnostic catalog
digest, it must also match the exact original encoding after full sealed17
admission. A matching optional digest never replaces that mandatory baseline;
`null` retains the original documented absence of an extra diagnostic pin.

Without `--confirm`, the command performs two fresh read-only eligibility
observations. With the exact fixed phrase
`UPGRADE PRESERVED CTCC DB0017 TO STOPPED DB0055`, it performs one bounded owner
transaction on that same explicitly pinned database. Frozen0050 requires the
actual current/session database owner to be a superuser. The tool neither
grants nor removes that privilege, and the non-superuser reference fixtures do
not change the maintenance-owner policy.

The transaction description below is implemented engineering behavior whose
apply remains unconditionally blocked by the mandatory live-custody lineage
fence. The 15 authored owner-workflow PostgreSQL cases and five separate
negative-Stop cases remain required and pending; they are neither passed nor
skipped. Enabling a bootstrap-owner route still requires an explicit reviewed
source-purpose binding and a genuine supervised producer, not a saved report.

Before opening a session, fixed driver startup settings disable session/local
preload libraries, SET ROLE, public search paths and default writes. They bound
statement, lock and idle-transaction timeouts. The original stored DB/owner
defaults are then checked against source-approved name/value predicates and
the separately reviewed digest: overriding a bad preload setting at startup
does not make its original policy acceptable. Effective heap, tablespace,
replication, RLS, function-body, all preload, path and timezone policy also
must match. Other client sessions refuse. The tool retains the existing owner advisory lock, legacy
Live lock1680010, exclusive version-marker lock and all original table locks.
Native storage/index/type admission precedes every application-head/row path.
Actual automation/legacy intent/position/order state is read before eligibility;
empty schema or matching revision never proves exchange flatness. A reservation
in any source17 state refuses: unresolved states are blocked, and frozen0027
independently refuses every old `reconciled_flat` record. No record is erased or
rewritten to make the migration pass.

The old-column multiset projects the original columns **before** whole-row JSON
encoding and uses the shared v2 SQL-null bitmap. Duplicate rows and canonical C
ordering remain part of the count/digest. Original table/column OIDs, definitions,
column ACLs, old sequences and all cluster role/membership/settings state are
retained. Only two state columns may append, from frozen0034. Its only permitted
old-row delta is disarm, Stop/lock true and exactly one appended
`control_protocol_upgrade_requires_clear` on singleton1. A malformed original
reason list refuses before the frozen migration can apply `COALESCE`; unknown
reasons, ordering and duplicates survive.

Only after sealed0017 catalog admission and locks does the fixed DDL phase use
`public, pg_catalog, pg_temp`, because historical CREATE statements are
unqualified. Original source capture stays `pg_catalog` only; metadata and
committed readbacks restore `pg_catalog, public, pg_temp` before any observer.
Unexpected stored defaults are rejected rather than changed to force admission.
This phase-specific setting does not alter any historical migration or the
existing55→58 routes.

The exact0018…0055 operation sequence is rehearsed inside a savepoint. Source
archive/bytes are verified before and after each application, the existing
source55 native catalog is checked, and all original rows are compared with the
fixed0034 projection. The savepoint must roll back to the exact0017 snapshot
before a second application. A same-database logical catalog fingerprint checks
the two applications; it is **not** an independent source55 schema baseline.
The source17 baseline, frozen operation sequence, session/hooks admission and
existing source55 catalog checks supply the separate source provenance. The
receipt explicitly leaves the independent whole-schema55 baseline flag false.

The old-sequence equality and new-table-empty checks are deliberate. Frozen
0018…0055 adds no SERIAL/IDENTITY column or sequence operation. Its only
SQLAlchemy `create_table`, in0043, uses `witness_sha256 VARCHAR(64)` as its
primary key; the other new table definitions also use explicit non-sequence
keys. The top-level0029 inventory INSERT reads only the schedule-pins table
created empty by0026 in this exact held sequence; INSERTs inside created
function bodies are definitions, not migration seed calls. The sole permitted
old application-row UPDATE is the explicit0034 negative control delta above.
These checks do not rewind counters, delete seed rows or treat a rehearsal
result as an expected source definition. Any future source change that creates
a sequence or initial row requires a separate reviewed policy change.

There is one COMMIT attempt. An exception or cancellation at that boundary is
terminal `COMMIT_UNKNOWN_DO_NOT_RETRY`. A returned COMMIT is followed by two new
NullPool sessions that verify the exact committed rows/catalog. A lost readback
is `COMMITTED_READBACK_REQUIRED`; it never reapplies DDL. Output remains DATA with
`execution_authority=false`, `account_complete=false`, `exchange_flat=null`.
The resulting legacy revision is0, with Stop and restart latch retained. A
separate, reviewed negative stop-reassertion procedure is required for the
revision0→1 bridge; this installer does not perform it automatically.

The new four projection PG cases exercise real source17 SQL/null and exact0034
behavior only. The separate eleven-case whole workflow suite includes genuine
source archive/private pins/TLS and an actual child CLI, rollback/null faults,
unknown real commit, lost readback, retained unresolved-intent refusal, and
session/local preload defaults that an uncontrolled connection really attempts
but the controlled startup blocks before rejecting their original policy. They
are authored/collectable; no collection or inert unit result is SQL acceptance.
