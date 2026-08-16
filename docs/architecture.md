# CTCC V2 architecture — v1.6.8

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

The optional adaptive Demo portfolio remains inside that simulated boundary:

```text
reviewed eight-symbol Demo/public universe
→ independent per-symbol analysis
→ cross-symbol candidate ranking
→ shared causal mathematical fusion contract
→ robust state and conformal uncertainty
→ downward-only effective score
→ score risk/leverage tier
→ remaining portfolio risk and margin
→ one protected position per instrument
→ instrument-level close attribution
→ standard session locks or opt-in continuous eligibility
```

The universe contains BTC, ETH, SOL, XRP, DOGE, ADA, LINK, and LTC USDT
perpetual swaps. It broadens discovery only: it does not raise position,
capital-bucket, portfolio-risk, submission, or execution-authority limits.
Fresh instrument metadata must still be unique, live, SWAP, and USDT-settled
before Demo sizing. The Live boundary remains the separate BTC/ETH allowlist.

Continuous Demo eligibility skips daily-loss, daily trade-count,
consecutive-loss, and cooldown entry gates only. Weekly-loss, drawdown,
protection, portfolio/capital, reconciliation, Arm, submission, and Emergency
Stop boundaries remain in force. It never changes Live execution authority.

The separately opted-in structural dynamic-risk adapter remains below the
strategy decision and above Demo order construction:

```text
confirmed 15m/1H/4H swing bracket
→ structural stop plus volatility buffer
→ next-structure target
→ fee/slippage/funding-adjusted net RR
→ downward-only five-band score risk
→ account-risk / position-bucket required 3/5/8/10/20x leverage
→ isolated-margin Demo request
```

Missing structure or net RR fails closed. A 20x cap additionally requires
high-grade validated mathematics and derivative alignment. The exchange
leverage response and post-acknowledgement protection are confirmed before the
run remains eligible. Attached parameters alone are not confirmation: the
exchange pending-Algo row must match the generated client ID, instrument,
mark-trigger prices, and protected size. Every later reconciliation repeats
that coverage check while a position is open; mismatch engages a safety stop.
The adapter has no Live imports or authority.

The OKX Live boundary is isolated from Demo and Paper:

