# DB0057 G12 restricted-writer successor

This source introduces a successor migration and an inert physical catalog/ACL
observer. It has not been installed, has no startup admission and grants no
execution authority. The existing DB0056 profile remains exact and unchanged.

## Migration

DB0057 replaces only the `FOR KEY SHARE` claim lookup in
`g12_invocation_publication_guard` with an ordinary `SELECT`. The source hash of
the complete function before replacement is checked in the same transaction.
Every validation predicate and the original claim/publication formats remain
unchanged. The function remains SECURITY INVOKER, owned by the original table
owner, with fixed `pg_catalog,pg_temp` search path and owner-only EXECUTE ACL.
Both tables are locked with ACCESS EXCLUSIVE NOWAIT during the migration.

The original claim is immutable. Its primary/unique keys, publication foreign
key, immutable/no-truncate triggers and both insert guards remain installed.
An ordinary lookup therefore needs SELECT without adding UPDATE solely for a
row-locking clause. PostgreSQL documents the extra UPDATE requirement for
locking SELECT clauses and preservation of ownership/ACL on CREATE OR REPLACE.
See [PostgreSQL 17 SELECT](https://www.postgresql.org/docs/17/sql-select.html) and
[CREATE FUNCTION](https://www.postgresql.org/docs/17/sql-createfunction.html).

The migration issues no GRANT and never changes retained rows. Downgrade
restores the exact previous body only if both journals are empty and the G12
tables have no nonowner grants. Retained custody blocks downgrade; evidence is
never deleted to make rollback succeed.

## Explicit successor profile

`current0057_native_g12_acl_v1` renders exactly two additional statements:
SELECT and INSERT on each G12 table for the configured writer. The inherited
42 statements remain the same, for a total of 44. Custody and frame readers
receive no G12 table access. The observer requires exact physical head 0057,
PostgreSQL 17, direct TLS, exact current/session user and role OID, and the
required transaction settings. Read-only observation is the default; a caller
may explicitly request observation inside its already-held read-write
transaction. The observer never changes transaction mode or applies a grant.

The full effective table/sequence privileges, direct routine ACLs, column ACL
absence, role restrictions, owner/default ACL policy and fitted-function
exclusion are checked. Missing required grants or extra privileges are refused.
The G12 catalog additionally checks exact function body hashes, invoker and
search-path properties, both table shapes, required CHECK semantics, keys,
indexes, all six application guards and all four internal FK triggers. It
reads the same catalog again before returning an observation. Unknown SQL
CHECK semantics are rejected by a restricted parser; OR, negation, arbitrary
casts/functions and trailing SQL cannot be normalized into an accepted check.

All returned records retain `admission=DENY`, `startup_admission=false` and
`execution_authority=false`. Inherited native definition admission is explicitly
false. Supplying an expected OID or retaining a catalog hash cannot promote an
observation into a runtime capability.

## Validation and remaining integration

The new source/refusal unit cases and the existing G12 custody/computation unit
regression were executed locally. Actual PostgreSQL integration cases have
been collected, but not executed. Their synthetic isolated TLS fixture tests
0056-to-0057 upgrade, empty downgrade/re-upgrade, restricted claim/publication
commit and independent six-session/XID readback, UPDATE/DELETE/TRUNCATE denial,
custody/frame denial, nonempty downgrade refusal, every required grant
revocation, extra UPDATE grant and disabled immutability trigger. Those cases
prove database mechanics only; they do not qualify a real candidate or trade.

The successor is not yet connected to the operational pipeline. The following
work must precede any installation or execution claim:

- Extend exact source-scope inventory with the new 58-file migration identity,
  retaining explicit historical 0056 target scopes and every old migration byte.
- The closed source-defined 0056/42 and 0057/44 dispatch now covers inherited
  graph/head validators, multirole comparison, restricted role observations,
  witnesses/stable identity and held writer/frame/Control observations. The
  successor query captures an additional real G12 catalog; its role-neutral
  validated definition hash joins the inherited graph reference. Raw catalogs
  retain physical OIDs separately. Default 0056 query bytes, hash labels and
  grant plan remain unchanged. Issued metadata and higher runtime consumers
  must still explicitly carry this profile; observed 0057 records are never
  rewritten as 0056.
- Dispatch all six G12 custody-session checks from the genuine issued profile
  and actual writer OID. Preserve task/factory/session/XID/deadline pins and the
  post-publication barrier.
- Measure and review the complete new schema/ACL/role/source identities on two
  isolated real PostgreSQL installations. Update the attended owner plan and
  independent readback for the successor; do not add grants to the installed
  DB0056 descriptor in place.
- Run the actual integration cases, full exact regression, migration/schema
  checks, and native G12-to-R7 pipeline before admitting the new profile.

The shared dispatch seam is the physical-head and ACL validator, rather than a
new parallel execution engine. Existing historical validators keep their
default 0056 contract. New callers must carry the closed successor profile
derived from their real issued metadata throughout the same controlled chain.

The new source pin dispatcher accepts exact source56 for DB0056 and exact
source57 for DB0056 or DB0057. On source57 it checks the complete 58-file
migration inventory and the unchanged 57-file source56 prefix. DB0057 always
requires source57. It does not change the separate physical database-head
check. Source57 owner/pregrant provisioning remains unsupported until its own
attended plan and catalog are supplied.
