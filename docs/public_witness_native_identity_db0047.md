# DB0047: exact native file identity

DB0024 stored the public receipt journal root's native device and inode as
signed `BIGINT`. A legitimate Windows volume identity can exceed that range,
so journal genesis can fail before the first public HTTP request. DB0047 keeps
the measured identities intact: device is an unsigned 64-bit integer and inode
is a positive unsigned 128-bit integer. It never hashes, truncates, wraps or
relabels an OS identity to fit the database.

The two columns become unconstrained-typmod `NUMERIC`, with explicit integral
and range checks. `NUMERIC(p,0)` is intentionally absent because a scale cast
could round a fractional input before a check sees it. The existing append
function signature, table identity and read composite remain; only append and
insert-guard function bodies change through schema-qualified `CREATE OR
REPLACE FUNCTION`. The old migration, embedded legacy SQL, checkpoint bytes
and record-hash algorithm stay unchanged. Exact finite integral `Decimal`
readbacks normalize to Python `int` before replay and hash comparison.

The JSONB append boundary checks number type and observable canonical rendered
unsigned integer spelling before numeric conversion. JSONB has already lost
some original wire spellings, such as an exponent that normalizes to an
integer; this API cannot recover that evidence. The separate
`checkpoint_json` TEXT still retains original spelling. Its JSON parser must
find exactly one numeric device and inode with canonical integer spelling,
rejecting duplicate identities, decimal or exponent spellings, booleans,
strings, out-of-range values and disagreement with the numeric columns.

Downgrade 47 to 46 requires the whole witness table to be empty. The lock,
empty check, column narrowing and exact legacy function restoration share the
migration transaction. A nonempty table is refused even when every value
would fit signed BIGINT. No evidence is deleted to make downgrade succeed.

## Validation status for the first diagnostic commit

The first diagnostic commit `1fbbf170a962d46c3851e06b8f05f5aa129a4043`
recorded source-derived function pins and the intended unbounded NUMERIC shape,
while deliberately leaving the PG17 constraint deparser baseline unsealed.
Both head-47 full migration readback and the Linux union reject an unsealed
baseline, including an identical caller DTO. The negative test still explicitly
exercises this condition. Historical head-46 v3 readback remains separate.

The isolated `native-witness-identity-0047` diagnostic migrates the exact
archive to head, runs the 17 current witness cases, and exports bounded public
DDL metadata. It does not invoke the full-schema acceptance gate.

Actual [diagnostic run 39690256720](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/39690256720)
passed all 17 cases without skips. Independent readback verified its exact
source archive, all 1,293 Git blobs, 1,292 manifest rows, COPY identity, migration
head/source digest, test inventory, export hash chain and cleanup. Readback
receipt SHA256 is
`1d6f765edb66d1e3e8a78d82606a41ff2b99928f0bd6aec66f6dae0c2d0da503`.
Independent semantic review confirmed the actual two column types have no
numeric typmod, both changed function bodies match the migration, and the
pretty=true CHECK enforces the intended integral u64/u128 bounds. Only that
constraint comparison digest is now sealed as
`b19f38740a3756948a3c17f97c4d4433d84010a7311a81a9bc3567eab1e5534b`.
The diagnostic's bounded catalog omits ownership, ACL, full trigger and other
schema metadata. The complete catalog query must still read and match every
one of those fields in a new exact-source native validation. Sealing this
comparison input does not grant full PostgreSQL or deployed-schema acceptance.

The separate `native-witness-migration-0047` profile now reuses the full
catalog probes for one empty 47-to-46 downgrade, exact legacy/custody readback,
head-47 re-upgrade/drift check, the same 17 cases, and one nonempty refusal with
exact before/after retention. Its bounded receipt is source-bound and preserves
validated partial progress on failure. This new profile still requires actual
exact-source execution; it does not replace the full PostgreSQL suite.

The hermetic full harness also requires an explicit empty 47 to 46 readback
before the existing empty-table roundtrip and head-47 re-upgrade. The old
DB0042 irreversible downgrade refusal is still independently checked. The
wide-value integration cases attempt a nonempty DB0047 downgrade and require
catalog and exact record readback to survive rollback.

The empty-down receipt is versioned separately from the historical head-46 v3
receipt. It embeds that v3 receipt unchanged and additionally requires both
identity columns to be BIGINT, the exact old append/guard bodies, unchanged
read/trigger/security metadata, and absence of the new numeric constraint.
The old v3 receipt alone cannot prove that DB0047 restored this contract.
The successful downgrade must also preserve an actual object-custody digest
observed before and after: table/function OIDs, owners, ACLs and trigger
identities must remain the same. Expected column-type/body changes and the new
constraint are checked separately; a legitimate type-driven constraint OID
rebuild is not misclassified as custody loss.

After the PostgreSQL suite, a separate DB0047 refusal stage requires committed
small and wide synthetic identities. Each before/after observation captures
head, schema and bounded retention metadata in one read-only repeatable-read
transaction. Only counts and digests leave the probe: all stored columns feed
the row digest; actual table/function/constraint/trigger OIDs, ACLs and bodies
feed the catalog digest. The sole attempted downgrade is 47 to 46. It must
fail with DB0047's exact policy error, then head/schema/all-row digests must be
identical. This stage does not reuse DB0042's earlier refusal. The Linux union
checks hashes, exact stage order, failure status, refusal log and both receipts.

Local unit/static checks and collection do not constitute PostgreSQL,
Windows-host source capture, point-in-time availability, Demo, or execution
acceptance. This migration grants no account or trading authority.