```text
authenticated production read
→ capability and account-identity pinning
→ PostgreSQL Live mirror
→ process-local short-lived Arm
→ durable idempotency intent
→ single protected production order
→ exchange reconciliation and automatic disarm
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

The v1.7 MIE extraction is a parallel, shadow-only boundary:

~~~text
confirmed fixed-horizon OHLCV window
→ deterministic Gate 2 feature snapshot (no runtime consumer)

frozen feature snapshot reference
→ versioned Evidence contracts
→ probability and regime contracts
→ model-health coverage
→ long/short/no-trade candidate
→ replayable shadow trace
~~~

MIE Gates 1 and 2 have no import from Paper, Demo, Live, exchange, or execution
packages, and existing runtime modules do not consume MIE. Every nested
contract fixes execution_authority=false; predictive validation may grant
decision-gate use in a later Gate but never exchange-write authority.

The external benchmark pack is a second isolated research boundary:

```text
reviewed public source metadata
→ immutable external dataset manifest
→ local artifact SHA-256 verification
→ explicit data-quality profile
→ reference-only formula and published-result records
```

Gate v1 has no network client, runtime consumer, database migration, or import
from MIE, strategy, risk, Paper, Demo, Live, exchange, or execution packages.
Every result fixes `promotion_eligible=false` and `execution_authority=false`.

## Authority rules

- The Paper engine is authoritative for local Paper state; PostgreSQL restores it.
- OKX Demo is authoritative for Demo orders, positions, protection, and balances.
- OKX production is authoritative for every Live order, position, protection,
  balance, and final state.
- PostgreSQL stores the last successfully reconciled Demo state, append-only performance snapshots, operator strategy controls, reports, and soak telemetry.
- A REST write acknowledgement is not treated as final order state.
- A safety stop does not infer that exchange exposure has disappeared.

## Dependency rule

- `domain` defines validated request and response models.
- `exchange.okx` signs and transports OKX requests.
- `okx_demo.service` applies Demo write safety and reconciliation.
- `okx_live.service` owns Live capability checks, account reconciliation,
  process-local Arm, durable write-intent gates, and production write safety.
- `okx_live.automation` may request one protected order only through
  `okx_live.service`; it has no direct exchange-write client.
- `demo_automation.service` owns Arm, order submission, and trading locks.
- `demo_automation.risk_profile` maps validated analysis-score ranges to Demo
  risk, leverage, and margin ceilings, and applies the downward-only shared
  mathematical grade without granting Live authority.
- `strategies.structural_protection` derives causal swing brackets;
  `demo_automation.structural_risk` applies cost-adjusted net RR and a
  quality-capped leverage ladder without granting write authority.
- `analysis.mathematical_core` is the single read-only fusion point for
  structure, momentum, derivative, state, conformal, volatility, and quality
  evidence.
- mie.contracts owns strict shadow Evidence, Forecast, Regime, ModelHealth,
  DecisionCandidate, and replay trace contracts.
- mie.adapters.legacy_mathematical translates the frozen mathematical core
  into correlated, downward-only MIE evidence without changing execution.
- `mie.features` owns strict fixed-horizon confirmed-bar inputs and pure
  statistics, signal, dynamics, momentum, and confirmed-geometry snapshots;
  Gate 2 has no runtime consumer or execution authority.
- `research.external_benchmarks` owns frozen public-source metadata, artifact
  identity, point-in-time dataset contracts, quality reports, and
  formula-parity records. It cannot fetch data or promote a model in Gate v1.
- `observability.service` owns soak preflight, bounded-session safety, and watchdogs.
- `performance.service` derives evidence, persists daily reports, and exposes operator strategy controls without exchange-write authority.
- `database.repositories` persists exchange mirrors, automation state, soak sessions,
  observability events, performance snapshots, daily reports, and strategy controls.

## Safety

- Live execution is disabled by default and requires independent configuration,
  authenticated runtime Arm, one-submission, protection, and reconciliation gates.
- The read-only Live transport permanently rejects non-GET requests; a separate
  execution transport is reachable only under production write settings.
- A Live Arm is never persisted or restored after restart.
- PostgreSQL advisory locking serializes Live writes across accidental duplicate
  API instances that share the same database.
- An ambiguous Live submission engages Emergency Stop and is never retried.
- Execute soak cannot enable writes or arm itself.
- Adaptive Demo sizing is disabled by default, enforces aggregate open-stop-risk
  and margin ceilings, and never reuses one instrument for multiple concurrent
  CTCC positions.
- Demo/Public symbol expansion is constrained to the reviewed eight-symbol
  mapping; configuration rejects duplicates, unknown instruments, scan symbols
  outside the allowlist, and automation scans missing a WebSocket subscription.
  The Live allowlist remains independently constrained to BTC and ETH.
- The mathematical gate uses confirmed past candles only; missing or conflicting
  evidence lowers coverage/consensus, shocks become instability, low-confidence
  evidence downgrades leverage, and opposed or unstable evidence blocks before
  leverage or order writes.
- Uncalibrated predictive structure/momentum evidence and failed conformal
  coverage remain auxiliary: they never add direction score, probability,
  leverage eligibility, or write authority. The separately gated structural
  adapter may use confirmed past-only swing prices as deterministic protection
  geometry; those anchors constrain stop, target, and the resulting worst-case
  sizing but are never interpreted as validated market alpha.
- Standard Demo sessions lock after three consecutive negative closes;
  continuous sessions skip that daily gate but retain true rolling seven-day
  close evidence and a non-daily high-water drawdown backstop. Ambiguous
  unknown close attribution engages Emergency Stop. Account equity deltas are
  never substituted for exchange-attributed trade PnL, even for one position.
- Execute soak requires a flat start, protection verification, a session loss
  budget, a submission cap, and automatic disarm.
- Missing protection or untracked exposure engages emergency stop but does not
  silently close a position.
- API remains single-worker because Arm and scheduler ownership are process-local.
- Performance validation never enables live trading and never auto-disables a strategy.
- Disabling a strategy affects future candidate selection only; existing positions and exchange orders are untouched.
- Public data and published benchmark results remain calculation references;
  they cannot increase score, risk, leverage, or execution authority.
