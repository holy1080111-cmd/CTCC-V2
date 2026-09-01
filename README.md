# CTCC V2 v1.6.9 — Durable OKX Live Recovery

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

For Demo automation, attached TP/SL parameters or order-detail echoes are not
treated as proof of protection. CTCC requires the active pending Algo returned
by OKX to match its unique protection client ID, instrument, mark-trigger
prices, and covered size, and repeats that check on later reconciliations.
The runtime watchdog refreshes automation tracking after its exchange
reconciliation round-trip. Only an actively running submission receives the
bounded reconciliation grace; genuine untracked exposure outside that window,
or exposure that remains untracked after expiry, still engages Emergency Stop.

## Persistence

Migration `0009` adds the isolated read-only Live account mirror. Migration
`0010` adds `okx_live_execution_intents`, which stores only request hashes,
identifiers, finite status values, and safe detail codes. It does not store API
credentials or raw request/response payloads. Migration `0011` adds JSONB state
for the disabled-by-default adaptive Demo portfolio and per-symbol cooldowns.
Migration `0012` records the exact equity basis used by Demo risk controls so
an account-mode change cannot silently reuse an incompatible daily baseline.
Migration `0013` stores attributed rolling realized-PnL events and a separate
non-daily equity high-water mark for weekly-loss and drawdown backstops.
Migration `0014` separates explicitly based Demo strategy equity from OKX
multi-asset account `totalEq`; legacy snapshots remain unbased and are excluded
from reliability validation rather than being backfilled. Migration `0015`
applies the same explicit equity identity to controlled execute-soak loss
limits and persists its basis and currency with every soak session. Migration
`0016` persists exact Live protection expectations, operator-reviewed intent
resolution, and a versioned safety latch that survives API restarts.

Expected migration after upgrade:

```text
0016 (head)
```

## Reviewed Demo and public-data universe

Public WebSocket, Paper scanning, and OKX Demo scanning use one reviewed
eight-instrument USDT-SWAP universe:

```text
BTC-USDT-SWAP  ETH-USDT-SWAP  SOL-USDT-SWAP  XRP-USDT-SWAP
DOGE-USDT-SWAP ADA-USDT-SWAP  LINK-USDT-SWAP LTC-USDT-SWAP
```

This expands candidate discovery, not exchange authority or simultaneous
exposure. Candidates are ranked across the complete scan by downward-adjusted
mathematical score, raw score, validated confidence, and then the bounded
auxiliary tie-break. Existing position-count, 2,000-USDT capital-bucket,
portfolio stop-risk, margin, duplicate, protection, Arm, and per-run submission
limits still decide whether any candidate may proceed.

Immediately before Demo sizing, instrument metadata must be unique, `SWAP`,
`live`, and USDT-settled. Settings reject duplicate or unreviewed symbols and
require Demo scans to remain inside both the Demo allowlist and, for automation,
the active public WebSocket subscription. The OKX Live boundary remains
strictly limited to BTC and ETH and is not expanded by this universe.

Run `scripts/verify_demo_multi_symbol_universe.ps1` with every execution switch
disabled to requalify live instrument state, spread, estimated 24-hour USDT
notional, minimum-order notional, order-book presence, and confirmed 4H history.
See `docs/demo_multi_symbol_universe.md` for the exact policy and rollout gate.

## External benchmark evidence (reference-only)

The external benchmark package freezes official public-source identity,
point-in-time metadata, artifact hashes, data-quality profiles, published
results, and formula-parity calculations. Gate v2 adds an operator-only HTTPS
acquisition tool that requires the expected SHA-256 and byte size before a
download, revalidates every redirect host, rejects unsafe ZIP metadata, and
places a verified artifact without overwriting an existing path.

Gate v2.1 adds the first provider-specific flow: it freezes Binance USD-M
`BTCUSDT` one-minute data for 2024-01-01 from the official sibling checksum and
HEAD metadata, shows the complete identity before an exact operator
confirmation, then performs a bounded ZIP download and a strict 1,440-row
quality profile. Raw data and evidence stay outside Git.

