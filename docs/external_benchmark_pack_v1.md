# CTCC-V2 External Benchmark Pack v1

## Purpose

This package freezes public market data and published benchmark results as
calculation references. It does not import another party's profitability claim
into CTCC, and it cannot promote a model, size a position, select leverage, or
reach Paper, Demo, Live, exchange, strategy, or risk runtime code.

The package closes the first part of the institutional evidence gap identified
in the 2026-08-16 core audit: every external reference must have an explicit
identity, point-in-time boundary, quality report, formula definition, and
reproducibility grade before CTCC may compare calculations against it.

## Gate v1 boundary

```text
operator-reviewed public source
→ manually staged immutable file
→ SHA-256 and byte-size verification
→ strict manifest and point-in-time checks
→ missing / duplicate / ordering / range quality profile
→ deterministic reference-only metric calculation
→ replayable benchmark run record
```

Gate v1 deliberately performs no network request. Source-specific downloaders
remain a later gate because redirects, provider terms, archive formats, checksum
availability, corrections, timestamp-unit changes, and large-file limits differ
by provider. A file must first be obtained from the official source, staged
outside the application runtime, and reviewed under its current terms.

## Reviewed public source catalog

| Source | Public material | Permitted reference use |
|---|---|---|
| [OKX historical data](https://www.okx.com/historical-data) | trades, candles, funding, L2 order book | data quality, execution calibration, replay, research baseline |
| [Binance public data](https://github.com/binance/binance-public-data) | spot/futures trades and candles | cross-venue data comparison and research baseline |
| [Nasdaq TotalView-ITCH sample](https://www.nasdaqtrader.com/Trader.aspx?id=ITCH) | order lifecycle messages | order-book reconstruction and event sequencing |
| [LOBSTER](https://lobsterdata.com/) | reconstructed L2/L3 samples | queue and fill-model calibration |
| [QuantConnect LEAN](https://github.com/QuantConnect/Lean) | regression algorithms and expected statistics | formula and engine regression parity |
| [NautilusTrader](https://nautilustrader.io/docs/latest/concepts/backtesting/) | deterministic backtest and matching concepts | replay, fill, book and metric parity |
| [Microsoft Qlib benchmarks](https://github.com/microsoft/qlib/tree/main/examples/benchmarks) | model configs and repeated-seed results | experiment and metric-format reference |
| [CFTC COT](https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm) | historical positioning reports | point-in-time research baseline |
| [FRED API](https://fred.stlouisfed.org/docs/api/fred/) | macro observations and real-time periods | macro vintage and revision-aware research |

Catalog inclusion is not a license grant. Every source descriptor fixes
`terms_review_required=true`, and each imported manifest records a separate
terms URL and license-review state.

## Immutable dataset manifest

Every dataset import must declare:

- source, dataset kind, exact official and terms URLs;
- retrieval time, event start/end, and when the final observation was available;
- timestamp encoding and UTC-only semantics;
- complete, required, key, and positive-numeric fields;
- instruments and explicitly reviewed intended uses;
- provider correction/revision policy and point-in-time safety;
- row count, relative artifact paths, byte sizes, media types, and SHA-256;
- `reference_only=true`, `promotion_eligible=false`, and
  `execution_authority=false`.

Artifacts cannot use absolute paths, `..`, Windows separators, symlinks, or
paths outside the dataset root. Verification reads but never repairs or mutates
the file.

## Data-quality profile

The profiler reports rates rather than silently cleaning data:

- observed versus declared row count;
- rows missing any required field;
- duplicate composite-key rows;
- invalid timestamps;
- adjacent timestamp reversals;
- records outside the frozen event window;
- rows with missing, invalid, zero, or negative positive-numeric fields.

Default limits are zero except for a two-row minimum. A non-zero tolerance must
be explicit in a frozen `DatasetQualityPolicy`; the report still preserves the
observed defect counts and rates. No repair, deduplication, interpolation, or
reordering occurs inside this gate.

## Public benchmark result boundary

A `PublishedBenchmarkRecord` stores public metrics, version, dataset identity,
seed count, retrieval time, source artifact hash, and one of four
reproducibility levels:

1. `reported_only`;
2. `code_available`;
3. `data_and_code_available`;
4. `independently_reproduced`.

All four levels remain `calculation_reference_only`. Even an independently
reproduced result is not a CTCC predictive-validation artifact because the
instrument universe, labels, costs, trial count, and test period can differ.

## Formula-parity metrics

`calculate_reference_return_metrics` accepts decimal net simple returns only;
binary floats are rejected. It defines and calculates:

- compounded total return;
- arithmetic mean return;
- sample standard deviation;
- annualized volatility;
- conventional zero/explicit-risk-free Sharpe ratio;
- peak-to-trough maximum drawdown magnitude;
- positive-return hit rate;
- gross-gain to gross-loss profit factor.

Inputs must already include fees, funding, and slippage. These metrics do not
calculate DSR, PBO, calibration, confidence intervals, liquidation risk, or
economic OOS eligibility. Those belong to later independently reviewed gates.

## Authority invariants

Automated tests enforce:

- no network client in Gate v1;
- no import from MIE, exchange, Paper, Demo, Live, execution, strategy, or risk;
- no application runtime consumer of `app.research`;
- no order ID, contract, leverage, margin, position-size, or write-authority
  field in any reference result;
- fixed false promotion and execution authority;
- unchanged fail-safe runtime defaults;
- no database migration.

## Operator verification

With Docker Desktop running and every execution-authority switch disabled:

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_external_benchmark_pack.ps1
```

Acceptance requires:

```text
EXTERNAL_BENCHMARK_PACK_V1_VERIFIED=1
EXTERNAL_BENCHMARK_RUNTIME_CONSUMERS=0
EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0013
API_HEALTH=healthy
```

## Later gates

1. Provider-scoped, bounded, redirect-aware acquisition with explicit terms
   review, pinned identity, atomic placement, and ZIP-bomb defenses is
   implemented in [External Benchmark Pack v2](external_benchmark_pack_v2.md).
2. Canonical OKX/Binance trade, candle, funding, and L2 parsers.
3. Deterministic market-event and order-book replay under `app/replay`.
4. Realistic partial-fill, queue, latency, spread, funding, gap, and liquidation
   calibration.
5. Frozen trial registry, purged walk-forward OOS, DSR/PBO, bootstrap intervals,
   calibration, and ablation.
6. Independent promotion review. External public results remain references and
   never self-promote CTCC.
