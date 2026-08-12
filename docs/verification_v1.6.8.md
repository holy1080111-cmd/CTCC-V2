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
PostgreSQL online migration through current head (now 0013)
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

## 2,000 USDT Demo capital-bucket gate

Performed in the isolated source-validation environment on top of MIE Gate 1:

```text
Exact Decimal threshold cases (0.01 through 10,000 USDT): passed
1,000 deterministic randomized partition paths: passed
Available-equity monotonicity and global-notional cap properties: passed
Below-2,000 one-slot service path: passed
4,998.339 USDT two-complete-slot service path: passed
Score-selected 1x/2x/3x write-path sizing: passed
Stop-risk and exchange-availability downward-only behavior: passed
Pooled USD equity-basis rejection: passed
Active-margin overrun dry-run block: passed
Active-margin overrun execute-path Emergency Stop: passed
Legacy percentage-margin mode compatibility: passed
Capital-bucket/settings/automation targeted suite: 113 passed
Complete unit suite: 405 passed
Non-PostgreSQL suite: 417 passed
Python compileall and Git whitespace check: passed
```

The source-validation environment has no Docker daemon or PostgreSQL service,
so the 10 PostgreSQL-marked tests, Docker image rebuild, Alembic online check,
runtime `.env` safety probe, canonical source mount check, and authenticated
read-only Demo dry-run remain mandatory installer gates. No exchange order was
submitted by these source tests. The fixed 2,000 USDT value is an
operator-selected constraint; the checks validate its arithmetic and safety
behavior, not economic optimality or future returns.

## Optional continuous Demo-session gate

The disabled-by-default continuous-session path is accepted only when score
risk, capital buckets, and protected orders are required, the configured
post-close cooldown is zero, and all write authority remains disabled during
verification. Its tests must prove both sides of the boundary:

```text
Continuous mode ignores stale daily-loss, trade-count, and streak locks
Continuous mode bypasses the post-close symbol cooldown
Standard mode still enforces daily loss and all three frequency gates
Weekly-loss and drawdown backstops remain active in continuous mode
Candidate fingerprint and per-run submission limits remain active
Telemetry counters remain persisted and visible
All Demo and Live write flags remain disabled during verification
```

The authenticated runtime dry-run adds `-ExpectContinuousSession` to verify the
status contract and still calls `run-once` with `execute=false`. It cannot prove
profitability and must not be reported as an exchange execution test.

Performed in the isolated source-validation environment:

```text
Settings + Demo-automation targeted suite: 111 passed
Complete unit suite: 423 passed
Non-PostgreSQL suite: 435 passed
Python compileall and Git whitespace check: passed
Canonical cross-platform manifest: passed (307 files)
No Demo or Live order submitted
```

Docker, PostgreSQL/Alembic online checks, and the authenticated runtime dry-run
remain operator gates because the source-validation environment has no Docker
daemon or private OKX credentials.

## Structural dynamic-risk Gate

Performed in the isolated source-validation environment with every exchange
write path unused:

```text
Confirmed long/short structural bracket and missing-structure paths: passed
Cost-unit and net-RR equations: passed
Five score/risk/leverage bands and 20x downgrade paths: passed
Isolated-margin Demo request construction: passed
Exact 150-USDT full-equity bucket-ceiling fixture: passed
Rolling seven-day close pruning/deduplication: passed
Non-daily equity high-water invariant: passed
Settings dependency and fail-closed defaults: passed
Complete unit suite: 467 passed
Non-PostgreSQL suite: 479 passed (10 integration tests deselected)
Alembic graph/offline 0012 -> 0013 SQL: passed
Python compileall and Git whitespace check: passed
Canonical cross-platform manifest: passed (315 files)
No Demo or Live order submitted
```

Docker build/health, PostgreSQL migration `0013`, online schema drift,
PostgreSQL integration tests, and the authenticated execute=false structural
dry-run remain operator gates. See
`docs/verification_demo_structural_dynamic_risk.md`.
