# CTCC-V2 External Benchmark Pack v2.1

## Decision

Gate v2.1 adds the first provider-specific, operator-confirmed public-data
reference flow. It is fixed to Binance USD-M `BTCUSDT`, one-minute klines, and
the completed UTC day 2024-01-01. The artifact and all generated evidence stay
outside Git. No application runtime, strategy, risk, leverage, Paper, Demo,
Live, database, or exchange-write path consumes this flow.

The result remains:

```text
reference_only=true
promotion_eligible=false
execution_authority=false
```

Passing this gate means that one provider archive has a consistent identity
and conforms to the reviewed schema. It is not evidence of alpha,
profitability, execution realism, or production eligibility.

## Dataset and grain

| Field | Frozen value |
|---|---|
| Provider | Binance Public Data |
| Market | USD-M futures |
| Symbol | `BTCUSDT` |
| Interval | `1m` |
| UTC day | `2024-01-01` |
| Expected grain | one row per UTC minute |
| Expected rows | 1,440 |
| Timestamp unit | Unix milliseconds |
| Provider endpoint family | `/fapi/v1/klines` |
| Revision policy | `provider_correctable` |
| Permitted use | data-quality and research calculation reference |

The official Binance public-data documentation states that daily files become
available the next day, USD-M kline archives follow the `/fapi/v1/klines`
schema, every ZIP has a sibling `.CHECKSUM`, and archived files may later be
corrected. The repository declares an MIT license, but CTCC still records
`license_status=review_required`; the source note is an engineering review,
not legal advice or a redistribution grant.

## Two-stage trust chain

The ZIP is never downloaded on first sight:

```text
fixed coordinates and reviewed terms-note digest
→ GET the bounded sibling .CHECKSUM
→ HEAD the exact ZIP
→ freeze checksum payload hash, ZIP SHA-256, byte size, media type,
  Last-Modified, observation time, URLs, and relative path
→ print the complete acquisition request
→ require exact operator phrase ACQUIRE_REFERENCE_ONLY
→ GET the pre-hashed ZIP through the Gate v2 bounded transport
→ inspect ZIP metadata without extraction
→ atomically place the artifact without overwrite
→ parse the one expected CSV member in memory
→ emit immutable manifest, generic quality, provider quality, and final evidence
```

Both metadata requests use credential-free HTTPS on the exact
`data.binance.vision` host. Redirects are never implicit; even a same-host path
change is rejected because it disagrees with the frozen coordinates. The
artifact acquisition sets its redirect allowance to zero. The metadata client
ignores ambient proxy variables and sends no API key, cookie, authorization
header, or exchange credential. The first stage performs a GET only for the
maximum-512-byte checksum sidecar and a HEAD for the ZIP; the artifact GET
exists only after the exact interactive phrase.

The exact ZIP media-type allowlist is `application/zip`,
`application/octet-stream`, and the provider-observed
`binary/octet-stream`. This is not a wildcard: `text/html`, missing types, and
all other values remain fail-closed. SHA-256, byte-size, URL, redirect,
Last-Modified, archive-member, and decompression limits still apply.

## Cross-contract identity

The profiler rejects the evidence set unless all of these agree:

- coordinates hash, exact artifact URL, exact checksum URL, request ID,
  provider source ID, and dataset-relative path;
- terms-review SHA-256 in the identity and acquisition request;
- provider checksum, request SHA-256, receipt SHA-256, and local file SHA-256;
- HEAD byte size, request byte size, receipt byte size, and local file size;
- acquisition request hash stored in the receipt;
- receipt final URL and media type against the frozen request;
- provider Last-Modified no later than the observation/retrieval times.

Evidence JSON uses root-bound POSIX relative paths, rejects symlinks and path
traversal, is limited to 2 MiB per document, and is written with no-clobber
link semantics. Re-running with identical content is idempotent; changed
content fails instead of replacing evidence.

## Data-quality checks

