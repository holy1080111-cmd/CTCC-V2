# Restricted custody deployment diagnostics

The exact `741a3c3` source's CI preflight ran four DB0046 cases. Three preflight
cases failed; the available evidence does not identify the failing method
phase. The fourth, deliberately negative case passed. The generic error and
suppressed native tracebacks do not establish that the first
`verify_deployment` await failed or identify the rejected condition.

`RuntimeBundleCustodyError.diagnostic_category` now exposes a fixed failure
group. The public error text, SQL, expected hashes, predicates, custody
capabilities and execution refusals are unchanged. A category is diagnostic
data and cannot issue any authority. Unknown driver or exception content is
never copied into this property.

The `df1d28f` diagnostic still exported three `unclassified` failures. A local
synthetic pytest/JUnit reproduction showed that failing inside the exception
handler retained an exception chain and therefore did not match the strict
single-line redactor. The wrapper now saves the fixed category, leaves the
handler and then reports the failure. The redactor remains strict; the actual
PostgreSQL method phase and category require a new native diagnostic run.

The `1baf117` run then exported `role_scope` for the three failed cases. This
locates the original catalog role guard but does not establish which caller
phase reached it. The diagnostic now records every failed condition from that
same role row as a read-only, bounded tuple of fixed names. No catalog values,
role OIDs, names, addresses or connection details are exported or re-queried.
The optional single-line `;checks=name,name` grammar accepts only those fixed
names, without duplicates; old category-only lines remain supported. Malformed
metadata becomes `unclassified`, preserving the failed outcome. These names
grant no deployment, custody or execution authority.

The nine internal PostgreSQL `"char"` catalog projections now use explicit
`::text AS` aliases while retaining the original guard comparisons. The pinned
asyncpg codec maps raw CHAROID values through its bytes decoder, making this a
strong type-mismatch candidate rather than an established native root cause.
The existing positive native preflight case now checks raw `relkind` and
`relpersistence` as bytes, their text casts as strings, and all nine repository
projections as strings on the actual restricted connection. It exports no row
values or identities. Until that exact-source native run executes, this remains
an implementation with offline checks, not PostgreSQL acceptance evidence.

The subsequent [native run 39664362079](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/39664362079)
executed exact `ad1f227dc074d850bd11d473dcc0d39bce651535`, tree
`06a8a32353979f944185a512e8763eed6f997b44`. All four selected cases passed,
including the actual raw-bytes/text baseline and committed exact readback.
Independent verification checked the downloaded server digest before/after,
ZIP CRC, every Git blob and mode, manifest, lock, image COPY identity, runner
receipt and redacted JUnit. Artifact SHA256:
`73f3f1bf89e1a83417fb031fed8e756a0f68267e8cd6ad993e46c6f6b0c67aab`.
The nonsecret independent receipt is
`../validation-results/ci-ad1f227-diagnostic-20261022/independent-receipt.json`,
SHA256 `8fd60b2b91f56f34e7a1a25347cb435bc9faea4b451bbb2fc96beb9fb971241d`.
This accepts four native preflight cases on that source only. The original
JUnit is hash-retained but not independently replayed; the safe redacted
report was checked. All ten repository cases, full migrations, merged-source
regression and actual production deployment remain separate requirements.

The separate `diagnose_runtime_custody_postgres` runner builds the exact Git
archive with the existing dependency lock, verifies every copied source byte,
and runs only the four existing native preflight cases on a disposable pinned
PostgreSQL image over an internal Docker network. It imports no exchange
credentials or host settings. It exports fixed categories, redacted JUnit,
source and run identity, and artifact hashes. Unexpected setup/driver text is
excluded from exported evidence. Original tests still reject a failed check.

This is a diagnostic mode with `full_postgresql_acceptance=False`. Even four
successful cases would not replace migration roundtrips, all ten repository
integration cases, the full PostgreSQL suite, restart/chaos acceptance or the
matching final-source CI. The prior source remains failed until a concrete
cause is repaired and separately tested; this tool disables no release check.
