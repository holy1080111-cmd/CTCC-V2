# Restricted custody deployment diagnostics

The exact `741a3c3` source's CI preflight ran four DB0046 cases. Three rejected
their first restricted-role deployment verification; the fourth, deliberately
negative case passed. The original generic error and suppressed native
tracebacks did not identify the rejected catalog condition.

`RuntimeBundleCustodyError.diagnostic_category` now exposes a fixed failure
group. The public error text, SQL, expected hashes, predicates, custody
capabilities and execution refusals are unchanged. A category is diagnostic
data and cannot issue any authority. Unknown driver or exception content is
never copied into this property.

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
