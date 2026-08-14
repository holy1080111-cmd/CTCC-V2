# OKX Live v1.6.8 operator runbook

This runbook is for real-money OKX SWAP execution. Complete each stage in order.
CTCC cannot guarantee profitability, and passing technical gates is not a
trading recommendation.

## 1. API key and host preparation

Create a dedicated OKX API key for CTCC:

- permissions: Read and Trade;
- Withdraw: disabled;
- IP restriction: bound to the host running CTCC;
- environment: the production OKX account that the operator intends to pin;
- credentials: stored only in local `.env`, never committed or pasted into
  chat, logs, screenshots, or command history.

The supported production origins are `https://openapi.okx.com` and
`https://eea.okx.com`. Demo credentials must not be copied into Live variables.
Endpoint names and payload fields follow the official
[OKX API documentation](https://tr.okx.com/docs-v5/en/).

## 2. Read-only activation

Start with writes and automation disabled:

```env
APP_VERSION=1.6.8
ENVIRONMENT=production
WEB_CONCURRENCY=1
TRADING_MODE=live
AUTO_TRADE=false
LIVE_TRADING=false

OKX_LIVE_ENABLED=true
OKX_LIVE_ALLOW_ORDER_WRITES=false
OKX_LIVE_AUTO_EXECUTION=false
OKX_LIVE_API_KEY=<local secret>
OKX_LIVE_API_SECRET=<local secret>
OKX_LIVE_API_PASSPHRASE=<local secret>

PAPER_AUTO_EXECUTION=false
OKX_DEMO_ALLOW_ORDER_WRITES=false
OKX_DEMO_AUTO_EXECUTION=false
```

Build and migrate, then run:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\verify_v168_live_boundary.ps1

powershell -ExecutionPolicy Bypass `
  -File .\scripts\verify_okx_live_readonly.ps1
```

The full boundary verification refuses to run unless every write/automation
switch is disabled. The read-only verification must show an IP-bound key,
Read + Trade permissions, no Withdraw
permission, a successful atomic mirror, and unchanged exposure counts.

## 3. Manual micro-order gate

After read-only evidence is reviewed, enable the write capability but keep
automation off:

```env
LIVE_TRADING=true
OKX_LIVE_ALLOW_ORDER_WRITES=true
OKX_LIVE_AUTO_EXECUTION=false

OKX_LIVE_ALLOWED_SYMBOLS=BTC-USDT-SWAP
OKX_LIVE_MAX_ORDER_SIZE_CONTRACTS=1
OKX_LIVE_MAX_NOTIONAL_USDT=1000
OKX_LIVE_MAX_OPEN_POSITIONS=1
OKX_LIVE_MAX_LEVERAGE=1
OKX_LIVE_REQUIRE_PROTECTION=true
OKX_LIVE_REQUIRE_IP_BOUND_KEY=true
OKX_LIVE_FORBID_WITHDRAW_PERMISSION=true
OKX_LIVE_REQUIRE_FLAT_START=true
OKX_LIVE_MAX_SUBMISSIONS_PER_ARM=1
OKX_LIVE_AUTO_DISARM=true
OKX_LIVE_ARM_TTL_SECONDS=300
OKX_LIVE_SESSION_LOSS_LIMIT_PCT=0.0025
OKX_LIVE_CANCEL_ALL_AFTER_SECONDS=30
OKX_LIVE_ORDER_EXPIRY_MILLISECONDS=5000
```

Rebuild so Settings revalidates the complete configuration. Choose a contract
quantity that is valid for the instrument and is genuinely a micro position for
the account. Protection prices must align to the current instrument tick and
must surround the current mark price.

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\execute_okx_live_micro_order.ps1 `
  -InstrumentId BTC-USDT-SWAP `
  -Direction long `
  -Size 0.1 `
  -StopLoss 99000 `
  -TakeProfit 102000
```

The production order request expires after five seconds by default so a delayed
request cannot become a stale fill. The example prices are placeholders, not
current market values. The script asks
for two exact interactive phrases before it can Arm and submit.

Immediately verify in the OKX UI:

- the correct account and instrument;
- side, number of contracts, and leverage;
- order state and fill size;
- attached stop-loss and take-profit;
- no unexpected pending order or Algo order;
- CTCC reconciliation matches the exchange.

Before the order call, CTCC requires the set-leverage response to echo the
requested instrument, margin mode, position side, and leverage. Any mismatch
disarms and stops before posting the order.

If CTCC reports an ambiguous order or missing protection, do not rerun with the
same or a new key until the OKX UI and read endpoints have been reconciled. CTCC
does not silently close the position.

Run only one authoritative CTCC deployment for an OKX account and database. A
database-wide advisory lock protects against duplicate API instances sharing
that database, but separate databases cannot coordinate their write authority.

## 4. Exposure-reducing actions

Cancel and close endpoints do not require an active Arm because they reduce
exposure, but they still require all production write configuration, the API
token, an exact action-specific confirmation phrase, and a new durable
idempotency key. A close acknowledgement is polled and reconciled until the
position disappears or the bounded confirmation window ends.

## 5. One-shot automation

Only after the manual micro-order gate has been reviewed:

```env
OKX_WS_ENABLED=true
OKX_LIVE_AUTO_EXECUTION=true
OKX_LIVE_SCAN_SYMBOLS=BTC-USDT-SWAP
OKX_LIVE_AUTOMATION_LEVERAGE=1
```

First run a non-executing strategy scan through the API with `execute=false`.
Then use the one-shot script, which requires both the Live Arm phrase and the
separate automation execution phrase:

```powershell
powershell -ExecutionPolicy Bypass `
  -File .\scripts\run_okx_live_automation_once.ps1
```

Automation requires a connected, fresh public WebSocket snapshot. It stops
after one protected submission and the Live service consumes the Arm. Restarting
the API never restores the Arm or restarts the Live scheduler.

For a market order, automated risk sizing uses the executable side of the
quote: ask for long and bid for short. Last, quote, and mark freshness are
tracked independently. Mark-trigger TP/SL is allowed only when the fresh mark
remains inside the protective bracket and its basis to the executable quote is
within the configured drift limit. Stop/target prices are aligned to exchange
ticks before reward/risk and quantity are recomputed.

The structural 3/5/8/10/20x profile is intentionally not a Live feature in
v1.6.8. Live automation remains the separately reviewed cross-margin 1–3x ATR
boundary until a future gate supplies out-of-sample, Demo-soak, liquidation,
and exchange confirmation evidence. Do not copy Demo 20x settings into Live.

## Incident rules

- REST acknowledgement is not final exchange state.
- Do not retry an idempotency key, including a key left in `reserved` after a
  crash.
- A transport error during order submission is ambiguous and engages Emergency
  Stop.
- Missing protection for observed exposure engages Emergency Stop; it does not
  imply the position is flat.
- Verify OKX directly before clearing Emergency Stop.
- Clearing Emergency Stop requires a fresh, flat reconciliation.
- Never delete PostgreSQL volumes to bypass an identity or intent conflict.
