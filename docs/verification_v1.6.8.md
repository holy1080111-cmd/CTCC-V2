# v1.6.8 artifact verification

Performed in the artifact-generation environment:

```text
Uploaded archive SHA256: verified
Unsafe archive paths: none
Credential/private-key scan: none found
Python compileall: passed
Non-PostgreSQL regression suite: 236 passed (8 PostgreSQL tests deselected)
Live fault-injection tests: passed
Alembic graph at original v1.6.8 artifact generation: 0010 (head)
Canonical cross-platform manifest: passed
```

Additional adaptive Demo/calculus source validation in the patch-generation
environment:

```text
Python compileall: passed
Exact exponential log-velocity recovery: passed
Exact quadratic log-acceleration recovery: passed
Noise-confidence suppression: passed
Unconfirmed-candle exclusion: passed
Robust-state exact-trend recovery: passed
Robust-state noise and endpoint-shock suppression: passed
Causal conformal prequential-coverage probes: passed
Mathematical fusion direction/conflict/instability probes: passed
Analytical/prequential/auxiliary separation probes: passed
Randomized auxiliary non-escalation paths: 100 passed
True-tie-only strategy ranking tests: source added; operator pytest pending
Pure cross-module mathematical assertions: 18 passed
Randomized causal numerical paths: 100 passed
Non-finite input and constant-series fail-closed probes: passed
Static Python compileall and Git whitespace check: passed
Canonical manifest: passed (281 files)
Docker/pytest/PostgreSQL: not available; operator gate still required
```

The Live fault-injection suite covers duplicate intent keys, single-attempt
transport failures, malformed or empty write acknowledgements, non-final order
states, ambiguous-order Emergency Stop, missing-protection Emergency Stop, no
silent close, flat-start Arm, Arm expiry, auto-disarm, cancel, close, read/write
transport separation, and one-shot automation.

Not performed in the artifact-generation environment:

```text
Docker Compose image build
PostgreSQL online migration through current head (now 0012)
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
