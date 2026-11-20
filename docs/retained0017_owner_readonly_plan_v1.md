# Retained0017 read-only source upgrade plan

This is a read-only inventory tool, not an upgrade installer. It connects only
when explicitly invoked with separately reviewed private inputs. It never
starts PostgreSQL, Docker, WSL, an API, a collector, a scheduler or trading.
No claim is made that the current host database is at0017. The historical
preservation receipt is historical evidence; `PG_VERSION` only identifies a
PostgreSQL major version, not an Alembic revision or reconciled account.

## Smallest safe retained-state route

Use a verified isolated clone of the preserved database while keeping the
original backend and legacy API stopped. Preserve the backup and source
identity independently. The same cloned database must retain the old Demo
state, ordinary/algo mirrors, order/fill/history, qualification ledger and
legacy Live intents/latches throughout. A fresh empty58 database cannot stand
in for those records or prove that the account has no unresolved submission.
A separate backend can coexist only while it is a verified continuation of
the retained ledger and the old application cannot write against that account.

The existing reviewed chain begins at actual55:

1. A separately reviewed retained0017→55 operation is still required.
2. Use the existing fixed42-grant physical55 provisioner, then the reviewed
   owner55→56 migration. Physical56 also has its separate ungranted42-grant route.
3. Use reviewed owner56→57 (adds exactly two G12 grants), then57→58 (adds no
   Demo grants), retaining every old row and role/catalog check.
4. Configure the exact Windows58 package with its private TLS CA, three
   restricted Demo DSNs and credential settings. The configuration wizard does
   not create a backend, TLS certificates or roles.
5. Genuine current account ingestion and complete retained-state reconciliation
   remain mandatory. No installer Arms or resolves an ambiguous order.

The historical Compose template calls generic `alembic upgrade0055`; it assumes
the retained volume, TLS and restricted login roles already exist. It is not a
safe retained-clone/TLS/role installer. The only in-repository TLS generator is
for a disposable hermetic test lab and must not become a production initializer.
Offline read-only VHDX/ExtFS metadata inspection of a verified backup may help
identify the correct volume without starting the original engine. It cannot
prove SQL head, source/schema equivalence, account identity or flatness.

## Read-only command and private inputs

Run `python -B -m scripts.retained0017_owner_plan_v1` with
`--inspect-retained-clone`, `--reviewed-pins-file`, `--reviewed-pins-sha256`,
`--owner-dsn-file`, `--ca-file` and `--source-archive-file`. DSN/password values
are never command-line arguments. The source archive/commit/tree/manifest and
the actual58 migration inventory are checked using existing owner utilities.
The actual owner must connect directly with hostname-verified pinned TLS to
the reviewed PostgreSQL17 cluster/database/owner and genuinely observe0017.
Settings files and ambient database defaults are not used.

Private pins have schema `ctcc.retained0017.readonly_owner_pins.v1` and exact keys
`schema`, `source_commit`, `source_tree`, `source_archive_sha256`,
`preserved_backup_sha256`, `route`, and `expected_catalog_sha256`.
`route` contains `source_manifest_sha256`, `ca_sha256`,
`cluster_system_identifier_sha256`, `host`, `port`, `database`, `owner`,
`database_oid`, `owner_oid`, and `database_head` (exactly `0017`).
The optional expected catalog pin is `null` for unreviewed discovery. A measured
catalog is never silently adopted as the expected baseline. Even a match is
only DATA and does not authenticate who reviewed the supplied pin.
The source-derived table/column minimum is explicitly separate from complete
source17 schema verification; `full_source17_schema_verified` remains false.

The tool takes two independent READ ONLY/REPEATABLE READ transactions and
explicitly rolls each back. It records all original public tables and columns
derived without executing migration bodies, physical relation OIDs, owner,
RLS/partition/persistence flags, complete catalog hash, actual sequence
last-value/called state, counts/byte budgets and deterministic SHA256 row
multisets. Each row binds the complete native JSON row and an original-column
SQL-null bitmap, distinguishing SQLNULL from a JSONB `null` value. Row hashes
sort with explicit C collation; duplicate rows remain duplicated. The complete
catalog covers attributes/defaults, constraints, indexes, triggers, function
metadata/bodies/ACL, types/enums, sequence definitions, relation dependencies,
schema/database/default ACL and owner/membership/settings metadata.
Missing/extra tables or columns, unexpected owner/RLS/storage, wrong actual
head, changed sequence, different independent reads, capacity excess or a
reviewed-catalog mismatch refuses. No row value is filled, deleted, normalized,
reconciled or projected into a different revision.
The inventory is the connected database's source17 public schema, not a full
cluster backup. Other user schemas, large objects, foreign tables, public views,
inheritance/partitions, row-level security and non-native column types refuse instead of
being treated as absent. Catalog-only checks precede selecting the version
marker, so an unreviewed replacement view is not invoked during discovery.

