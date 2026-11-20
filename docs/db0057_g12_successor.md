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
  and actual writer OID. This source now carries those pins in the private live
  account replay view together with its original configured factories and role
  witness. G12 validates the complete profile and effective ACL before and after
  every custody session, retains one catalog fingerprint across all six, and
  binds physical head, writer OID and catalog fingerprint in the successor
  transcript. Task/factory/session/XID/deadline pins and the post-publication
  barrier remain required. Higher selector/startup admission must still provide
  the genuine successor owner; DATA constructors never enter the publisher.
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

The native issuer transaction and original G6 reader now recognize actual 0057
through the closed profile. The original DB0055 strict catalog path continues
to require its sealed baseline flag; 0056/0057 observations must keep that flag
false. The owned V6 writer, frame reader and counter Control context carry the
profile derived from the same original role witness. No branch sets a passing
or authority flag to compensate for an unavailable successor owner.

The fourth isolated PostgreSQL case exercises the real native G12 custody hooks
with a clearly synthetic private-owner handoff. All twelve physical catalog
checks, full effective ACL checks, six SQL sessions/transaction IDs, inserts and
independent committed readbacks are real when this test runs. It does not
manufacture a public candidate, run R7, reserve risk or submit an order. This
case has been collected only in the current source work; it is not an executed
PostgreSQL or Demo acceptance claim.

## Exact-source isolated G12 smoke contract

`scripts.verify_current0057_hermetic` is a separately named, explicit four-case
diagnostic. It requires `--g12-successor-smoke`, exact Git archive SHA256, commit,
tree, all 58 migration files, unchanged 0056 prefix and matching runner/helper
bytes. It builds a fresh source image and disposable internal-only PostgreSQL
17 TLS/Redis containers without host credentials or host `.env`. The runner
checks source COPY, manifest, Linux dependency lock, fresh 0056 upgrade, actual
0057 migration identity, empty downgrade/re-upgrade and schema drift before
executing the four G12 tests. Trading flags are disabled and external order
writes remain zero. Disposable TLS keys are removed after container cleanup
and are excluded from uploaded artifacts.

Each test records a source-bound, no-clobber observation of the real physical
0057 head, PostgreSQL version, TLS, cluster identity hash, all three role OIDs,
44-statement ACL shape and inherited/G12 definition hashes. JUnit, every stage
log, each observation and cleanup are hashed and independently read back.
`scripts.verify_current0057_g12_smoke_pair` replays both exact-source artifact
sets and requires distinct cluster identities with equal normalized definition
hashes. The workflow `db0057-g12-smoke.yml` provisions those two independent
hosted clusters when explicitly dispatched on its dedicated source branch.

This smoke returns `hermetic_regression=NOT_RUN`,
`component_validation=NOT_RUN`, `execution_authority=false` and
`trading_acceptance=false`. It is not full Linux regression, native startup,
candidate, Demo or Live acceptance. Generic source57 dispatch cannot fall back
to a 0056 archive contract. It selects the distinct full successor contract
described below. The original 0056
runner/contract bytes are unchanged. Historical-source scopes record real
source57 and a separate database56 target; they never rewrite an observed head.

## Full source57 hermetic regression and union

`verify_current0057_full_hermetic` admits only the exact 0057 source inventory,
archive, Git tree, manifest and host runner/helper code. The closed versioned
runner builds and tests one disposable component with host credentials and
host `.env` excluded, an internal-only network, PostgreSQL 17 TLS, Redis,
source COPY validation, dependency lock, lint/format, actual 0057 migration and
schema drift checks. Its anchor exercises supported older upgrades, explicit
0056-to-0057 upgrade, empty downgrade/re-upgrade and API/PostgreSQL/Redis restart
with fresh readback. The root trust tests keep their constrained independent
Linux execution and exact platform skip contract.

One source-defined collection plugin assigns every collected case exactly once
across PostgreSQL owners 0/1/2 and eight deterministic shards. Owners 0/1 keep
the existing historical module allocation. Owner 2 executes actual 0057 G12
guard/44-ACL/six-session cases and retains their source-bound observations.
Owner 1 retains explicit source57/historical56 campaign, fitted, inherited
native and postgrant observations through the separately versioned historical
wrapper. Physical 0057 migration logs are never projected as 0056.

The union replays all eleven components from the same exact archive, requires
equal complete collections and disjoint selected cases, verifies actual JUnit
identities, approved skips and all required inherited/new safety cases once,
and refuses missing components, duplicated runs or smoke-only artifacts. The
required `Docker hermetic regression` workflow selects [0,1] for historical
source profiles and [0,1,2] only after exact reviewed source57 inventory proof.
The eight shards and Windows acceptance remain required. No old counts or
older receipts satisfy this new source57 contract.

These runner/union mechanisms have bounded source/codec/refusal tests. A full
hosted source57 regression has not been executed by this implementation work.
Only actual verified component artifacts can produce a full regression PASS;
execution authority and trading acceptance remain false throughout.
