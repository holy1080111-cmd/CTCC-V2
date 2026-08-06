# CTCC V2 v1.5 — Demo Reliability & Performance Validation

v1.5 adds durable OKX Demo performance evidence on top of the verified v1.4
controlled execution soak.

```text
OKX Demo reconciliation
→ append-only equity snapshots
→ filled-order and realized-PnL extraction
→ fee, funding, and adverse-slippage analysis
→ UTC daily reports
→ strategy-level review metrics
→ operator-only strategy enable/disable controls
→ reliability validation with explicit sample-size gates
```

## Safety boundary

This release remains Demo-only. It does not contain a live-money broker.

```env
AUTO_TRADE=false
LIVE_TRADING=false
TRADING_MODE=okx_demo
```

Performance endpoints are read-only with respect to exchange exposure. Capturing
an equity snapshot calls OKX Demo reconciliation, but it does not place, cancel,
or close an order.

Automatic strategy disabling is deliberately forbidden:

```env
OKX_DEMO_STRATEGY_AUTO_DISABLE=false
```

A strategy can be disabled only through an authenticated operator request with
an exact confirmation phrase. Disabling a strategy affects future candidate
selection only; it never closes or modifies an existing position.

## Added persistence

Migration `0008` adds:

```text
demo_performance_snapshots
demo_daily_performance_reports
demo_strategy_controls
```

Every successful OKX Demo reconciliation appends an equity snapshot. Historical
OKX Demo order rows and automation-run attribution are used to derive:

- realized PnL samples;
- recorded fees, rebates, and funding fees;
- adverse slippage against the automation reference price;
- win rate, profit factor, expectancy, and equity drawdown;
- per-strategy review recommendations.

Metrics are evidence from the available Demo records. They are not a guarantee
of future profitability and they do not authorize live execution.

## New API

All endpoints require `X-CTCC-Token`.

```text
GET  /api/demo-performance/summary
GET  /api/demo-performance/validation
POST /api/demo-performance/snapshot/capture
GET  /api/demo-performance/daily/{YYYY-MM-DD}
GET  /api/demo-performance/strategies
POST /api/demo-performance/strategies/{strategy}/disable
POST /api/demo-performance/strategies/{strategy}/enable
```

Daily report dates use UTC.

## Upgrade from v1.4

Stop without deleting PostgreSQL volumes:

```powershell
cd C:\CTCC-V2
docker compose down
```

Do not use `-v`.

Back up the current folder, extract this release as `C:\CTCC-V2`, and copy the
old `.env` into the new folder. Set:

```env
APP_VERSION=1.5.0

OKX_DEMO_PERFORMANCE_WINDOW_DAYS=30
OKX_DEMO_PERFORMANCE_SNAPSHOT_RETENTION_DAYS=90
OKX_DEMO_PERFORMANCE_SNAPSHOT_QUERY_LIMIT=50000
OKX_DEMO_PERFORMANCE_ORDER_QUERY_LIMIT=10000
OKX_DEMO_PERFORMANCE_MIN_ACTIVE_DAYS=7
OKX_DEMO_PERFORMANCE_MIN_REALIZED_TRADES=20
OKX_DEMO_PERFORMANCE_MAX_AVERAGE_SLIPPAGE_BPS=10
OKX_DEMO_PERFORMANCE_MIN_PROFIT_FACTOR=1.0
OKX_DEMO_PERFORMANCE_MAX_DRAWDOWN_PCT=0.02
OKX_DEMO_STRATEGY_REVIEW_MIN_TRADES=5
OKX_DEMO_STRATEGY_REVIEW_MIN_WIN_RATE=0.35
OKX_DEMO_STRATEGY_AUTO_DISABLE=false

WEB_CONCURRENCY=1
AUTO_TRADE=false
LIVE_TRADING=false
PAPER_AUTO_EXECUTION=false
```

For installation verification, keep automatic Demo execution disabled:

```env
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
OKX_DEMO_SOAK_ALLOW_EXECUTE=false
```

Build, migrate, and test:

```powershell
cd C:\CTCC-V2
docker compose up -d --build
docker compose exec api alembic current
docker compose exec api pytest -p no:cacheprovider
```

Expected migration:

```text
0008 (head)
```

## Read-only verification

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_performance.ps1 `
  -WindowDays 30
```

The script captures one read-only reconciliation snapshot, checks the summary,
validation, and eight strategy-control records, and confirms that position,
pending-order, and Algo-order counts did not change.

## Daily report

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\generate_demo_daily_report.ps1
```

The JSON report is saved under `reports\` by default.

## Operator strategy control

Example:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\set_demo_strategy_control.ps1 `
  -Strategy trend_pullback `
  -Action disable `
  -Reason "Review after sufficient negative Demo sample"
```

The script requires the exact interactive phrase `DISABLE_DEMO_STRATEGY` or
`ENABLE_DEMO_STRATEGY`.

## Reliability validation

`/api/demo-performance/validation` does not return a live-trading approval. It
only checks whether the configured Demo evidence gates are met:

- minimum active days;
- minimum realized-trade samples;
- maximum average adverse slippage;
- minimum profit factor;
- maximum observed equity drawdown.

A result of `reliability_ready=false` is normal until enough Demo data exists.
Do not lower thresholds merely to make the field become true.

## Limitations

- Private account reconciliation remains REST-based.
- Performance attribution depends on retained OKX order fields and CTCC client
  order IDs; unmatched orders are reported as `unattributed`.
- Flat or zero-PnL opening orders are not treated as closed trades unless they
  can be identified as closing/reduce-only orders.
- External Slack, email, and mobile push delivery are not included.
- No live-money execution adapter exists in this release.
