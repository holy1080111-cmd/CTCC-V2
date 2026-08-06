# CTCC V2 v1.5 — Demo Reliability & Performance Validation

v1.5 records durable Demo account evidence and converts it into reproducible
performance and reliability reports. It does not add live execution.

## Added

- Append-only equity snapshots on successful OKX Demo reconciliation.
- UTC daily performance reports persisted in PostgreSQL.
- Realized PnL, recorded fee, rebate, funding-fee, and slippage analysis.
- Equity-curve maximum drawdown calculation.
- Strategy-level samples and review recommendations.
- Authenticated operator-only strategy enable/disable controls.
- Candidate filtering so disabled strategies cannot be selected for future Demo
  automation orders.
- Reliability validation with explicit minimum-data and quality thresholds.

## Migration

```text
0008_demo_reliability_performance.py
```

New tables:

```text
demo_performance_snapshots
demo_strategy_controls
demo_daily_performance_reports
```

No existing table is removed.

## Safe defaults

```env
OKX_DEMO_STRATEGY_AUTO_DISABLE=false
AUTO_TRADE=false
LIVE_TRADING=false
```

Strategy controls never close an existing position. Automatic strategy
quarantine is intentionally unavailable; review and disable actions require an
operator, authentication, a reason, and an exact confirmation phrase.

## Interpretation warning

Reliability validation is a Demo evidence gate, not an investment result and
not a live-trading authorization. Small samples, missing attribution, and
exchange field semantics are surfaced rather than silently filled in.
