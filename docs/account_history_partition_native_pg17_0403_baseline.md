# DB0050 native PG17 catalog baseline from source 0403

This baseline records a reviewed native catalog. It does not authenticate an
account, satisfy the restricted runtime gate, authorize Demo/Live, or complete
PostgreSQL/concurrency/full release acceptance.

The source was Git commit `0403b9c6be1c9766b8da4804604ba7138ac735b2`,
tree `e6c98a393e1bf0545b1c6240bca483977c6e7171`, with manifest
`2466d148f72faa2dece775ba1b13ec9019b73bc7c556a99aaa03ed63a8af5a0e`.
The actual diagnostic ran as GitHub run `40104520052`, artifact `12563837258`.
The artifact ZIP SHA256 is
`2f1b57e43d385e192eb44044ec518e8847bd2097b006e952be752ca8061187eb`.
The exact 100039-byte wrapper is retained, unmodified, at
`tests/fixtures/account_history_partition_native_pg17_0403_catalog.json`, SHA256
`fec7abcfaecebaa5216ef9835a0d4497a473b8363a0bbdaf6995ef3d59f9f4d6`.
The wrapper's `source_head=0050` and `database_head=0050` are migration revisions;
they are not Git commit identities. Its source pins describe the source before
this seal proposal and must not be relabelled as the later sealed source.

The real server was PostgreSQL 17.11 (`170011`). The observed inventory contains
2 permanent public tables, 24 columns, 23 validated constraints, 6 valid/ready
unique B-tree indexes, 9 exact composed SQL function bodies, 8 enabled triggers
and 25 owner-only ACL rows. The nine CHECK expressions were independently
compared to their intended source semantics, including BETWEEN expansion,
explicit text/bigint casts, arithmetic, regexes and AND/OR grouping. Foreign-key
targets and function/trigger namespace/OID links were separately checked.

`deparser_sha256` canonicalizes the observed ordered constraints and indexes.
It excludes only each constraint's `table_oid` and `target_oid`; no row,
definition, expression, predicate, ordering or other field is removed. The
reviewed 13025-byte canonical input hashes to `37190935d1b8e2807d26590225165779bb12e9684c260dd19fb61fba0d3c64c3`. Dynamic OIDs remain
mandatory graph checks in the full verifier. The effective DDL identity remains
`ea1673b244a9cdda216f49300045ef0f36633b292ac59da853b940291d7576bb`.
The original nine function bodies, migration0050 and all guard functions remain
unchanged by this seal; only the policy header and two explicit seal constants
change. Reading a fixture or calling a structural diagnostic cannot install a
seal.

The capture login was the actual `ctcc` superuser/owner, in repeatable-read with
the default search path. This is an administrative structure/deparser snapshot.
The strict runtime gate must reject this snapshot: restricted direct login,
read-committed, the required search path, actual grants, factories, ownership,
source custody, current revision and original leases still require independent
live readback. Structural formal matching is not runtime admission. Function
creation/body matching also does not execute every lazily planned statement or
prove concurrency and recovery behavior.

Independent source/data reviews are retained externally as
`native50-0403-independent-catalog-review-20261024/review.json`
(`2f58e1d33c851a532c3e8ad38ca0a7571a43167f079015b27441bb7ee2ac1a4c`)
and `0403-native50-root-catalog-review-20261024/review.json`
(`f3c345eeb73c82ed10d5ba53165313d6ad11a5792e6cf6518400d601199918d5`).
Full exact-source artifact closure is a separate root-reviewed evidence chain.
The original diagnostic's formal gate refusal while the seal was None remains
a refusal; a later seal does not rewrite that historical outcome.

This proposal was prepared without application imports, pytest, SQL/PG, Docker,
Git, network or source application. The recorded-catalog regressions are
declared tests, not newly executed results. Actual scoped verification, fresh
hermetic migration, restricted-role and integration/concurrency validation,
matching CI, Demo, Live and final acceptance remain outstanding.
