# Historical55 bootstrap and reviewed Linux owner upgrades

`config/compose.native-only.override.yaml` is an explicitly historical physical55
owner template. It is **not** the current58 application or a general database
initializer. Migration is pinned to `0055`, matching
`scripts.provision_native_roles_v1`. The old `upgrade head` command would have
produced58 and then correctly failed the55-only grant procedure.

All five services require an explicit profile and `restart=no`. The API has no
credentials, dependencies or health/readiness claim; even selecting its separate
profile executes only an unconditional refusal. No intermediate55 server can be
started through this template. The final Windows launcher independently requires
real physical58 and genuine current role/recovery checks.

## Preserve the actual database first

Do not start Docker Desktop merely to inspect the old installation: existing
`restart=unless-stopped` containers may resume the preserved legacy API. Preserve
the original `C:\CTCC-V2` source, private files, container/service metadata,
database/volume identity, backup and complete old intent/reservation/exposure
state first. A fresh database, research lab or new profile cannot stand in for
those records. Unknown state remains unknown and blocks rollout.

Before any owner command below, require the exact reviewed source image/archive,
manifest/dependencies, actual cluster/database/owner identity, retained-state
checkpoint, direct hostname-verified TLS and private pinned CA, and pre-created
distinct restricted roles. The historical Compose template does not create TLS
certificates, login roles or authenticate a retained volume. Its inherited base
volume/default server settings are not proof of these prerequisites. Do not
activate it until a separately reviewed retained-volume/TLS setup is present.
Do not inject a host `.env` or expose a DSN/password on a command line or report.

When using the old Compose loader, its base file still interpolates
`POSTGRES_PASSWORD` before the override removes that field. An isolated,
reviewed owner environment can use a clearly synthetic nonsecret placeholder
for that removed input; the effective model must contain only
`POSTGRES_PASSWORD_FILE`, never the inline field. Never supply the old runtime
environment just to satisfy interpolation. Effective-config inspection is
read-only and grants no permission to start services.

## Existing42-grant physical55 route

Run the following only from the exact reviewed source on the Linux owner host.
All uppercase shell values below denote reviewed **file paths or public hashes**;
none contains credential text. Pin files differ by stage and bind their actual
physical predecessor. Do not manufacture a newer head, role receipt or approval.

For a genuine retained pre55 database, the historical owner migration stops at55;
the historical provisioner then requires55 and installs exactly28+11+3 grants to
the three already-created restricted login roles. It independently reads them
back. A partial commit is a failure requiring review, never a grant retry. The
effective Compose dependency is `postgres healthy -> migration55 completed ->
provision55`; it cannot run the provisioner directly after58.

For an already verified physical55 database with those42 grants, use the existing
reviewed55-to56 migration (no new grants):

```sh
python -B -m scripts.native_owner_upgrade_0056_v1 \
  --upgrade-reviewed-native-owner-0055-to-0056 \
  --reviewed-pins-file "$PINS56_FILE" --reviewed-pins-sha256 "$PINS56_SHA256" \
  --owner-dsn-file "$OWNER_DSN_FILE" --ca-file "$CA_FILE" \
  --source-archive-file "$SOURCE_ARCHIVE_FILE"
```

After its independent committed readbacks, verify the actual restricted56 graph
and unchanged42 grants. Do not invoke an ungranted56 bootstrap over this already
granted account. The source58 historical adapter preserves actual physical56;
it does not rewrite a source/DB observation to a different version.

## Alternative: actual ungranted physical56

If independent inspection proves actual56 with pre-created restricted login
roles and **no grants yet**, the existing target planner and attended Linux
grant command provide the supported42-grant path:

```sh
python -B -m scripts.native_owner_target_plan_0056_v1 \
  --inspect-reviewed-owner-target \
  --reviewed-inputs-file "$TARGET_INPUTS_FILE" --reviewed-inputs-sha256 "$TARGET_INPUTS_SHA256" \
  --owner-dsn-file "$OWNER_DSN_FILE" --ca-file "$CA_FILE" \
  --source-archive-file "$SOURCE_ARCHIVE_FILE" \
  --private-plan-directory "$PRIVATE_PLAN_DIRECTORY"

python -B -m scripts.native_owner_grant_0056_v2 --apply-reviewed-grant \
  --plan-file "$PLAN_FILE" --plan-file-sha256 "$PLAN_SHA256" \
  --owner-dsn-file "$OWNER_DSN_FILE" --ca-file "$CA_FILE" \
  --source-archive-file "$SOURCE_ARCHIVE_FILE" --journal-directory "$JOURNAL_DIRECTORY"
```

The exact terminal acknowledgement, original durable one-shot journal and
independent restricted-login readback remain mandatory. This command explicitly
refuses Windows because its journal durability policy is unsupported there;
that restriction is unchanged. Planner/grant exit2 means inert owner DATA,
not application startup or trading authority. Do not use blind `&&`/automatic
retry around these commands. Uncertain attempts require the existing
`--reconcile-only` route with the same private plan/journal, never reapplication.

## Shared56 ->57 ->58 route

The57 and58 installers are read-only unless their explicit execute flag and
matching phrase are present. First run each without those two arguments and
inspect its genuine source, database, role, local stopped/flat and catalog result.
Then execute that same reviewed stage:

```sh
python -B -m scripts.native_owner_upgrade_0057_v1 \
  --reviewed-pins-file "$PINS57_FILE" --reviewed-pins-sha256 "$PINS57_SHA256" \
  --owner-dsn-file "$OWNER_DSN_FILE" --ca-file "$CA_FILE" \
  --source-archive-file "$SOURCE_ARCHIVE_FILE" \
  --execute --confirmation "UPGRADE CTCC OWNER DB0056 TO DB0057"

python -B -m scripts.native_owner_upgrade_0058_v1 \
  --reviewed-pins-file "$PINS58_FILE" --reviewed-pins-sha256 "$PINS58_SHA256" \
  --owner-dsn-file "$OWNER_DSN_FILE" --ca-file "$CA_FILE" \
  --source-archive-file "$SOURCE_ARCHIVE_FILE" \
  --execute --confirmation "UPGRADE CTCC OWNER DB0057 TO DB0058"
```

The57 transaction adds the two fixed G12 grants, reaching44. The58 transaction
installs its exact additive migration and adds no Demo grants. Both retain their
real catalog/row/revision checks, transactional rehearsal and independent
postcommit readback. Unknown commit or late readback failure is terminal and
must not be automatically retried, downgraded, erased or called uncommitted.

Owner SQL acceptance does not prove exchange flatness. Continue only with the
[explicit58 stopped setup](windows_tool0058_stopped_setup.md), genuine signed Demo
identity/current account ingestion, complete preserved-state reconciliation and
the [current58 Windows tool](windows_tool_physical0058_v1.md). Arm stays OFF.
Dedicated Live roles/runtime are separate; an analysis profile or research
PostgreSQL directory is never promoted to a trading ledger.

## Validation scope

The effective-model tests reject `upgrade head`, `upgrade0058`, early/optional
grant dependencies, automatic restarts and API startup dependencies. A source
YAML test checks the actual template and executes only its fixed Python refusal
expression, with no Compose daemon, database, network, credentials or server.
These tests are not host provisioning, a migration execution or provider evidence.
