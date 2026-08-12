# CTCC-V2 MIE Gate 0 Rebaseline

Date: 2026-08-12

This report freezes the baseline used for the MIE contract extraction. It does
not grant Demo or Live execution authority.

## Repository identity

- Canonical repository: `holy1080111-cmd/CTCC-V2`
- Canonical branch: `develop/v1.6.8`
- Operator-verified local and remote commit:
  `58369615492e6d361e2550adb07674615c2db27d`
- Operator-verified worktree state: clean and synchronized with
  `origin/develop/v1.6.8`
- Reconstruction source: the frozen v1.6.8 bundle plus the verified Gate 8 and
  Gate 8B changes retained in the handoff workspace.

The GitHub connector available to this verification environment has no
installed account, so it cannot independently read the private repository.
The canonical commit above therefore remains operator evidence. The isolated
reconstruction is accepted only for contract-only development and local
regression; it is not evidence for a Live promotion.

## Runtime evidence

The operator supplied the following successful runtime evidence before this
freeze:

- Docker Desktop Linux engine running.
- `ctcc-v2-api` healthy on `127.0.0.1:8100`.
- PostgreSQL and Redis healthy.
- Alembic current/head: `0012`.
- `verify_v168_live_boundary.ps1` completed with exit code 0.
- Canonical manifest passed with 282 source files.
- Gate 8B read-only account-basis probe completed without an exchange write.

The isolated reconstruction additionally passed:

- canonical source manifest: 282 files;
- Python byte-code compilation;
- 331 unit tests;
- Git whitespace validation.

Docker/PostgreSQL integration remains an operator-runtime acceptance step
because this isolated environment does not share the Windows Docker daemon.

## Safety freeze

The source defaults and operator runtime evidence both keep these switches
disabled:

```text
AUTO_TRADE=false
LIVE_TRADING=false
PAPER_AUTO_EXECUTION=false
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
OKX_DEMO_SCORE_RISK_ENABLED=false
OKX_DEMO_SOAK_ALLOW_EXECUTE=false
```

MIE contracts and shadow traces must not contain an order, quantity, leverage,
exchange-write command, or execution authority.

## Existing mathematical baseline

The following Gate 8 / Gate 8B behavior is present:

- past-only causal quadratic trend and derivative estimation;
- robust causal state estimation;
- one-step prequential conformal return interval;
- shared mathematical confirmation with downward-only score caps;
- auxiliary evidence restricted to deterministic tie-breaking;
- score-tiered Demo risk ceilings;
- aggregate Demo stop-risk and margin ceilings;
- maximum three tracked Demo positions on different instruments;
- three consecutive confirmed losses engage the daily lock;
- ambiguous close attribution engages the emergency stop;
- account risk uses the verified settlement-currency equity basis;
- changing the equity basis requires a flat, untraded session.

## Gate 0 decision

Gate 0 is accepted for the following narrow next step:

1. create immutable MIE contracts;
2. create a legacy-to-MIE evidence adapter;
3. create a complete shadow trace contract;
4. add unit, property, serialization, and authority-boundary tests.

Gate 0 does not authorize:

- changing the existing strategy selection path;
- changing Demo sizing;
- connecting MIE to Paper, Demo, or Live execution;
- adding a database migration;
- enabling any write or automation flag.