All defects are acceptance blockers. The profiler reports them and never
repairs, deduplicates, interpolates, reorders, or fills records.

| Check | Acceptance rule |
|---|---|
| ZIP member | exactly `BTCUSDT-1m-2024-01-01.csv` |
| Schema | exactly 12 reviewed kline columns |
| Row count | exactly 1,440 |
| Open times | exact minute sequence from `00:00` through `23:59` UTC |
| Keys | no duplicate open timestamp |
| Close time | `open_time + 59,999 ms` |
| Numeric parse | finite exact decimal/integer values |
| OHLC geometry | positive prices, `high >= open/close`, `low <= open/close`, `high >= low` |
| Volume | non-negative base/quote values; generic positive-volume policy also applies |
| Trades | non-negative integer count |
| Taker volumes | non-negative and no greater than corresponding total volume |
| Time window | no invalid, reversed, or out-of-window timestamp |
| Point-in-time | provider availability no later than retrieval |

`failure_codes` are preserved in both the generic and Binance-specific quality
reports. Final `passed=true` is possible only when the failure list is empty.

## Evidence outputs

The default root is `C:\Users\<user>\CTCC-V2-benchmark-data`, outside the
repository. The fixed probe writes:

```text
evidence/btcusdt-1m-2024-01-01-identity.json
evidence/btcusdt-1m-2024-01-01-request.json
evidence/btcusdt-1m-2024-01-01-receipt.json
evidence/btcusdt-1m-2024-01-01-manifest.json
evidence/btcusdt-1m-2024-01-01-generic-quality.json
evidence/btcusdt-1m-2024-01-01-binance-quality.json
evidence/btcusdt-1m-2024-01-01-evidence.json
binance/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01-01.zip
```

If Binance later changes the checksum, size, or metadata, do not delete or
overwrite the earlier evidence. Treat it as a new provider revision and use a
new empty dataset root for a new review.

## Verification and operator probe

First verify the source boundary with every write switch disabled:

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_external_benchmark_pack.ps1
```

Acceptance includes:

```text
EXTERNAL_BENCHMARK_PACK_V2_1_VERIFIED=1
EXTERNAL_BENCHMARK_RUNTIME_CONSUMERS=0
EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0014
API_HEALTH=healthy
```

Then run the real public reference probe:

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run_binance_btcusdt_reference_probe.ps1
```

Review the printed URL, checksum, byte size, Last-Modified, and authority
fields. Type `ACQUIRE_REFERENCE_ONLY` only if they are acceptable. Successful
acceptance ends with:

```text
BINANCE_BTCUSDT_REFERENCE_PROBE_VERIFIED=1
BINANCE_KLINE_ROWS=1440
REFERENCE_ONLY=1
PROMOTION_ELIGIBLE=0
EXECUTION_AUTHORITY=0
REAL_ORDER_TESTED=0
```

Source tests use `httpx.MockTransport`; they do not contact Binance and cannot
claim that the current provider artifact passed. Only the operator probe can
produce that point-in-time evidence.

## Assumptions and unresolved work

- This gate assumes the exact official host and provider checksum are the
  appropriate integrity anchors for this public reference.
- It validates one 2024 millisecond archive only; it does not generalize to
  Binance Spot timestamps from 2025 onward, other intervals, monthly files,
  trades, funding, or order books.
- Header presence is recorded; both the exact reviewed header and the legacy
  headerless provider layout are parsed, but no alternate schema is inferred.
- A clean archive can still contain exchange anomalies not detectable from
  OHLCV invariants alone.
- No CTCC model may consume this data until later gates add frozen train/test
  partitions, leakage checks, cost assumptions, walk-forward OOS evidence,
  uncertainty, and an independent promotion review.

Gate v3 adds frozen multi-window batch evidence while keeping provider data
isolated. Deterministic strategy replay, costs, leakage controls, and genuine
out-of-sample promotion review remain later gates.
