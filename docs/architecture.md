# CTCC V2 architecture — v1.5

## Execution boundaries

```text
Public OKX market data
→ deterministic analysis/strategy/risk
→ local Paper broker and persistence
```

The OKX Demo broker is a separate exchange boundary:

```text
authenticated Demo request
→ CTCC safety gates
→ OKX Demo REST
→ exchange reconciliation
→ PostgreSQL exchange-state mirror
```

The controlled execute-soak layer supervises the explicitly armed Demo
automation:

```text
operator arm
→ execute-soak preflight
→ bounded scheduled runs
→ exchange exposure/protection/equity reconciliation
→ automatic disarm or emergency safety stop
```

The v1.5 performance layer is evidence-only and does not gain execution
authority:

```text
successful OKX Demo reconciliation
→ append-only equity snapshot
→ retained order and automation attribution
→ fee/funding/slippage/drawdown calculations
→ UTC daily report and reliability gates
→ authenticated operator review
```

## Authority rules

- The Paper engine is authoritative for local Paper state; PostgreSQL restores it.
- OKX Demo is authoritative for Demo orders, positions, protection, and balances.
- PostgreSQL stores the last successfully reconciled Demo state, append-only performance snapshots, operator strategy controls, reports, and soak telemetry.
- A REST write acknowledgement is not treated as final order state.
- A safety stop does not infer that exchange exposure has disappeared.

## Dependency rule

- `domain` defines validated request and response models.
- `exchange.okx` signs and transports OKX requests.
- `okx_demo.service` applies Demo write safety and reconciliation.
- `demo_automation.service` owns Arm, order submission, and trading locks.
- `observability.service` owns soak preflight, bounded-session safety, and watchdogs.
- `performance.service` derives evidence, persists daily reports, and exposes operator strategy controls without exchange-write authority.
- `database.repositories` persists exchange mirrors, automation state, soak sessions,
  observability events, performance snapshots, daily reports, and strategy controls.

## Safety

- Live execution remains unavailable.
- Execute soak cannot enable writes or arm itself.
- Execute soak requires a flat start, protection verification, a session loss
  budget, a submission cap, and automatic disarm.
- Missing protection or untracked exposure engages emergency stop but does not
  silently close a position.
- API remains single-worker because Arm and scheduler ownership are process-local.
- Performance validation never enables live trading and never auto-disables a strategy.
- Disabling a strategy affects future candidate selection only; existing positions and exchange orders are untouched.
