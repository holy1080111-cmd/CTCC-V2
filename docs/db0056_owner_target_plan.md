# DB0056 owner target plan

Run this separately from the API on a configured PostgreSQL 17 owner target.
The installed source must match the reviewed archive byte for byte. Source
inventory follows the existing manifest rules for build outputs and metadata.
The target must already be at DB0056 with the original three restricted roles
present and their 42 inherited grants absent.

```text
python -B -m scripts.native_owner_target_plan_0056_v1 --inspect-reviewed-owner-target --reviewed-inputs-file /private/target.json --reviewed-inputs-sha256 <reviewed digest> --owner-dsn-file /private/owner-dsn --ca-file /private/ca.pem --source-archive-file /reviewed/CTCC-source.tar
```

Use a separately reviewed private JSON record with schema
`ctcc.native_owner_0056.target_plan_inputs.v1`. Its top-level keys are
`schema`, `source_commit`, `source_tree`, `source_archive_sha256`, `route`,
`writer`, `custody` and `frame_reader`. The `route` object has
`source_manifest_sha256`, `ca_sha256`, `cluster_system_identifier_sha256`,
`host`, `port`, `database`, `owner`, `database_oid`, `owner_oid` and
`database_head` (always `0056`). Keep the exact owner PostgreSQL DSN in its
separate private file. The inputs and DSN files must be directly within
owner-private directories, inaccessible to other ordinary accounts.

The tool checks archive identity, owner TLS identity, PostgreSQL role OIDs,
the installed definitions and the exact pregrant ACL. It compares the
portable owner definitions with the reviewed independent PostgreSQL
observations and separately reopens the four cluster-specific catalog
measurements. Some catalog statements require a READ WRITE transaction
profile. The tool only runs catalog reads in that profile.

Exit `2` means `OBSERVED_DENY`. Output includes hashes only for private
target values. The target plan digest binds the exact release source, target,
roles and their 42-statement grant plan. Exit `1` means the observation was
refused. This command installs no grants and confers no startup or execution
authority.

Add `--private-plan-directory /private/empty-plan-directory` to retain
`db0056-target-grant-pins.json` for the later direct-login readback. The
directory must already be owner-private. Publication refuses to replace
an existing file. The file contains the target role and database identities,
so keep it private. It contains no DSN, password or permission to grant.

After provisioning, run the direct-login check with the three restricted
runtime database URLs configured on the same reviewed TLS route. Supply the
private plan file and the `private_target_plan_file_sha256` from observation.

```text
python -B -m scripts.native_owner_postgrant_readback_0056_v1 --inspect-granted-owner-target --plan-file /private/plan/db0056-target-grant-pins.json --plan-file-sha256 <file digest> --owner-dsn-file /private/owner-dsn --ca-file /private/ca.pem --source-archive-file /reviewed/CTCC-source.tar
```

Exit `2` means fresh logins observed the exact scoped grants and an unchanged
physical target. The command confirms the restricted PostgreSQL principals and
their privileges. It does not establish which authentication mechanism was
used. Exit `1` is a refusal. The check does not apply or retry a grant.

## Apply one owner grant plan

Use a POSIX owner terminal with one persistent owner-private journal directory
per deployment host. Keep the owner DSN separate from the three restricted
runtime DSNs. The latter must point to the same reviewed TLS target so the
command can independently read them back after commit.

```text
python -B -m scripts.native_owner_grant_0056_v2 --apply-reviewed-grant --plan-file /private/plan/db0056-target-grant-pins.json --plan-file-sha256 <file digest> --owner-dsn-file /private/owner-dsn --ca-file /private/ca.pem --source-archive-file /reviewed/CTCC-source.tar --journal-directory /private/grant-journal
```

The command shows `CTCC_DB0056_GRANT_42 <file digest>` and requires the exact
phrase through the interactive terminal. This records the operator's intent.
It makes no claim of independent reviewer identity. The command independently
rechecks the approved plan and real owner catalogs, writes and reads back the
attempt marker, and applies the 42 inherited grants inside one locked
PostgreSQL transaction. The lock and exact pre-grant check also protect the
database if two hosts try to provision it.

Exit `2` and `GRANTED_AND_OBSERVED` report the exact committed grant and
successful fresh restricted-role login checks. Exit `1` reports a refusal.
Exit `3` requires reconciliation. Re-run the command with
`--reconcile-only` in place of `--apply-reviewed-grant` to recheck the
granted state. The reconciliation command never grants permissions or reopens
an attempt. A result that cannot prove the exact granted ACL stays
`RECONCILIATION_INCONCLUSIVE`. Preserve the attempt journal even if a fresh
read later confirms the grants. These commands leave startup and execution
admission at `DENY`.
