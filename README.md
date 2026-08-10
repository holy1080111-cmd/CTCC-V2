# CTCC V2 v1.6.8 — Controlled OKX Live Execution

CTCC V2 now has an isolated OKX production boundary that can reconcile and,
only after explicit multi-stage authorization, operate real OKX SWAP positions.
Paper and OKX Demo remain separate systems and cannot be enabled together with
Live execution.

```text
OKX Live authenticated reads
→ capability and account-identity pinning
→ atomic PostgreSQL mirror
→ process-local short-lived Arm
→ durable idempotency intent
→ exchange precheck and hard risk caps
→ one protected market order
→ exchange-state reconciliation
→ automatic disarm or emergency stop
```

## Safety boundary

The shipped defaults cannot place an exchange order:

```env
TRADING_MODE=analysis_only
AUTO_TRADE=false
LIVE_TRADING=false
OKX_LIVE_ENABLED=false
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
```

Production writes require all of the following at the same time:

- `ENVIRONMENT=production`, `TRADING_MODE=live`, and `WEB_CONCURRENCY=1`;
- an API token of at least 32 characters;
- a dedicated OKX key with Read + Trade, no Withdraw, and an IP binding;
- Live writes explicitly enabled in configuration;
- no Paper or Demo automatic execution;
- an authenticated runtime Arm with an exact confirmation phrase;
- a flat account start with no position, pending order, or pending Algo order;
- a protected SWAP market order within the symbol, size, notional, leverage,
  lot-size, tick-size, exchange max-size, and loss-budget limits;
- a short exchange-side request expiry on the actual order submission;
- a new durable idempotency key that has never been reserved before.

A PostgreSQL advisory lock also serializes the entire Live write section across
accidental duplicate API instances connected to the same database.

An Arm is held only in the API process, expires in at most 15 minutes (5 minutes
by default), allows exactly one order submission, and is never restored after a
restart. A write transport error is not retried. If an order may have reached
OKX but its result is ambiguous, CTCC engages Emergency Stop and does not submit
again.

Missing protection or untracked exposure never causes CTCC to silently close a
real position. The operator must reconcile the exchange and choose an explicit
cancel or close action.

## Persistence

Migration `0009` adds the isolated read-only Live account mirror. Migration
`0010` adds `okx_live_execution_intents`, which stores only request hashes,
identifiers, finite status values, and safe detail codes. It does not store API
credentials or raw request/response payloads. Migration `0011` adds JSONB state
for the disabled-by-default adaptive Demo portfolio and per-symbol cooldowns.

Expected migration after upgrade:

```text
0011 (head)
```

## Adaptive Demo portfolio (disabled by default)

The Demo automation can rank candidates by analysis score and, only when
`OKX_DEMO_SCORE_RISK_ENABLED=true`, use score-tiered leverage, stop-risk, and
margin caps. Different instruments may coexist; CTCC still permits at most one
tracked position per instrument because OKX cross-margin SWAP leverage is set
per instrument.

The default tiers are 72–79 (1x / 0.5% risk / 15% margin cap), 80–89
(2x / 0.75% / 20%), and 90–100 (3x / 1.0% / 25%). Aggregate open stop-risk is
capped at 2% of equity and estimated margin at 60%. These values are ceilings,
not targets: exchange lot rounding, stop distance, and the global notional cap
can produce smaller positions. Enabling the feature also requires the configured
daily loss limit to be at least as large as the aggregate open-risk ceiling.

Adaptive risk also requires a shared, past-only mathematical fusion contract.
It separates analytically checked derivative/state evidence and
prequentially checked conformal coverage from uncalibrated structure/momentum
evidence. The latter produces only a bounded auxiliary tie-break bonus and
cannot change eligibility, effective score, leverage, margin, or risk. The raw
strategy score is retained, while an `effective_score` can only be lower:
high-reliability validated alignment may retain 3x, moderate evidence is
capped at 2x, mixed or insufficient evidence is capped at 1x, and opposed or
unstable mathematics blocks the candidate before leverage or order writes.

Three consecutive negative closing outcomes lock new Demo entries for the
remainder of the UTC day. A profitable close resets the sequence; the UTC day
rollover resets it. With multiple positions, CTCC requires instrument-level
realized-PnL evidence and engages Emergency Stop rather than guessing from a
shared account-equity change.

