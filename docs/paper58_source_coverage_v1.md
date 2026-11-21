# Persistent Paper58 source coverage

This slice is a source-only engineering inventory. It neither validates an
installed catalog nor opens a database, provisions a role, imports the service
singleton, constructs ASGI, or changes the current closed startup gate.

`scripts/paper58_source_coverage_v1.py` freezes the actual current58 relation
roster and lists each genuine registered model's columns, compiled PostgreSQL
types, server defaults, separate client defaults/on-update markers, all keys,
CHECK/unique/FK constraints, indexes and CREATE TABLE source. It traverses only
the fixed linear 0001–0058 migration graph and the domain source modules that
graph explicitly imports. SQL declaration sites retain source expressions,
dynamic-name gaps and historical/effective-override composition requirements.
ORM structure and a declaration site's existence do not prove installed
function bodies, trigger bindings, default ACLs or the complete schema.

The exact Paper table plan is 20 privilege cells on eight tables. Only Paper
state/checkpoint, audit and system-event rows are writable. Original recovery
counts additionally need SELECT on both orchestrator history/fingerprint
tables; they receive no automation write rights. Public schema USAGE is
separate. The future actor must have no schema CREATE, sequence usage,
membership/inherited extra/column grants, native execution rights or venue
credentials. Normal boot must never provision or migrate.

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
