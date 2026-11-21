# Persistent Paper58 source coverage

This slice is a source-only engineering inventory. It neither validates an
installed catalog nor opens a database, provisions a role, imports the service
singleton, constructs ASGI, or changes the current closed startup gate.

`scripts/paper58_source_coverage_v1.py` freezes the actual current58 relation
roster of 104 model tables and separately includes the unmapped canonical
Alembic version table (105 public tables total). It lists columns, compiled PostgreSQL
types, server defaults, separate client defaults/on-update markers, all keys,
CHECK/unique/FK constraints, indexes and CREATE TABLE source. It traverses only
the fixed linear 0001–0058 migration graph and the domain source modules that
graph explicitly imports. SQL declaration sites retain source expressions,
dynamic-name gaps and historical/effective-override composition requirements.
ORM structure and a declaration site's existence do not prove installed
function bodies, trigger bindings, default ACLs or the complete schema.

The exact Paper runtime plan is 20 privilege cells on eight tables plus one
SELECT on alembic_version for preflight: 21 cells on nine tables total. Only Paper
state/checkpoint, audit and system-event rows are writable. Original recovery
counts additionally need SELECT on both orchestrator history/fingerprint
tables; they receive no automation write rights. Public schema USAGE is
separate. The future actor must have no schema CREATE, sequence usage,
membership/inherited extra/column grants, native execution rights or venue
credentials. Normal boot must never provision or migrate.

The closed source route audit includes all 16 actual local-analysis endpoints
and the two static redirects. The allowed analysis endpoint imports and
describes a supplied snapshot offline; it never calls an analysis repository.
The live-symbol analysis and dashboard/report snapshot endpoints are excluded,
so they do not justify extra table grants. Readiness performs SELECT 1 and
Redis PING. Paper reads use the original in-memory broker; mutations persist
through the original snapshot repository. Startup additionally calls original
recovery/status/counts, and shutdown persists the same Paper state. The audit
pins the exact route/handler and relevant service/repository source bodies,
their imported bindings, and the original router/service/session construction
modules. It reconstructs their SQL-model CRUD union: runtime20 plus marker SELECT1. It is
still source coverage, not an actual route/database/actor acceptance test.

The map points to existing source catalog checks with their real prerequisites.
Physical heap/index/I/O safety is not schema equality. Existing inherited
0055–0058 actor observers keep their role/head restrictions and owner58 denial;
Paper cannot be relabeled as a native actor. Financial/lifecycle metadata
comparers require a held, safely admitted catalog before any deparser. The
same-DB logical fingerprint and ORM equality cannot substitute for full
source-defined function/trigger/ACL/type coverage.

Remaining work is explicit: resolve the effective fixed migration/SPEC
overrides, compose a complete truthful Paper58 catalog observer, prove a real
separate NOSUPER actor's exact rights and retain an opaque source-owned startup
context. Closed Paper settings must address inherited OS variables as well as
dotenv; `_env_file=None` alone does not do that. Native Demo/Live origin and
physical guards remain unchanged. PostgreSQL NOSUPER cannot SET even an empty
session_preload_libraries SUSET startup parameter: private fresh/profile
settings must be independently admitted rather than granting SET privilege.

Use the original PaperBroker, PaperExecutionService and PersistenceRepository.
Startup must verify actual recovery `last_error=None`, `consistent=True` and
the existing unknown-commit fence in that same owned context. Awaiting recover
alone is insufficient. No Paper usability or startup acceptance is claimed by
this source inventory or its tests.
# Offline effective-declaration inventory

`paper58_effective_ddl_v1` uses the genuine Alembic PostgreSQL offline context
with canonical `Base.metadata` naming. It never loads `migrations/env.py`, a URL,
Settings or an application singleton. Every direct connection request refuses;
the whole revision's partial output is discarded, with no fabricated row,
role flag or scalar result. Source bytes are checked again after emission.

The bounded source run found121 latest-known function declarations and139
trigger declarations. Revisions0045 and0049–0055 require actual catalog input
and remain explicitly unresolved. Unsupported object changes or overload/drop
history also remain unresolved. These are latest source declarations, not a
complete installed object set; the registry cannot admit a schema or startup.
The genuine canonical55 DATA reference and the separate fresh58 structural
purpose must close that prefix and PostgreSQL-specific representation gap.

Nine focused checks passed, including an isolated child with no application,
session, Settings or Paper singleton import; socket connections, subprocesses,
SQLite connections and environment-file opens were forbidden. Its environment
retained only Windows `SystemRoot`, required for native Python imports. An
earlier empty-environment attempt failed at `_overlapped` import before any
source generation; that failure was not PostgreSQL or runtime acceptance.
