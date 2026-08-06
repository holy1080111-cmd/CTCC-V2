# OKX Demo Broker v1.1

v1.1 introduces a manual-only adapter for OKX Demo Trading. It is deliberately
separate from the automatic Paper orchestrator.

## Safety boundary

- `LIVE_TRADING` and `AUTO_TRADE` are rejected when true.
- Demo order writes are disabled by default.
- Private CTCC Demo routes require `X-CTCC-Token`.
- Only configured SWAP instruments are allowed.
- Order size, open-position count, and leverage are capped locally.
- Protected orders are required by default.
- Every exchange request includes `x-simulated-trading: 1`.
- Read operations may retry with a fresh timestamp/signature.
- Write operations are never automatically retried after transport ambiguity.
- A write acknowledgement is followed by order-detail polling; it is not
  treated as proof of a final fill.

## Exchange-authoritative reconciliation

`POST /api/okx-demo/reconcile` reads account configuration, balance, positions,
pending orders, recent terminal orders, and pending conditional algo orders.
The result is mirrored to PostgreSQL. OKX Demo remains the source of truth.

## Manual endpoints

```text
GET  /api/okx-demo/status
POST /api/okx-demo/connectivity-check
GET  /api/okx-demo/account-config
GET  /api/okx-demo/balance
GET  /api/okx-demo/positions
GET  /api/okx-demo/orders/pending
GET  /api/okx-demo/algo-orders/pending
GET  /api/okx-demo/order-detail
POST /api/okx-demo/reconcile
POST /api/okx-demo/orders
POST /api/okx-demo/orders/cancel
POST /api/okx-demo/positions/close
POST /api/okx-demo/leverage
```

All endpoints except `/status` require the local CTCC API token. Every write
request also requires the exact body confirmation `OKX_DEMO_ONLY`.

## Not included

- automatic strategy-to-OKX Demo execution
- OKX private WebSocket order/fill stream
- real-money execution
