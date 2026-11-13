# DB0056 Demo source preflight

Run from a reviewed CTCC source directory using the private environment of the
configured source runner.

```text
python -B -m scripts.inspect_demo_source_profile_0056 --preflight-only --instrument BTC-USDT-SWAP
```

The command checks these settings and components.

| Stage | Successful check |
| --- | --- |
| `settings` | Runtime settings parsed successfully. |
| `native_profile` | The native-only production Demo profile is enabled and its execution, reconciliation, scheduler and outbox switches are off. |
| `private_evidence_root` | `OKX_DEMO_NATIVE_EVIDENCE_ROOT` points to an existing owner-private directory outside the source tree. |
| `postgres_tls_configuration` | `DATABASE_URL`, `OKX_DEMO_NATIVE_CUSTODY_DATABASE_URL` and `OKX_DEMO_NATIVE_FRAME_READER_DATABASE_URL` select three roles on one route. The `CTCC_NATIVE_PG_TLS_CA_FILE` contents match the reviewed `CTCC_NATIVE_PG_TLS_CA_SHA256`. |
| `restricted_postgres_roles` | The three configured roles pass the read-only DB0056 catalog and privilege witness. |
| `demo_source_configuration` | All three Demo credential fields are set, the approved Global route and selected instrument match, and `OKX_DEMO_READ_MAX_RETRIES` is zero. |

An observation of the PostgreSQL roles can still run when the exchange
credentials are absent. This command makes zero exchange requests and does
not create source evidence directories, start the API, or run migration or
role grants. It requires no database owner login. Route, certificate, account
and credential values are omitted from its output.

Exit `2` with `OBSERVED_DENY` means all checks passed. Exit `1` means any
stage failed, was unavailable, or the command line was invalid. The authority
fields always show `admission=DENY`, and the credentials check only confirms
local configuration. Verification of the configured account uses the separate
`scripts.observe_demo_current_source_0056` command. The original native-only
Compose overlay and DB0055 provisioner remain separate.
