# Structural dynamic-risk Gate verification

## Scope frozen by this Gate

- confirmed 15m/1H/4H swing brackets;
- structural stop plus volatility buffer and next-structure target;
- notional-based fee, slippage, and funding estimates;
- cost-adjusted net RR and cost-inclusive risk sizing;
- score bands with downward-only mathematical caps;
- smallest-required 3/5/8/10/20x leverage and explicit 20x downgrade rules;
- isolated-margin Demo requests;
- 150-USDT / complete 2,000-USDT capital buckets;
- continuous eligibility without daily loss/count/streak/cooldown locks;
- rolling seven-day attributed PnL and non-daily equity high-water mark;
- migration `0013` and fail-closed defaults.

No OKX Live authority is changed. No installer or test enables Demo writes,
arms automation, or submits an order.

## Completed source-environment checks

```text
Python compileall: passed
Git whitespace check: passed
Unit regression: 467 passed
Non-PostgreSQL regression: 479 passed (10 integration tests deselected)
Alembic heads: 0013 (head)
Alembic 0012 -> 0013 offline SQL generation: passed
Canonical manifest: 315 files
```

The unit matrix includes long/short structure, missing structure, costs and
unit consistency, five risk bands, net-RR failure, 20x approval/downgrade,
isolated order construction, the exact 150-USDT bucket fixture, rolling PnL
pruning/deduplication, non-daily high-water behavior, and safe settings.

## Operator Docker/PostgreSQL acceptance still required

Run with every write switch disabled:

```powershell
cd C:\CTCC-V2
docker compose up -d --build
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_v168_live_boundary.ps1
```

The script must pass Docker health, migration/current head `0013`, schema drift,
targeted tests, PostgreSQL integration, full regression, whitespace, and the
canonical manifest. A source-only pass is not a substitute.

After the code gate passes, configure the structural dependencies but keep
`OKX_DEMO_ALLOW_ORDER_WRITES=false` and `OKX_DEMO_AUTO_EXECUTION=false`, rebuild,
and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_structural_dynamic_risk_dryrun.ps1
```

Only after both gates pass should an operator consider a separately armed,
bounded OKX Demo write test. Market profitability, liquidation probability,
and 150-to-2,000 timing remain unverified until out-of-sample and Demo execution
evidence exists.
