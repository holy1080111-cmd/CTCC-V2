# Canonical owner0055 →0056 retained-state upgrade

The existing `scripts.native_owner_upgrade_0056_v1` command now requires
`ctcc.native_owner_0056.reviewed_upgrade_inputs.v2` for its explicit positive
upgrade flag. The module and command names remain canonical. The old V1
archive, source and route helpers remain DATA-only for historical consumers;
V1 input cannot enable DDL.

V2 adds required ordered `role_names` and `role_oids` arrays for writer,
custody and frame reader. They must match three distinct actual configured
restricted logins and the exact original database/owner OIDs. Names are never
guessed from role inventory. The reviewed source commit/tree/archive,
manifest, CA, cluster and TLS owner route remain required. A pin file/hash is
an input and does not by itself prove who reviewed it.

The historical partition catalog requires its actual owning role to be a
PostgreSQL superuser. Keep that owner out of the runtime API. The three
configured writer, custody and frame logins remain separate restricted roles.
A role demoted after provisioning must fail before the migration transaction,
with revision 0055 and its exact existing grants preserved.

PostgreSQL component 1 requires both the genuine supported CLI upgrade and
the genuine demotion refusal. The former non-superuser success expectation
is retired. The original failed result and the focused refusal diagnostic
remain archived under their original commits and test identities.
[Original failure](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/45863452395),
[Refusal diagnostic](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/45867111319),
[Supported CLI acceptance](https://github.com/holy1080111-cmd/CTCC-V2/actions/runs/45869229514).

The command uses the existing configured three-role source and opens fresh
owner/writer/custody/frame connections with the reviewed CA. It verifies the
full actual native55 catalog, custody/frame/writer proof and exact42 effective
grants before the owner transaction. No grant, revoke or role creation occurs.
Before a version-row read, a catalog-only predicate checks the native ordinary
Alembic table and fixed builtin VARCHAR layout. A held SHARE lock prevents
replacement by a view while separate route/role sessions read it. Other user
schemas, foreign tables and large objects are outside this tool's admitted
storage scope and are refused.

Under the existing owner advisory lock, Alembic NOWAIT lock and local-table
locks, it repeats source55 catalog/role checks before application-row reads.
All original native scalar types and absence of row-rewriting rules are also
checked by the shared `ctcc.owner.retained_row_sql_null_bitmap.v2` codec.
Every row is retained as a deterministic multiset including duplicates and an
ordered SQL-null bitmap. SQL NULL and JSON null are distinct. Original column
layouts, relation/object OIDs, defaults, functions, constraints, triggers,
indexes, sequence definitions and values, role state and ACLs are retained.

The exact source56 migration first runs inside a savepoint. Its full actual
inherited/fitted catalog and retained-state checks must pass. The savepoint is
then actually rolled back; the entire original55 inventory must match. The
same fixed migration is applied again, all checks repeat, and its logical
catalog must match the rehearsal. Only then is one outer COMMIT attempted.
The only new data tables are the three source-defined fitted candidate
tables, all empty. The Alembic marker changes55→56; every other old value and
layout remains exact. No old field is normalized or reset.

After COMMIT, fresh owner route sessions, three restricted-role logins and
repeated independent complete inventories must match the committed result.
The V2 receipt binds the new row encoding and source identity. It cannot be
reused to qualify old V1 receipts or source commits.
Its native-profile catalog and old-state preservation fields are explicit;
the broader `schema_drift_verified` remains false because this operation does
not create a reviewed full historical legacy-schema baseline.

Default invocation refuses without mutation. Unknown COMMIT gives terminal
`INCOMPLETE` and no retry. Failed post-commit readback remains incomplete;
the tool does not erase the version marker, downgrade, regrant or run again.
Existing stopped/disarmed/local-flat constraints remain required. Existing
unresolved trades/intents are preserved and block incompatible upgrades.

This is database profile installation only. `startup_admission` and
`execution_authority` stay false; no exchange request, leverage change,
reconciliation, account-flat or Demo/Live acceptance is claimed. Actual host
0017→55 preparation is a separate reviewed task and is not supplied here.
