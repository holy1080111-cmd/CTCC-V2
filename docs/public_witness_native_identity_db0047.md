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

The source-derived function body pins and the intended unbounded NUMERIC
column shape are recorded in
`scripts/verify_native_witness_identity_schema.py`. The PG17 native constraint
deparser baseline is deliberately **unsealed**. Both head-47 full migration
readback and the Linux union reject this state, including a caller DTO that
copies the pending template. Historical head-46 v3 readback remains separate.

The isolated `native-witness-identity-0047` diagnostic migrates the exact
archive to head, runs the 17 current witness cases, and exports bounded public
DDL metadata. It does not invoke the pending full-schema acceptance gate.
Native metadata must be compared against the intended integral/range guard,
function bodies, ownership and retained privileges before the baseline is
sealed in another reviewed commit. An observed hash alone is not acceptance.

The hermetic full harness also requires an explicit empty 47 to 46 readback
before the existing empty-table roundtrip and head-47 re-upgrade. The old
DB0042 irreversible downgrade refusal is still independently checked. The
wide-value integration cases attempt a nonempty DB0047 downgrade and require
catalog and exact record readback to survive rollback.

Local unit/static checks and collection do not constitute PostgreSQL,
Windows-host source capture, point-in-time availability, Demo, or execution
acceptance. This migration grants no account or trading authority.
