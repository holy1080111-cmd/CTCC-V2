# DB0056 owner target plan

Run this separately from the API, from a complete extraction of the reviewed
archive on a configured PostgreSQL 17 owner target. Keep the archive itself,
configuration, virtual environment and generated files outside that extraction.
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
