# Preserved deployment and rollback boundary

The original source repositories and Docker Desktop engine remain untouched.
The effective last API container used image
`sha256:8e9fd84b69cde3beed5aba81bfa79043090fe2a8b55f330327664922aa5b6d18`,
with a `Demo-Structure-Profile` evidence bind. Its PostgreSQL and Redis containers
and API all have `unless-stopped` restart policies. Starting that engine is not
part of source validation.

The data disk was attached through an explicitly read-only VHDX/block backend.
Its ext4 journal needed recovery. A separate QCOW2 overlay received that recovery;
the original disk remained read-only and its size/mtime remained unchanged.
The recovered filesystem was mounted read-only and only the two named CTCC
volumes were archived. No other application's volume or container was restored.
[QEMU read-only and snapshot options](https://www.qemu.org/docs/master/tools/qemu-nbd.html).

| Preserved volume | Bytes | Plaintext archive SHA256 |
| --- | ---: | --- |
| `ctcc-v2_ctcc_v2_postgres_data` | 130911744 | `0a95217927c786e22e3c813d733d0b2929ac0e3294bef1a89436c8f0025c719b` |
| `ctcc-v2_ctcc_v2_redis_data` | 5632 | `de8fd3e89d1a2afc4541eaaa83ae14c8aaa026e61f1687f779bbf77fa92ee9e0` |

The PostgreSQL archive was restored into a new private directory and started in
an isolated PostgreSQL 17 container with `--network none`. The server recovered,
read-only queries succeeded, and migration head was `0017`. The original tar's
hash was unchanged. The restored server was then stopped; no application,
exchange client or automated trading service ran against it.

The saved September 16 automation row had Arm=false, no active tracked trades,
no legacy active trade and 24 recorded realized events. Stored order rows were
62 filled and 35 canceled. The new qualification ledger had no rows. These are
historical local records, not a current account reconciliation, a verified
forensic result, or new-pipeline Demo samples. Current exchange exposure remains
unknown until authenticated fresh reconciliation.

Private raw archives remain outside source/evidence bundles in the isolated
preservation store. An additional encrypted copy is under
`%LOCALAPPDATA%\CTCC\PrivateCheckpoints\20260919`. Windows DPAPI CurrentUser
protects those files; decrypt-and-hash readback matched both archive identities.
Restoration requires the preserving Windows profile. Credential-bearing database
archives and protected copies are excluded from Git, reports and release ZIPs;
the public checkpoint retains only identities, aggregate counts and verification
results.

Rollback is a controlled recovery operation: verify the exact preserved source,
database archive and dependency identities, restore into an isolated target,
retain durable safety/uncertain records, and start with Live and Demo entry writes
disabled and Arm=false. Reconcile current account identity, positions, pending
orders, protection and unresolved intents before enabling any entry authority.
Never restore old Arm, auto-close exposure, cancel protection, or replay an
unresolved intent merely because an earlier database copy appears flat.
