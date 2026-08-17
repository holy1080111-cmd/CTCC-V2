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
place a real order. The packaged Docker verification runs pytest through
`scripts/hermetic_pytest.py`. The helper preserves only `DATABASE_URL` and
`REDIS_URL`, removes all other `Settings` values inherited from the deployment,
then applies `ENVIRONMENT=test` and `TRADING_MODE=analysis_only`. It also
asserts that the resulting test process has no Paper, Demo, or Live execution
authority. The outer verifier separately checks the real running container
before sanitization, so a test override cannot conceal an enabled write path.

## External Benchmark Pack v2 acquisition gate

The source-validation environment verifies the network boundary with
`httpx.MockTransport`; it does not contact an external provider. Acceptance
covers predeclared SHA-256 and byte size, reviewed-host redirects, media type,
stream limits, no-clobber placement, identical-file idempotence, partial-file
cleanup, ZIP traversal/duplicate/nesting/expansion rejection, and an end-to-end
acquisition-to-v1-quality reference flow.

The operator must still run `scripts/verify_external_benchmark_pack.ps1` under
Docker/PostgreSQL with every write switch disabled. A real provider artifact is
not accepted until its terms review digest, official URL, byte size, and
checksum are independently supplied in the acquisition request. No external
artifact is bundled with CTCC and no source test performs a public download.

### Gate v2.1 Binance reference probe

Gate v2.1 fixes the first provider-specific reference to Binance USD-M
`BTCUSDT`, one-minute klines, UTC day 2024-01-01. Mock-backed verification must
prove that preparation performs only a bounded checksum GET followed by the
artifact HEAD, that cross-contract identity tampering is rejected, and that a
complete synthetic archive produces exactly 1,440 valid rows without any
promotion or execution authority.

After `EXTERNAL_BENCHMARK_PACK_V2_1_VERIFIED=1`, the operator may run
`scripts/run_binance_btcusdt_reference_probe.ps1`. It prints the live official
identity before requiring the exact phrase `ACQUIRE_REFERENCE_ONLY`; only then
does it download the pre-hashed public ZIP into an outside-repository data root
and generate immutable quality evidence. The source suite does not make this
public request, so `BINANCE_BTCUSDT_REFERENCE_PROBE_VERIFIED=1` must not be
reported until the local operator probe actually emits it.

The isolation contract is covered by `tests/unit/test_hermetic_pytest.py`,
including a deployment profile with 10% Demo portfolio risk, a 10% weekly
backstop, structural dynamic leverage, and Demo writes enabled. Those values
must be absent from the pytest process while the Docker database and Redis URLs
remain available to integration tests.

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

## Execution-price and leverage correctness follow-up

The combined source tree was revalidated after closing field-freshness,
executable-side quote, mark-basis, candle-close, rolling-PnL, tick-alignment,
capital-bucket leverage, leverage-response, actual-fill pending-Algo
protection, ongoing protection coverage, and close-attribution defects:

```text
Focused Demo/private-transport regression: 89 passed
Complete unit suite: 521 passed
Non-PostgreSQL suite: 533 passed (10 integration tests deselected)
Python compileall and Git staged/unstaged whitespace checks: passed
Canonical cross-platform manifest: passed (333 files)
No Demo or Live order submitted
```

Docker build/health, exact online migration `0013`, schema drift, the ten
PostgreSQL-marked tests, and authenticated read-only exchange probes remain
operator acceptance steps on the exact same reviewed tree.

## Reviewed eight-symbol Demo/public universe

The candidate-universe gate is separate from execution. With every Paper,
Demo, and Live authority switch false, the public-only qualification checks:

```text
BTC-USDT-SWAP  ETH-USDT-SWAP  SOL-USDT-SWAP  XRP-USDT-SWAP
DOGE-USDT-SWAP ADA-USDT-SWAP  LINK-USDT-SWAP LTC-USDT-SWAP
```

Each instrument must be a unique live USDT-settled SWAP with a valid book,
spread no greater than 8 bps, estimated 24-hour USDT notional of at least
10 million, minimum-order notional no greater than 25 USDT, and at least 200
confirmed non-stale 4H candles. The verifier also requires the running
WebSocket, Paper, Demo allowlist, and Demo scan configuration to equal the
reviewed universe while the Live lists remain inside the separate BTC/ETH
boundary.

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_multi_symbol_universe.ps1 `
  -RepoPath C:\CTCC-V2
```

Passing the market qualification is time-sensitive and must be repeated before
a later separately authorized Demo soak. It does not place an order, increase
portfolio exposure, prove profitability, or promote any additional instrument
into Live execution.
