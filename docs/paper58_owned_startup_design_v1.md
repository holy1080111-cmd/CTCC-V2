# Paper58 owned startup: separate structural and actor purposes

The official source72d reference run44890988317 produced two genuine fresh
canonical55→58 databases, with different database, owner and marker OIDs. The
unchanged archived verifier passed. Both complete normalized catalogs contain
the same9,218,124bytes, SHA256
`0c0336e396efe2b91c2d725f8e936673ef44aa081dd330b3a62c1f1369b8f8ab`.
The two raw catalogs are retained as compressed fixtures and checked after
decompression against their original hashes. The fixture also retains the
exact independent verifier receipt with the normalized and source artifact
hashes and the original false authority flags.

## Two distinct continuation purposes

Purpose A is a fresh canonical58 database whose owner is an allocated role,
not initdb role10. Its actual owner flags are SUPERUSER, NOINHERIT, LOGIN,
NOCREATEROLE, NOCREATEDB, NOREPLICATION and NOBYPASSRLS, with no memberships.
The0c literal and source scope6b8 apply only to its **pre-provision** catalog.
The owner gate rejects bootstrap10 before normalization. It retains every
ownership, ACL and shared-dependency edge; none is synthesized or discarded.

Purpose B is the preserved bootstrap-owner continuation. Its actual0017
owner10/all-seven-flags-true profile has PostgreSQL's distinct pinned-owner
shared-dependency behavior. It must complete the reviewed genuine0017→0055→
0056→0057→0058 route while preserving the existing ledger and receive its own
complete bootstrap58 observation/purpose. Purpose A cannot approve B, relabel
its owner, fill missing edges, or replace that ledger with a blank58 database.
Purpose B remains denied by these modules.

## Implemented bounded components

`paper58_owner_preflight_v1` compares the entire observed pre-provision catalog
to the approved fresh-unpinned literal. Its new collector accepts an actual
asyncpg SQLAlchemy AsyncConnection in an existing READ ONLY transaction. It
uses the original native marker/storage and raw safety predicates, locks all
105 source tables, repeats safety, and obtains the unchanged full typed query
twice. It performs no role, ACL, schema, data or application startup writes.

`paper58_actor_acl_v1` compiles a closed policy only after that complete
pre-provision DATA match. The exact source roster is21 table privilege cells
on9 tables: the original20 runtime cells on8 tables, plus SELECT on the
Alembic marker. All105 tables and every source column remain audited for
unapproved effective rights and grant options. All9 sequences and161 APP/
native functions are audited separately. The planned actor is a different
allocated LOGIN role with NOSUPERUSER, NOINHERIT, NOCREATEDB, NOCREATEROLE,
NOREPLICATION and NOBYPASSRLS, with no role memberships or object ownership.

`paper58_actor_inventory_sql_v1` is a catalog-only DATA reader for the actual
restricted connection. It does not execute APP functions, pg_control_system,
or a financial/native actor observer. It audits table and inherited column
rights, grant options, schema/database rights, sequences, APP function EXECUTE,
membership and ownership, default/parameter ACLs, and all applicable stored
setting scopes. It emits stored-setting **names**, never their values. Stock
driver and introspection ACLs match their initial `pg_init_privs` or native
defaults. The single reviewed hardening is an exact bootstrap-owner-only ACL
on `pg_catalog.pg_control_system()` in the dedicated Paper database. Both
the owner and restricted readbacks verify this ACL. The restricted connection
also checks that its effective EXECUTE is absent.

These components return DATA with schema/Paper/startup/execution authority
false. A synthetically matching privilege matrix remains DATA. It cannot
create an owned startup context, prove an actual connection, recover Paper,
or authorize an application by a JSON flag.

## Exact ACL overlay and actual context

Explicit provisioning is separate from normal boot. It may create the reviewed
actor and add only CONNECT, public-schema USAGE and the21 table cells, without
grant options. It must revoke effective PUBLIC TEMPORARY/schema CREATE and
APP/native function EXECUTE where required. It must not grant Paper any
sequence use, native control function, owner table locks, schema CREATE, native
table writes, extra column rights, or role membership. No grant ALL shortcut
is permitted.

`paper58_actor_overlay_v1` checks the observed ACLs, dimensions and eleven
physical shared dependencies for the nine tables, current database and public
schema. It also checks the matching entries for any reviewed function repeated
in the stored-expression callback inventory. Every input ACL and dependency
remains in the original catalog hash. A separate projection of the explicitly
matched delta replays against the same full pre-provision structural literal.
The expected schema ACL grantor is the observed stock `pg_database_owner` role.
The separately collected restricted inventory checks every explicit dependency
for the Paper role across the cluster. Genuine isolated PostgreSQL must still
validate the overlay and restricted inventory.

The owned context collects both observations itself and keeps the owner
connection open through restricted recovery. One restricted TLS backend holds
the Paper process advisory lease and backs the exact session factory used by
the original `PersistenceRepository` and `PaperExecutionService`. The normal
boot path performs no provision, migration or automatic DDL.

## Startup and recovery composition

Closed configuration must reject unsafe inherited Live/Demo/order/automation/
outbox settings and secrets before provider or generic application imports.
Disabling dotenv alone does not close OS environment input. NOSUPER actors
must not be sent SUSET session_preload_libraries settings: genuine PG17 rejects
even an empty override. Maintenance preflight must independently reject unsafe
stored/server preload configuration before opening the restricted actor.

The local-only ASGI app exposes the same path and method allowlist, the existing
dashboard, snapshot analysis and Paper service. Its Paper handlers explicitly
bind the recovered service and serialize operations on the lease-holding
backend. It refuses cross-origin requests. The startup code requires a
consistent, error-free, committed recovery checksum before serving. Shutdown
uses the same service and skips a checkpoint if its commit outcome is unknown.

The six writable tables in the retained catalog have four native foreign-key
triggers on Paper order and position rows. Paper account and checkpoint inserts
supply `id=1`, so their serial defaults require no sequence grant. The first
actual restricted test must exercise those writes and a restart using a fresh
engine under the same actor. It must also exercise the unknown-commit fence
before closing the container startup boundary. Native Demo and Live retain
their own physical origin requirements.

## Verified Windows launcher

`python -m scripts.run_paper58_owned_v1 --profile <absolute-private-profile>`
starts the app on `127.0.0.1` with its PostgreSQL recovery lifespan enabled.
Append `--check` for package and configuration readback. The launcher checks
the original Windows package identity, Git archive, source tree, manifest and
dependency set before configuring the app.

The private profile schema is `ctcc.paper58.fresh_owned_windows_profile.v1`.
It requires `package_root`, `package_identity_sha256`, `port`, `ca_file` and
`ca_sha256` plus private file pointers `api_token_file`,
`owner_database_url_file` and `actor_database_url_file`. Each pointer holds
one exact value. The two authenticated PostgreSQL URLs use the same direct TLS
server and fresh database with distinct roles. The application is available at
`/dashboard/local-analysis` after the startup lifespan passes. This launcher
requires a separately provisioned fresh database. Existing bootstrap-owner
ledgers need their own observed continuation.
