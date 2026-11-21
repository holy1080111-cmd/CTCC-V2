# Native owner storage diagnostic

The fixed `work/db0058-native-storage-diagnostic` workflow reuses the existing
exact-source archive/TLS probe runner with `--native-storage-diagnostic`. It runs
only two original disposable owner-lab cases: a genuine native55 schema and a
genuine native57 schema. It has no caller-selected node, SQL or module argument,
does not open an installed database, and cannot issue a venue request.

The diagnostic evaluates the unchanged original combined storage predicate and
source-named catalog-only components. Its bounded lexer preserves quoted bytes
and parentheses and reconstructs each original conjunction exactly. The actual
cases compare the components' three-valued conjunction with the same original
combined predicate. Queries inspect only fixed `pg_catalog` metadata, before any
marker-row read, deparser or application-row query in the diagnostic itself.
The existing labs own fresh-schema setup and cleanup.

Each case writes safe group booleans, source/predicate hashes and false admission
fields before asserting success. A false or unknown original predicate still
fails the job, even if later component observations change. The diagnostic has
its own identity, report and archived readback; it never supplies a source17
reference pair, full-suite union, schema admission or execution authority.
The default reference-probe purpose is unchanged.

Both actual node identities are mandatory exactly once on current58 PostgreSQL
component3 and excluded from Windows execution as PostgreSQL cases. Frozen56/57
contracts remain unchanged. Pure lexer, identity, omission and routing checks or
collection-only fixture resolution do not count as actual PostgreSQL execution.
No production predicate or callback/OID allowlist was changed for this probe.