See `docs/mathematical_core.md` and `docs/demo_adaptive_portfolio.md` for the
equations, exclusions, configuration, and rollout gates. This capability does
not expand the one-position, one-submission OKX Live boundary.

## Authenticated Live API

Every `/api/okx-live/*` endpoint, including status, requires `X-CTCC-Token`.

```text
GET  /api/okx-live/status
POST /api/okx-live/connectivity-check
GET  /api/okx-live/account-config
GET  /api/okx-live/balance
GET  /api/okx-live/positions
GET  /api/okx-live/orders/pending
GET  /api/okx-live/algo-orders/pending
GET  /api/okx-live/order-detail
POST /api/okx-live/reconcile

POST /api/okx-live/arm
POST /api/okx-live/disarm
POST /api/okx-live/emergency-stop
POST /api/okx-live/clear-emergency-stop
POST /api/okx-live/orders
POST /api/okx-live/orders/cancel
POST /api/okx-live/positions/close
POST /api/okx-live/leverage

GET  /api/okx-live/automation/status
POST /api/okx-live/automation/start
POST /api/okx-live/automation/stop
POST /api/okx-live/automation/run-once
```

Account IDs and raw OKX payloads are excluded from API response models. The
database mirror stores one-way account fingerprints so a changed account cannot
silently replace the original mirror.

## Upgrade and test

Keep the PostgreSQL volume:

Before running this regression gate, keep every Paper, Demo, and Live
write/automation switch disabled. The packaged script enforces that preflight
and runs pytest under an additional test-only environment override.

```powershell
cd C:\CTCC-V2
docker compose down
docker compose up -d --build
docker compose exec -T api alembic heads
docker compose exec -T api alembic current 2>&1
docker compose exec -T api alembic check
docker compose exec -T `
  -e ENVIRONMENT=test `
  -e TRADING_MODE=analysis_only `
  -e AUTO_TRADE=false `
  -e LIVE_TRADING=false `
  -e PAPER_AUTO_EXECUTION=false `
  -e OKX_LIVE_ENABLED=false `
  -e OKX_LIVE_ALLOW_ORDER_WRITES=false `
  -e OKX_LIVE_AUTO_RECONCILE_ON_START=false `
  -e OKX_LIVE_AUTO_EXECUTION=false `
  -e OKX_DEMO_ALLOW_ORDER_WRITES=false `
  -e OKX_DEMO_AUTO_EXECUTION=false `
  -e OKX_DEMO_SOAK_ALLOW_EXECUTE=false `
  api python -m pytest -q -p no:cacheprovider
docker compose run --rm --no-deps `
  --volume "${PWD}:/source:ro" `
  api python /source/scripts/manifest.py --root /source --check
```

The same sequence is packaged for Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\verify_v168_live_boundary.ps1
```

Do not use `docker compose down -v` during an upgrade.

PowerShell 5.1 may render Alembic INFO lines written to stderr as
`NativeCommandError` when `$ErrorActionPreference = "Stop"`. Capture the native
exit code and combined output; do not interpret the INFO line alone as a failed
migration.

## Staged real-account activation

Do not jump directly from installation to automation:

1. Use a dedicated, IP-bound Read + Trade key with Withdraw disabled.
2. Enable Live read mode only and run `verify_okx_live_readonly.ps1`.
3. Review the account fingerprint pin, equity, positions, orders, and Algo
   orders in OKX itself.
4. Enable Live writes while leaving Live automation false.
5. Run one operator-controlled micro order with
   `execute_okx_live_micro_order.ps1`; independently verify fill and TP/SL on
   OKX.
6. Explicitly close or cancel through the operator endpoint if required and
   reconcile again.
7. Only after the manual evidence passes, enable Live automation and first use
   `run_okx_live_automation_once.ps1`. Scheduled automation remains separately
   armed and process-local.

See [docs/live_execution_v1.6.8.md](docs/live_execution_v1.6.8.md) for the exact
configuration and runbook.

## Existing Demo and Paper systems

The v1.5 Demo reliability, performance reports, controlled Demo soak, operator
strategy controls, and deterministic Paper broker remain available. Their
settings and tables are unchanged, but their write or automation switches must
be off in Live mode.
