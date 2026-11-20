# Canonical owner retained-row encoding v2

The current56→57 and57→58 owner installers use one shared source-defined
`native_owner_retained_rows_v2` codec. The retained0017 read-only observer uses
the same codec and multiset SQL. No historical receipt, captured source,
migration or old validation result is rewritten.

Plain `to_jsonb(row)` does not distinguish SQLNULL from JSONB `null` in a nullable
JSONB column. V2 hashes the complete native JSON row plus a boolean SQL-null
bitmap in original column order. Each fixed-length row hash participates in a
C-collated ordered multiset; duplicates remain present. Counts/byte budgets,
physical relation OIDs, complete ordered column type/nullability layout and
actual sequence OID/definition/ownership/last-value/called state are retained.
Each invocation reads the actual transaction again; no saved DTO or cache is
accepted as a SQL connection or preservation authority.

The owner wrappers retain their exact source/archive/route/catalog admission,
account/legacy locks, flat/stopped constraints, grant counts, one-commit and
unknown-commit/readback rules. Only the separately verified Alembic marker
VALUE is excluded during an upgrade. Its physical relation and column layout
remain included. Every other old row/layout/OID/sequence must match. Current58
may add only its exact ten empty source-defined Live tables; that DDL creates
no new sequences. Changed or added sequences refuse. Unknown inheritance,
partitioning, RLS, object kinds or column identities refuse.

Current owner dry-run/commit receipts use v2 schemas and explicitly bind
`ctcc.owner.retained_row_sql_null_bitmap.v2`. An old JSON-only receipt cannot be
relabelled with the new header. Current wrappers obtain new real snapshots;
source commit/archive/manifest pins must match the new code. The catalog-only
reinstall pin continues to describe catalog definitions, never retained-row or
trading authority.

Authored PostgreSQL faults flip `audit_logs.before` between SQLNULL and JSONB
`null`, proving the old JSON representation has zero delta while the new
snapshot refuses. The56→57 case requires complete transaction rollback;57→58
tests both rehearsal and final-migration failures with complete restoration.
Existing positive/unknown-commit suites remain required. Collection is not SQL
execution and these fixtures are not account, venue or profitability evidence.

The canonical55→56 installer still needs its separate reviewed full row
preservation/rehearsal/readback extension. This change does not implement or
authorize that broader workflow or retained0017→55 mutations.
