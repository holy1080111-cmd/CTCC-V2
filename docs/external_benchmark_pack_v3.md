# CTCC-V2 External Benchmark Pack v3

## Decision

Gate v3 turns the single-day Binance reference probe into one frozen,
operator-confirmed batch. It accelerates evidence collection without widening
runtime or execution authority.

```text
reference_only=true
promotion_eligible=false
runtime_consumers=0
execution_authority=false
```

Passing this gate proves artifact identity, minute-level structural quality,
partition completeness, and deterministic descriptive calculations. It does
not prove that a CTCC strategy predicts returns, survives costs, or should be
promoted.

## Frozen plan

The plan ID is `binance.btc_eth.1m.calendar_split.v1`.

| Partition | UTC dates | Symbols | Days per symbol | Purpose |
|---|---|---:|---:|---|
| development | 2024-01-01 through 2024-01-30 | BTCUSDT, ETHUSDT | 30 | build and inspect deterministic research transforms |
| validation | 2025-01-01 through 2025-01-30 | BTCUSDT, ETHUSDT | 30 | independently repeat the same fixed calculations |
| retrospective holdout | 2026-07-23 through 2026-08-21 | BTCUSDT, ETHUSDT | 30 | recent-regime reference frozen before strategy replay |

The plan contains 180 daily ZIP artifacts and exactly 259,200 expected
one-minute rows. Windows are chronological, unique, and non-overlapping.
Symbols, dates, interval, market family, host, and path are contract-bound;
there is no operator flag that silently changes the research universe.

The last partition is explicitly
`retrospective_not_prospective`. Calling it a holdout records intended future
use, but does not make it an untouched prospective sample if it has already
been inspected. Any later strategy evaluation must record whether its design
predated access to this evidence and must create a newer prospective window
when independence cannot be demonstrated.

## Two-stage operator boundary

Preparation and acquisition remain separate:

```text
frozen plan + reviewed terms digest
→ bounded checksum GET and exact ZIP HEAD for all 180 coordinates
→ immutable identity/request evidence
→ print plan, windows, byte total, first request, and last request
→ require exact phrase ACQUIRE_BINANCE_BATCH_REFERENCE_ONLY
→ bounded concurrent artifact GETs (maximum four)
→ no-clobber placement and provider quality profile
→ deterministic daily and partition summaries
→ immutable final batch evidence
```

Preparation does not GET a ZIP. Every artifact request has a provider checksum,
exact byte size, exact reviewed URL, approved media type, provider
Last-Modified time, reviewed terms digest, and execution authority fixed to
false. The acquisition layer rechecks URL, media type, byte count, SHA-256,
ZIP metadata, member name, and local path.

Concurrency is bounded at four. A partial run can reuse only artifacts and
evidence whose exact hash-bound content still agrees; changed evidence fails
closed rather than being overwritten. The dataset root must be outside Git.

## Quality and descriptive calculations

Each daily archive passes the existing v2.1 checks: one reviewed CSV member,
exact schema, exactly 1,440 ordered minute records, valid timestamps and OHLC
geometry, finite numbers, non-negative volumes and trades, and valid taker
volume bounds. Gate v3 repeats the critical structural checks before creating
a daily summary.

For each symbol and partition, the batch emits:

- first open, last close, high, low, volume, quote volume, and trade count;
- close-path total return, volatility, drawdown, hit rate, and related frozen
  reference metrics;
- a robust Theil-Sen slope over log daily closes;
- log-price path efficiency between 0 and 1;
- a descriptive `rising`, `flat`, or `falling` label derived only from the
  slope sign.

These are exact calculations over observed history, not forecasts. A positive
slope describes that frozen path and does not establish a probability that the
next candle will rise. Gate v3 therefore fixes:

```text
descriptive_only=true
predictive_validity_claimed=false
strategy_evaluated=false
costs_evaluated=false
```

## Evidence outputs

The operator selects an outside-repository dataset root. Gate v3 writes the
frozen plan and preparation records under:

```text
evidence/binance-reference-batch-v1-plan.json
evidence/binance-reference-batch-v1-preparation.json
```

Each coordinate receives immutable identity, request, receipt, manifest,
generic quality, Binance quality, final evidence, and daily-summary JSON. The
verified ZIP remains under its exact provider-relative path. Final acceptance
is recorded at:

Price fields remain strictly positive. Binance futures may legitimately
publish a complete minute with zero traded volume, so volume is checked as
nonnegative by the provider-specific profile. Negative volume, invalid OHLC,
missing minutes, duplicate timestamps, and interval defects remain hard
failures. This prevents the generic strictly-positive price rule from
rejecting a structurally valid zero-volume candle.

```text
evidence/binance-reference-batch-v1-evidence.json
```

The final contract requires 180 completed unique requests, 259,200 minute
rows, six partition summaries, zero partition overlap, zero runtime consumers,
and no promotion or execution authority.

## Source verification

Run with every Paper, Demo, and Live write switch disabled:

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_external_benchmark_pack.ps1
```

Acceptance includes:

```text
EXTERNAL_BENCHMARK_PACK_V3_VERIFIED=1
EXTERNAL_BENCHMARK_RUNTIME_CONSUMERS=0
EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0013
API_HEALTH=healthy
```

Source tests use synthetic ZIPs and `httpx.MockTransport`. They verify the
complete preparation-to-summary chain without making a public request.

## Real reference-only batch probe

After source verification:

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_binance_reference_batch_probe.ps1
```

Review the printed frozen plan and identities. Type
`ACQUIRE_BINANCE_BATCH_REFERENCE_ONLY` only to authorize the 180 public ZIP
GETs. It does not authorize an exchange order. Successful acceptance ends
with:

```text
BINANCE_REFERENCE_BATCH_PROBE_VERIFIED=1
BINANCE_BATCH_ARTIFACTS=180
BINANCE_BATCH_MINUTE_ROWS=259200
BINANCE_BATCH_PARTITION_SUMMARIES=6
RETROSPECTIVE_HOLDOUT=1
STRATEGY_EVALUATED=0
COSTS_EVALUATED=0
REFERENCE_ONLY=1
PROMOTION_ELIGIBLE=0
RUNTIME_CONSUMERS=0
EXECUTION_AUTHORITY=0
REAL_ORDER_TESTED=0
```

## What must come next

Gate v3 is a data and descriptive-evidence gate. A later gate must add a
canonical replay dataset, declare the strategy and parameter selection before
reading holdout results, model fees/funding/spread/slippage, validate event-time
alignment and leakage, report walk-forward out-of-sample uncertainty, and
compare against simple benchmarks. Only a separately reviewed promotion gate
may make validated evidence available to decision logic; no research gate can
grant exchange-write authority.
