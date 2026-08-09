# v1.6.8 artifact verification

Performed in the artifact-generation environment:

```text
Uploaded archive SHA256: verified
Unsafe archive paths: none
Credential/private-key scan: none found
Python compileall: passed
Non-PostgreSQL regression suite: 236 passed (8 PostgreSQL tests deselected)
Live fault-injection tests: passed
Alembic graph: 0010 (head)
Canonical cross-platform manifest: passed
```

The Live fault-injection suite covers duplicate intent keys, single-attempt
transport failures, malformed or empty write acknowledgements, non-final order
states, ambiguous-order Emergency Stop, missing-protection Emergency Stop, no
silent close, flat-start Arm, Arm expiry, auto-disarm, cancel, close, read/write
transport separation, and one-shot automation.

Not performed in the artifact-generation environment:

```text
Docker Compose image build
PostgreSQL online migration 0009 -> 0010
Alembic online schema-drift check
PostgreSQL integration tests
Authenticated OKX production read reconciliation
Real-money micro-order submission
Independent verification of live TP/SL in the OKX UI
```

Those checks require the operator's local Docker services, retained PostgreSQL
volume, network location, and private OKX credentials. They must not be reported
as passed until completed locally. Source tests never contact OKX production or
place a real order. The packaged Docker verification explicitly overrides its
test process to `ENVIRONMENT=test`, `TRADING_MODE=analysis_only`, and disables
every Paper, Demo, and Live write/automation switch even if the deployment
`.env` has since enabled production execution.
