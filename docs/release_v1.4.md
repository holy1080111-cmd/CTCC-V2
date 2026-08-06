# CTCC V2 v1.4 — Controlled Demo Execution Soak

v1.4 converts the previously generic execute-soak path into a bounded,
exchange-reconciled safety test. It still uses OKX Demo only and does not add a
live broker.

## Added controls

- Authenticated execute-soak preflight endpoint.
- Flat exchange exposure required before session start.
- Session-specific maximum number of submitted orders.
- Session equity and PnL tracking with a stricter session loss budget.
- Protection verification for active positions using pending Algo orders or
  attached Algo metadata from reconciled recent orders.
- Critical safety stop for untracked exposure, symbol mismatch, multiple
  positions, missing protection, loss-budget breach, or execution exceptions.
- Automatic disarm on completion, operator stop, error, restart interruption,
  and safety stop.
- Safety stops do not silently close a position. Existing exchange protection
  remains authoritative and the operator must review exposure.

## New API

```text
GET /api/demo-observability/soak/preflight
```

The existing soak status now also reports equity, session PnL, drawdown,
submission limits, protection checks, active exchange counts, automatic-disarm
state, and a safety-stop reason.

## New migration

```text
0007_controlled_demo_execution_soak.py
```

It extends `demo_soak_sessions`; no existing table is deleted.

## Safe defaults

```env
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
OKX_DEMO_SOAK_ALLOW_EXECUTE=false
OKX_DEMO_EXECUTION_SOAK_MAX_SUBMISSIONS=1
OKX_DEMO_EXECUTION_SOAK_LOSS_LIMIT_PCT=0.0025
OKX_DEMO_EXECUTION_SOAK_REQUIRE_FLAT_START=true
OKX_DEMO_EXECUTION_SOAK_REQUIRE_PROTECTION=true
OKX_DEMO_EXECUTION_SOAK_AUTO_DISARM=true
```

## Important limitation

This release proves only the behavior observed under the operator's Demo test
conditions. It does not prove strategy profitability and it does not authorize
live-money execution.
