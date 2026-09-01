# OKX Live v1.6.9 operator runbook

This runbook covers CTCC's real-money OKX SWAP boundary. Passing these gates is
not a trading recommendation and does not establish profitability. Never place
a Live order merely to test a deployment.

## 1. Prepare secrets and back up first

Create a dedicated, IP-bound OKX API key with Read and Trade permissions and
Withdraw disabled. Store credentials only in the local `.env` file.

```powershell
cd C:\CTCC-V2
Copy-Item .env.example .env
```

`POSTGRES_PASSWORD` is the literal PostgreSQL password. `DATABASE_URL` must use
the same password, with reserved URL characters percent-encoded. For example,
the literal password `p@ss%word` becomes `p%40ss%25word` only inside the URL.
Alembic accepts URL-encoded passwords, but it cannot detect a mismatch between
these two independently supplied values.

Before every upgrade, create a database backup while the current stack is still
available:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup.ps1
Get-ChildItem .\backups\ctcc-v1.6.9-*.dump
```

Move the backup to protected storage and verify it is non-empty. Do not run
`docker compose down -v`; deleting the PostgreSQL volume is not a recovery
method and can destroy account pins, idempotency history, and the safety latch.

## 2. Safe upgrade verification

Keep every execution switch disabled during the build and regression gate:

```env
TRADING_MODE=analysis_only
AUTO_TRADE=false
LIVE_TRADING=false
PAPER_AUTO_EXECUTION=false
OKX_LIVE_ENABLED=false
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
OKX_DEMO_SOAK_ALLOW_EXECUTE=false
```

Upgrade without removing volumes, then independently verify readiness and the
schema revision:

```powershell
docker compose down
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:8100/readiness
docker compose exec -T api alembic heads
docker compose exec -T api alembic current 2>&1
docker compose exec -T api alembic check
powershell -ExecutionPolicy Bypass `
  -File .\scripts\verify_v168_live_boundary.ps1
```

Both `alembic heads` and `alembic current` must report `0016`, `alembic check`
must report no new upgrade operations, and `/readiness` must return ready. The
Docker health status is necessary but does not replace these checks.

If migration or readiness fails, leave all write switches disabled, collect the
API and PostgreSQL logs, and restore only from a verified backup using the
operator's normal PostgreSQL restore procedure. Do not edit Alembic revision
tables by hand.

## 3. Read-only activation

After the offline gate passes, enable Live authenticated reads only:

```env
APP_VERSION=1.6.9
ENVIRONMENT=production
WEB_CONCURRENCY=1
TRADING_MODE=live
AUTO_TRADE=false
LIVE_TRADING=false
OKX_LIVE_ENABLED=true
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
PAPER_AUTO_EXECUTION=false
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
```

Run `scripts/verify_okx_live_readonly.ps1`. Confirm the expected account,
fingerprint pin, positive equity, positions, regular orders, and Algo orders in
both CTCC and the OKX UI. Reads must show an IP-bound key with Read + Trade and
without Withdraw permission.

## 4. Durable safety latch and unresolved intents

In v1.6.9, a safety event is stored in PostgreSQL with a monotonically changing
version. Restarting the API does not clear it. Cancel and explicit close actions
remain available for reducing exposure, but Arm, order placement, and leverage
changes stay blocked.

First inspect OKX directly. Then obtain CTCC's exact unresolved-intent snapshot:

```powershell
$headers = @{ "X-CTCC-Token" = $env:CTCC_API_TOKEN }
$unresolved = Invoke-RestMethod `
  -Headers $headers `
  -Uri http://127.0.0.1:8100/api/okx-live/execution-intents/unresolved
$unresolved | ConvertTo-Json -Depth 5
```

Each returned item contains `idempotency_key`, `status`, and `updated_at`. Do not
edit or omit items: the clear request is an exact compare-and-set snapshot. If
the list is non-empty, the request requires both confirmation phrases:

```powershell
$payload = @{
  confirmation = "CLEAR_OKX_LIVE_STOP"
  expected_unresolved_intents = @($unresolved)
  unresolved_confirmation = "RECONCILE_OKX_LIVE_UNRESOLVED_INTENTS"
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Method Post `
  -ContentType application/json `
  -Headers $headers `
  -Body $payload `
  -Uri http://127.0.0.1:8100/api/okx-live/clear-emergency-stop
```

If no unresolved intent exists, send an empty
`expected_unresolved_intents` array and omit `unresolved_confirmation`.

The server does not trust the operator snapshot alone. Under the global Live
execution lock it repeatedly reconciles the same account, requires zero
positions, pending regular orders, and pending Algo orders, and verifies that
each referenced order remains terminal or absent across the configured stable
window. It then resolves the exact unchanged intent set and clears the same
latch version. Any concurrent intent, latch, account identity, order-state, or
exposure change aborts the clear.

After success, fetch `/api/okx-live/status`, the unresolved-intent endpoint, and
OKX itself again. The unresolved list must be empty. A failed clear is a signal
to investigate; do not delete rows, reset the volume, recycle idempotency keys,
or repeatedly retry against changing exchange state.

## 5. Manual and automated execution gates

Only after the read-only evidence and safety-latch state are reviewed may the
operator enable Live writes. Keep automation disabled, use one reviewed symbol,
1x leverage, and a genuinely minimal valid contract size. Run
`scripts/execute_okx_live_micro_order.ps1`, then independently verify the fill
and the exact active OKX OCO protection in the OKX UI.

CTCC requires a unique active protection Algo with the expected client ID,
instrument, side, position side, margin mode, mark-trigger TP/SL geometry,
market execution prices, and covered size. A delayed OKX Algo appearance is
bounded-polled; duplicates or mismatches engage the durable safety latch.

Do not retry an ambiguous submission. A canceled order with any accumulated
fill is also ambiguous, not a confirmed zero-fill cancellation. Reconcile the
exchange first and use only explicit exposure-reducing actions as necessary.

Live automation remains a separate final gate. Start with a non-executing scan,
then use `scripts/run_okx_live_automation_once.ps1`. Arm is process-local,
short-lived, and single-use; neither Arm nor the scheduler is restored after a
restart.

## Incident checklist

- Keep write and automation switches disabled until the incident is resolved.
- Verify the account, positions, regular orders, Algo orders, and fills in OKX.
- Treat REST acknowledgement as provisional exchange state.
- Never reuse an idempotency key, including one left in `reserved`.
- Use cancel or close only when it clearly reduces the verified exposure.
- Preserve the database, logs, exact unresolved snapshot, and latch version.
- Clear Emergency Stop only through the scoped stable-flat procedure above.
