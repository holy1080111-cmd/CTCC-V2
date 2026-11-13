# DB0056 stable deployment descriptor

After the exact attended DB0056 grant and restricted-role readback, export a
private descriptor while the owner credential is still available. Run it with
the exact installed source and the same reviewed grant plan, archive, and CA.
The destination directory must already be private and outside the source tree.

```text
python -B -m scripts.native_deployment_descriptor_0056_v1 export-owner-verified --plan-file /private/db0056-target-grant-pins.json --plan-file-sha256 <plan digest> --owner-dsn-file /private/owner-dsn --ca-file /private/ca.pem --source-archive-file /reviewed/CTCC-source.tar --descriptor-directory /private/deployment
```

The command rechecks the target with fresh owner and three restricted TLS
logins and independently rereads the two reviewed portable measurements. It
publishes `db0056-deployment-descriptor.json` once without clobbering and
rereads its exact bytes. The descriptor contains the source identity and
digests of the CA, role identity, deployed definitions and ACL, owner-observed
cluster, and target grant plan. It stores no connection strings, account
identity, role names, IPs, or credentials. Retain the separately supplied
digest and transfer the descriptor to a private runtime-owned directory.

Use the execution-disabled native Demo profile, the three restricted
PostgreSQL URLs and the pinned TLS CA at runtime. This mode needs no owner DSN
or exchange credentials.

```text
python -B -m scripts.native_deployment_descriptor_0056_v1 verify-restricted --descriptor-file /private/deployment/db0056-deployment-descriptor.json --descriptor-sha256 <descriptor digest> --source-archive-file /reviewed/CTCC-source.tar
```

Readback verifies exact installed source and original portable measurements,
then compares the restricted database/role identity, definitions and ACL.
The three-role witness checks a common PostgreSQL incarnation during the
invocation. The stored role identity excludes PostgreSQL boot time, so a
restart can retain the same expected descriptor. The owner-observed cluster
digest remains export provenance. The restricted mode does not query it or
authenticate who supplied the descriptor.

Exit `2` is an observed diagnostic match or export. Exit `1` is refusal.
Both operations leave startup and execution admission denied and send no
exchange requests. This does not apply a schema migration, change permissions,
start the API, or replace account restart recovery.
