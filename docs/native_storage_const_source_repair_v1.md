# Native constant and array-wrapper source repair

The actual source1428 native55/native57 diagnostics both rejected only CONST
type IDs 19 and 705 and the partial-index node-tag group. The source-derived
subquery specifically found ARRAYCOERCEEXPR; it did not establish that no other
tag was rejected. Original combined predicates remained false and both tests
failed. The exact official artifact is run44863816367/artifact14691285697,
ZIP SHA256 d9382752f6e4d1848410a93d0df5aba20f56b9c18de03a1b19582ccc5bcfcfa9.

The fixed REL_17_11 facts now include native name(19) and unknown(705), their
exact I/O/subscript procedures and constant-only type properties. Name's
typelem is char(18), width is source NAMEDATALEN=64 and collation is native
C(950). Unknown is the sole explicit pseudo-type exception. Other admitted
types must still have the original base-type profile. All original 22 type
rows and 380 procedure rows are retained unchanged, as are the operator,
family, class and support inventories. Application row types and the existing
index type/collation/operator/function/dependency limits are unchanged.

ARRAYCOERCEEXPR is admitted using its exact seven-field primary source
definition in src/include/nodes/primnodes.h. Its nested expression is subject
to every original node/type/function/operator/collation/dependency restriction.
The first repair retained the rejection of CASETESTEXPR and FUNCEXPR; no child
was inferred from the source17 CHECK inventory. A fixed, source-filter-derived diagnostic now
retains at most 32 distinct rejected uppercase node tags. A nonempty inventory
still makes the original test and archived verifier fail. It grants no schema,
startup, reference, migration or execution authority.

The matching b50 native55/native57 run44871620548/artifact14694400632 then
proved both CONST lists empty and CASETESTEXPR the sole remaining rejected
tag/category; the two original combined predicates still failed. Its official
ZIP SHA256 is baaf0acab69a2963652f991b99c45c9bd0048dd822c970eba0c9080467cf9431.
The followup admits this context-value leaf only with its three exact source
fields: typeId, typeMod and collation. The existing fixed 21 index types and
collations 0/100 also constrain the newly recognized fields. Extra/missing
fields, nested callbacks, FUNCEXPR and unknown tags remain refused. No type,
procedure, operator, support, family or class row was added by this followup.
The primary executor compiles CASETESTEXPR to EEOP_CASE_TESTVAL; the existing
ARRAYCOERCEEXPR supplies its element context. These facts do not grant schema
or execution authority and do not turn either retained failure into a pass.
The source int32 typeMod also has its exact signed32-bit bounds. A CASE length
guard refuses long decimal operands before casting; at most11 signed decimal
characters fit safely in int8, then the narrower int32 range is mandatory.
This is a representation bound, not a new typemod semantic policy.

Primary sources are the exact PostgreSQL REL_17_11 catalog .dat/.h files,
pg_config_manual.h and primnodes.h. Their raw SHA256 identities are recorded
in native_owner_pg17_btree_facts_v1.json. Only catalog metadata and raw
pg_node_tree text are inspected; no observed type I/O, index support, operator,
AM callback, application row or deparser is executed by these predicates.

Reviewed primary definitions: [types](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/catalog/pg_type.dat),
[internal procedures](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/catalog/pg_proc.dat),
[C collation](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/catalog/pg_collation.dat),
[name width](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/pg_config_manual.h),
and [node fields](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/include/nodes/primnodes.h).
The exact [executor source](https://raw.githubusercontent.com/postgres/postgres/REL_17_11/src/backend/executor/execExpr.c)
SHA256 is f9cf5e2e83d5c096bd769bbde808bf3322e797ac13a1c89b31ba680600224acd.

Actual native positives and preserved callback/AM/index/collation negatives
must run on the matching repaired source. Pure source tests and SQL PREPARE
are supporting checks, never that actual acceptance.
