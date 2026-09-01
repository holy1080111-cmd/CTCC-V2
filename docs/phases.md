# Frozen implementation sequence

- v0.1 Foundation
- v0.2 Transaction Core and database schema
- v0.3 OKX public REST market data and data quality
- v0.4 Indicators, structure, and regime
- v0.5 Strategy evaluation
- v0.6 Risk engine
- v0.7 Paper broker
- v0.8 OKX public WebSocket realtime market
- v0.9 Auto Paper Orchestrator
- v1.0 Persistence and restart recovery (completed)
- v1.1 OKX Demo execution and reconciliation
- v1.2 Position-management hardening and monitoring
- v1.3 Demo soak and observability
- v1.4 Controlled Demo execution soak
- v1.5 Demo reliability and performance validation
- v1.6.8 Isolated OKX Live reads, real-position execution gates, and one-shot automation
- v1.6.9 Durable Live safety latch, exact protection, and scoped restart recovery
- Adaptive Demo portfolio gate: score-tiered leverage/risk/margin, multiple
  instruments, aggregate caps, shared causal mathematical fusion, robust state,
  prequential conformal coverage validation, auxiliary-score isolation, and
  three-stop UTC lock (operator acceptance pending)
- v1.7.0 MIE rearchitecture:
  - Gate 0 rebaseline and validation registry completed in isolated verification
  - Gate 1 immutable shadow contracts and legacy evidence adapter frozen after
    Windows Docker/PostgreSQL acceptance
  - Gate 2 deterministic statistics, signal, dynamics, momentum, and confirmed
    geometry feature core is shadow-only pending operator acceptance
  - later Gates remain shadow-only until independently frozen
- External Benchmark Pack:
  - Gate v1 immutable source/dataset/result contracts, artifact verification,
    strict quality profiling, and deterministic formula parity
  - Gate v2 operator-only, pre-hashed HTTPS acquisition with reviewed-host
    redirects, byte/media limits, atomic no-clobber placement, and ZIP safety
  - Gate v2.1 official Binance BTCUSDT 2024-01-01 identity preparation,
    operator-confirmed bounded acquisition, canonical one-minute parsing, and
    strict 1,440-row provider-quality evidence
  - Gate v3 frozen BTCUSDT/ETHUSDT development, validation, and retrospective
    holdout windows; 180 exact daily artifacts, 259,200 expected minute rows,
    and descriptive partition evidence with no strategy or cost evaluation
  - no runtime consumer, model promotion, database write, or execution authority
  - later gates add deterministic replay, execution calibration, and
    independently reviewed OOS validation
- v2.0 Final acceptance

## v1.0 completed

- Persistent Paper account, orders, positions and PnL
- Persistent orchestrator history and candidate fingerprints
- Startup recovery before realtime/scheduled execution
- Checksum reconciliation and audit records
- Backup, restore and restart-verification scripts

## v1.1 completed

- Authenticated OKX Demo REST reads and request signing
- Manual Demo order, cancel, close-position and leverage operations
- Attached TP/SL with strict local safety gates
- Exchange-authoritative PostgreSQL reconciliation
- Automatic OKX Demo execution and live execution remain unavailable


## v1.2 completed

- Explicitly armed OKX Demo automation
- One protected order per scan
- Daily loss, trade-count, consecutive-loss, cooldown, and duplicate locks
- Emergency stop and restart disarm
- Persistent automation state, runs, and fingerprints

## v1.3 completed

- Observation-only long-running soak sessions
- Heartbeat watchdog, metrics, durable events, and restart interruption detection

## v1.4 completed

- Controlled execute-soak preflight
- Flat-start, submission-cap, session-loss, and protection guardrails
- Emergency safety stop and automatic disarm on every execute-soak exit path
- Durable execute-soak equity, drawdown, protection, and exposure telemetry


## v1.5 completed

- Append-only Demo equity and exposure snapshots on successful reconciliation
- Realized PnL, recorded fee/rebate/funding, adverse-slippage, and drawdown analysis
- UTC daily performance reports persisted in PostgreSQL
- Strategy-level sample metrics and explicit review recommendations
- Authenticated operator enable/disable controls for future candidate selection
- Reliability evidence gates for active days, realized trades, slippage, profit factor, and drawdown
- Automatic strategy disabling and live-money execution remain unavailable

## v1.6.8/v1.6.9 implementation and local acceptance complete; exchange evidence pending

- Dedicated production REST transport with Demo header structurally absent
- Account capability validation and one-way identity pinning
- Atomic Live PostgreSQL mirror and durable execution-intent idempotency
- Production-only write settings, process-local expiring Arm, one submission, and auto-disarm
- Protected market-order precheck, contract/notional/leverage caps, bounded polling, and reconciliation
- Explicit cancel, close, leverage, Emergency Stop, and clear-stop flows
- Versioned PostgreSQL safety latch that survives API restarts
- Exact pending-Algo protection identity, geometry, coverage, and bounded
  propagation confirmation
- Scoped unresolved-intent recovery with repeated stable-flat exchange checks
  and compare-and-set latch clearing
- Zero-fill cancellation confirmation and serialized leverage rechecks
- One-shot strategy/risk automation that can write only through the Live service
- Disabled-by-default continuous Demo eligibility can skip daily loss,
  trade-count, consecutive-loss, and cooldown locks while retaining per-order
  protection, weekly-loss/drawdown, capital/portfolio, and execution boundaries
- Disabled-by-default structural Demo risk uses confirmed swing brackets,
  cost-adjusted net RR, isolated margin, and downward-only 3–20x leverage;
  migration 0013 persists true rolling close evidence and a non-daily high-water
  mark
- Local acceptance completed on 2026-09-01: PostgreSQL `0015 -> 0016 ->
  0015 -> 0016`, schema-drift detection, healthy Docker API/Redis/PostgreSQL,
  full hermetic regression, canonical manifest, and GitHub CI all passed from
  the reviewed v1.6.9 tree.
- Remaining real-money acceptance is deliberately external: a separately
  authorized, operator-controlled read-only account check and protected
  micro-order evidence. No verifier or release step may arm or submit it.

## v1.7.0 MIE Gate 0/1/2

- Gate 0 records the canonical v1.6.8 commit, migration 0012, 282-file
  manifest, fail-safe flags, and the current mathematical validation registry.
- Gate 1 extracts immutable Evidence, ProbabilityForecast, RegimeSnapshot,
  ModelHealth, DecisionCandidate, and MieShadowTrace contracts.
- The legacy mathematical adapter preserves causal/prequential/auxiliary
  distinctions and assigns one shared dependency group.
- Directional candidates require positive net EV and every logic check, but
  remain shadow-only and contain no order geometry.
- Gate 1 has no execution-side imports and does not change the legacy strategy,
  Demo, or Live paths.
- Gate 2 adds strict confirmed-bar inputs, deterministic replay/provenance, and
  five pure feature families without connecting MIE to an existing runtime
  caller.
- Gate 2 makes no predictive or profitability claim and adds no migration,
  probability model, decision, risk sizing, API, or exchange authority.