Gate v3 adds a separate frozen BTCUSDT/ETHUSDT batch: three non-overlapping
30-day windows, 180 pre-hashed daily ZIPs, and 259,200 expected minute rows.
It emits deterministic data-quality, return-path, Theil-Sen trend, and path
efficiency evidence. The recent window is retrospective, and all trend labels
remain descriptive rather than predictive; no strategy or trading-cost model
is evaluated by this gate.

No API route or application runtime imports this package. External files and
published metrics cannot promote a model, change score/risk/leverage, or reach
Paper, Demo, Live, or exchange writes. See
`docs/external_benchmark_pack_v1.md`,
`docs/external_benchmark_pack_v2.md`, and
`docs/external_benchmark_pack_v2_1.md`, and
`docs/external_benchmark_pack_v3.md`.

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
can produce smaller positions. In standard session mode, enabling the feature
also requires the configured daily loss limit to be at least as large as the
aggregate open-risk ceiling.

Automated Demo entries are price-bounded `fok` orders, not unbounded market
orders. CTCC derives the most adverse fill that can still satisfy the configured
reward/risk floor, intersects it with
`OKX_DEMO_EXECUTION_MAX_ADVERSE_SLIPPAGE_BPS` (5 bps by default), aligns the
stricter result to the instrument tick, and sizes against that worst case. A
long can fill only at or below its cap; a short can fill only at or above its
floor. The complete order must fill inside the boundary or OKX cancels it.

After acknowledgement, CTCC requires a terminal full-fill state, exact
`accFillSz`, positive `avgPx`, confirmed attached mark-trigger TP/SL, and an
actual fill reward/risk that still meets the configured floor. A clean zero-fill
FOK is recorded as blocked with no active trade, but still consumes one order
submission allowance. Partial, ambiguous, price-bound violating, or below-floor
fills preserve any confirmed exchange protection and engage the safety lock;
CTCC never silently closes acknowledged exposure. This stronger boundary
deliberately trades fill rate for execution-quality safety.

An additional disabled-by-default Demo capital-bucket gate can replace the
percentage margin ceiling. With a verified USDT equity basis, an account at or
below 2,000 USDT forms one full-equity slot; above 2,000 USDT, only complete
2,000 USDT slots count, up to the configured position limit. Each order still
uses the lower of that slot, available USDT equity, score-tier stop-risk, the
global notional/contract caps, and exchange lot rounding. The bucket controls
estimated cross-margin sizing only; it does not isolate exchange collateral,
force the full amount to be consumed, authorize a write, or alter OKX Live.

The structural dynamic-risk extension is separately disabled by default. When
enabled, it uses complete 15m/1H/4H brackets made from confirmed swing
support/resistance, places a volatility buffer beyond the structural stop, and
uses the next confirmed structure as the target. It rejects candidates unless
reward/risk remains at least 2.0 after configured round-trip fees, slippage,
and funding. Those costs are included in position risk rather than reported
after sizing.

Its bands are 72–79 (1.5% risk, at most 3x), 80–89 (2.5%, 5x), 90–94
(3%, 8x), 95–97 (4%, 10x), and 98–100 (6%, 20x). CTCC selects the smallest
ladder leverage needed to fund the account-level risk ceiling from the current
position-margin bucket after structural stop distance and costs. Above 2,000
USDT this calculation includes `account equity / 2,000`; requirements above
the approved cap are reported but never executed above 20x. A 20x result
also requires confirmed high-grade mathematics, confidence/reliability at
least 0.65, instability no higher than 0.20, confirmed derivative alignment,
net-RR approval, isolated margin, and all normal safety gates. A missing 20x
quality condition caps leverage at 10x; missing structure or insufficient net
RR blocks the candidate.

Before an order, the OKX leverage response must echo the intended instrument,
margin mode, position side, and leverage. After acknowledgement, attached or
pending mark-trigger TP/SL must be confirmed. Either mismatch stops automation;
confirmed exchange exposure is retained for operator reconciliation and is
never silently closed.

At or below 2,000 USDT, one bucket is capped by available risk equity. Above
2,000 USDT, each complete 2,000-USDT bucket creates one possible slot, subject
to the configured position limit. A bucket remains a ceiling, not an order to
consume all margin. Risk sizing, costs, exchange limits, and portfolio risk can
produce a smaller allocation. Structural orders use isolated margin.

