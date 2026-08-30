# Reviewed OKX Demo multi-symbol universe

CTCC v1.6.8 uses a reviewed eight-instrument universe for public market data,
Paper candidate scanning, and OKX Demo candidate scanning:

| Canonical symbol | OKX instrument |
|---|---|
| `BTC/USDT:USDT` | `BTC-USDT-SWAP` |
| `ETH/USDT:USDT` | `ETH-USDT-SWAP` |
| `SOL/USDT:USDT` | `SOL-USDT-SWAP` |
| `XRP/USDT:USDT` | `XRP-USDT-SWAP` |
| `DOGE/USDT:USDT` | `DOGE-USDT-SWAP` |
| `ADA/USDT:USDT` | `ADA-USDT-SWAP` |
| `LINK/USDT:USDT` | `LINK-USDT-SWAP` |
| `LTC/USDT:USDT` | `LTC-USDT-SWAP` |

This is a candidate universe, not an instruction to hold eight positions. It
does not change Live authority, portfolio limits, capital buckets, or the
number of submissions permitted by an execute-soak run.

## Boundary separation

The same immutable mapping is used by public-data normalization and the Demo
settings validator. Demo, Paper, and public WebSocket lists may contain only
the reviewed eight instruments. Demo scan symbols must remain a subset of the
Demo allowlist. When Demo or Paper automation is enabled, every scan symbol
must also have a configured public WebSocket subscription.

The OKX Live production boundary is deliberately separate and remains limited
to:

```text
BTC-USDT-SWAP
ETH-USDT-SWAP
```

Settings reject a Live allowlist or scan list containing any other instrument.
Adding a Demo symbol therefore cannot silently promote it into production.

## Read-only qualification policy

Before accepting the universe, run the public-only verifier with every Paper,
Demo, and Live execution-authority switch disabled. Each instrument must pass:

- exactly one instrument metadata row;
- state `live`, type `SWAP`, and settlement currency `USDT`;
- valid positive best bid and ask plus a non-empty order book;
- spread no greater than 8 basis points;
- estimated 24-hour USDT notional of at least 10 million USDT;
- minimum contract order notional no greater than 25 USDT;
- contract value denominated in the instrument's base currency;
- at least 200 confirmed 4H candles;
- latest confirmed 4H candle no older than 8 hours.

For OKX derivatives, `volCcy24h` is measured in base-currency units. The
qualification screen estimates USDT notional as `volCcy24h * last`. This is a
liquidity screen, not a promise of executable depth or future slippage.

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_demo_multi_symbol_universe.ps1 `
  -RepoPath C:\CTCC-V2
```

The verifier refuses to run when any execution-authority flag is true and
does not call any private or order endpoint. Passing output ends with:

```text
DEMO_UNIVERSE_READONLY_VERIFIED=1
NO_ORDER_ENDPOINT_CALLED=1
ALL_WRITE_AUTHORITY_DISABLED=1
```

Because exchange state and liquidity change, this qualification is renewable
evidence, not a permanent fact. Re-run it after configuration changes and
before any separately authorized Demo soak.

## Runtime selection and fail-closed behavior

Every scan evaluates the requested instruments independently. A public-data or
strategy failure for one instrument is recorded against that instrument and
does not crash the remaining scan. Eligible candidates are then ordered by:

1. downward-adjusted effective mathematical score;
2. raw strategy score;
3. validated mathematical confidence;
4. bounded auxiliary bonus as a true-tie-only discriminator;
5. configured universe order as the final deterministic tie-break.

Before Demo sizing, CTCC fetches fresh instrument metadata again and blocks on
non-unique metadata, non-SWAP type, non-live state, or non-USDT settlement.
The existing spread, market-data quality, structural protection, cost-adjusted
net reward/risk, capital, portfolio, leverage, reconciliation, and exchange
protection gates remain mandatory.

Expanding candidate breadth does not establish profitability. It only creates
more independent opportunities to reach the same validated decision gates.
Walk-forward, out-of-sample, cost-adjusted Demo, and execution-soak evidence
remain required before any claim about strategy edge.