`--private-receipt-directory` optionally publishes the complete DATA receipt
into an already private directory, with no clobber and a fresh native readback.
Raw rows are never exported; only metadata, counts, OIDs and row hashes are
retained. Stdout contains only fixed status, hashes and counts. Existing or
partially accepted artifacts are never deleted after a late failure.

Successful observation returns exit2 (`OBSERVED_UNREVIEWED_CATALOG_DATA` or
`OBSERVED_MATCH_DATA`), with admission DENY. Refusal returns exit1 and a fixed
redacted error. `exchange_flat=null`, account completeness, execution and schema
upgrade authority are false. A backup digest input does not prove that the
connected database came from that backup; `backup_to_database_lineage_verified`
stays false. Two equal reads do not exclude a concurrent writer that changes
and restores data; `concurrent_writers_excluded` stays false. A future migration
must establish those facts under its own exclusive maintenance boundary.

## Remaining retained migration policy requiring review

Generic Alembic applies the exact migration chain transactionally, including
0027's refusal of unreviewed legacy terminal reservation state. It does not
provide retained-clone identity, all-old-row comparison, rollback rehearsal,
durable one-shot attempt journal, independent commit readback, TLS/role setup,
or a reviewed policy for old singleton state.

The sole version marker necessarily changes from0017 to0055. Separately,0034
adds revision0/restart-latch columns and changes only the known negative
control fields: Arm false, EStop/locked true, and appends
`control_protocol_upgrade_requires_clear`. Every other original column,
history/counter/risk/intent/exposure must remain byte-semantically preserved.
The known allowance is recorded by this plan; it does not approve or apply a
transition to the actual retained row. Malformed old `lock_reasons` must refuse,
never become an empty list or be normalized to satisfy the migration.
Before any0017→55 mutation, the owner tool must rehearse in a savepoint, compare
all original-column row multisets/OIDs/sequences and the exact allowed0034
change, roll back to exact17, then commit at most once and independently read
back. Unknown commit must never cause automatic retry, downgrade or cleanup.

There is a second concrete transition gap: existing56→57 owner installation,
manual startup and source-only control inspection require legacy revision≥1;
0034 gives the retained row revision0. Empty-genesis initializers correctly
refuse preserved runs/orders/history. Default legacy `recover()` also normalizes
other state and is not an upgrade workaround. A separately reviewed negative
retained-state preparation must bridge that state while preserving all risk,
history, exposure and unknown blockers. Existing restart-clear further requires
revision≥2 and its exact restart reason set, so the known migration-stop reason
needs an explicit audited acknowledgement procedure after complete genuine
reconciliation. Unknown reasons must remain blockers. No such mutation or
clear-stop procedure is implemented by this read-only tool.

## Validation scope

Unit tests cover the actual source plan, closed pins, identity/head projection
refusals, hidden/missing/extra schema, capacity, independent drift, private
no-clobber readback, secret redaction and DATA-only semantics. Authored actual
TLS PostgreSQL tests create a disposable database genuinely at0017 with synthetic
retained losses/history and an ambiguous Live intent. They exercise unchanged
rows/OIDs/sequences, actual-head/schema refusals and independent row/sequence/ACL
drift. Local collection is not their execution or any actual host deployment.
One additional outer CLI case uses the existing admitted runner identity and
original exact source archive, private real input files, verified TLS, two
fresh sessions and independent native receipt readback. It has no source,
archive or transport-authentication stub. The isolated database still uses the
explicit hermetic trust-auth lab; it makes no password-authentication or real
backup/account lineage claim. This case is authored for that hosted component
and is not represented as locally executed.