For OKX single-currency margin accounts, USDT-settled automation uses only the
USDT detail equity and `details[].availEq` as its risk and availability basis;
BTC, ETH, OKB, and account-level USD valuation are not pooled into USDT buying
power. Multi-currency and portfolio-margin accounts instead use adjusted
account equity and account-level available equity. The raw account `totalEq`
remains visible for reporting but never substitutes for missing available
margin.

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

An optional, disabled-by-default Demo continuous-session mode removes the
daily realized-loss, daily trade-count, consecutive-loss, and post-close
cooldown entry gates. It
does not create an unconditional order loop: every scheduled scan must still
produce a qualifying mathematical/strategy/risk decision, and duplicate,
position, capital-bucket, available-equity, weekly-loss, drawdown, protection,
reconciliation, submission-limit, Arm, and Emergency Stop gates remain active.
The mode requires a zero configured cooldown. The daily PnL and all skipped
counters remain visible for audit, but `OKX_DEMO_DAILY_LOSS_LIMIT_PCT` is not an
eligibility stop while continuous mode is active. It never changes the OKX Live
boundary.

See `docs/mathematical_core.md`, `docs/demo_adaptive_portfolio.md`, and
`docs/demo_structural_dynamic_risk.md` for the equations, exclusions,
configuration, and rollout gates. These capabilities do not expand the
one-position, one-submission OKX Live boundary.

## MIE shadow rearchitecture

MIE Gate 1 defines immutable evidence, forecast, regime, health, candidate,
and replay-trace contracts. Gate 2 adds a deterministic feature snapshot over
strict UTC, confirmed, fixed-horizon OHLCV bars: descriptive statistics,
causal signal processing, endpoint dynamics, normalized momentum, and delayed
confirmed geometry.

MIE remains disconnected from every Paper, Demo, Live, exchange, risk-sizing,
and order path. Gate 2 creates no probability, decision, migration, API route,
or execution authority, and it makes no predictive or profitability claim.
See `docs/mie_gate2_mathematical_features.md` and
`docs/mie_gate2_verification.md`.

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
GET  /api/okx-live/execution-intents/unresolved
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

Create the local secret file before the first start. `.env` is intentionally
not committed:

```powershell
Copy-Item .env.example .env
```

Set `POSTGRES_PASSWORD` and the password component of `DATABASE_URL` to the same
value. Percent-encode reserved URL characters in `DATABASE_URL`; do not encode
the standalone `POSTGRES_PASSWORD` value.

Back up the PostgreSQL volume before stopping or rebuilding services:

```powershell
cd C:\CTCC-V2
powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1
```

Keep the generated backup outside the container lifecycle and verify that it is
non-empty before continuing. Then keep the PostgreSQL volume during upgrade:

Before running this regression gate, keep every Paper, Demo, and Live
write/automation switch disabled. The packaged script enforces that preflight
and runs pytest under an additional test-only environment override.

```powershell
cd C:\CTCC-V2
docker compose down
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8100/readiness
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

The expected Alembic head and current revision are both `0016`. A healthy
container alone is not sufficient deployment evidence: `/readiness` and all
three Alembic commands above must also succeed. See the
[v1.6.9 Live operator runbook](docs/live_execution_v1.6.9.md) for rollback and
durable Emergency Stop recovery.

PowerShell 5.1 may render Alembic INFO lines written to stderr as
`NativeCommandError` when `$ErrorActionPreference = "Stop"`. Capture the native
exit code and combined output; do not interpret the INFO line alone as a failed
migration.

The reconciled history of reported CTCC-V2 defects, operator-command failures,
closed regressions, and still-unproven claims is maintained in
[docs/ctccv2_conversation_issue_audit.md](docs/ctccv2_conversation_issue_audit.md).

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

See [docs/live_execution_v1.6.9.md](docs/live_execution_v1.6.9.md) for the exact
configuration and runbook.

## Existing Demo and Paper systems

The v1.5 Demo reliability, performance reports, controlled Demo soak, operator
strategy controls, and deterministic Paper broker remain available. Their
settings and tables are unchanged, but their write or automation switches must
be off in Live mode.
