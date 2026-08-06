# CTCC V2 v1.2 — Safe Demo Automation

v1.2 connects deterministic strategy evaluation and the Risk Engine to the
already-verified OKX Demo broker. It remains Demo-only and adds a second,
runtime arming gate on top of the environment capability switch.

## Safety model

1. `OKX_DEMO_AUTO_EXECUTION=false` by default.
2. Enabling the capability does not arm or start automation.
3. Arming requires an authenticated API call and zero exchange exposure.
4. The armed state is never restored after an API restart.
5. Every order is market, allow-listed, size-limited, 1x by default, and has
   both stop loss and take profit.
6. At most one order is submitted per scan.
7. Daily loss, daily trade count, consecutive loss, cooldown, duplicate
   candidate, stale price, and existing exposure locks are enforced.
8. Emergency stop disarms and stops scheduling; it does not silently close a
   position. Existing Demo protection remains on OKX.
9. `AUTO_TRADE`, `LIVE_TRADING`, and real mode remain prohibited.

## New tables

- `demo_automation_state`
- `demo_automation_runs`
- `demo_automation_fingerprints`

## New API

- `GET /api/demo-automation/status`
- `GET /api/demo-automation/history`
- `POST /api/demo-automation/arm`
- `POST /api/demo-automation/disarm`
- `POST /api/demo-automation/start`
- `POST /api/demo-automation/stop`
- `POST /api/demo-automation/run-once`
- `POST /api/demo-automation/emergency-stop`
- `POST /api/demo-automation/clear-emergency-stop`

All endpoints require `X-CTCC-Token`.
