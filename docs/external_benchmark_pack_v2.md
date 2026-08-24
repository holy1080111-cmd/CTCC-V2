# CTCC-V2 External Benchmark Pack v2

## Decision

Gate v2 adds an operator-only acquisition boundary for immutable public
reference artifacts. It closes the gap between an official public archive and
the v1 artifact/quality contracts without giving downloaded material a path to
strategy, risk, leverage, order, Demo, or Live runtime code.

```text
official public artifact + operator-reviewed terms + predeclared SHA-256/size
→ HTTPS GET on a reviewed host
→ every redirect revalidated
→ bounded identity-encoded stream
→ SHA-256 / size / media-type match
→ ZIP metadata safety inspection without extraction
→ atomic no-clobber placement
→ immutable acquisition receipt
→ v1 manifest, quality profile, and reference calculations
```

`reference_only=true`, `promotion_eligible=false`, and
`execution_authority=false` remain fixed contract values.

## Why the expected identity comes first

TLS authenticates a connection to a host; it does not prove that an archive is
the same revision the operator reviewed. Gate v2 therefore requires the exact
SHA-256 and byte size before downloading. A successful HTTP response without a
predeclared identity is intentionally rejected.

Binance publishes a sibling `.CHECKSUM` for each public ZIP and documents that
archives can later be corrected. The operator must review and pin the current
checksum and size in the request. OKX publicly lists tick trades, candles,
funding, and L2 history, but Gate v2 accepts an OKX artifact only after its
actual download host and identity are explicitly reviewed; the catalog is not
silently widened to a storage or redirect host.

## Acquisition request

The request is strict JSON. Example only—the checksum and size below are not a
real provider artifact and must be replaced with reviewed values:

```json
{
  "request_id": "binance.btcusdt.klines.1m.2026-07",
  "source_id": "binance.public_data",
  "download_url": "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-07.zip",
  "terms_url": "https://github.com/binance/binance-public-data",
  "terms_review_sha256": "REPLACE_WITH_64_LOWERCASE_HEX",
  "terms_reviewed_at": "2026-08-16T00:00:00Z",
  "relative_path": "binance/btcusdt/BTCUSDT-1m-2026-07.zip",
  "expected_sha256": "REPLACE_WITH_64_LOWERCASE_HEX",
  "expected_byte_size": 123456,
  "expected_media_types": [
    "application/zip",
    "application/octet-stream",
    "binary/octet-stream"
  ],
  "archive_kind": "zip",
  "terms_accepted": true,
  "reference_only": true,
  "promotion_eligible": false,
  "execution_authority": false
}
```

The URL must be HTTPS on port 443 and contain no credentials, query, or
fragment. Its host and every redirect host must remain within the reviewed
provider descriptor. The terms review digest identifies the operator's own
frozen review note; it is not a legal opinion or a provider license grant.

## Transport and filesystem controls

- only `GET` is implemented;
- ambient proxy environment variables are ignored by the internal client;
- `Authorization`, cookies, API keys, and exchange credentials are never sent;
- redirects are manual and bounded, and every hop is revalidated;
- compressed HTTP content encodings are rejected so the received bytes match
  the predeclared hash;
- response media type, optional `Content-Length`, streamed byte count, and
  SHA-256 must all agree;
- the byte limit is enforced before and during streaming;
- dataset root, parent directories, destination, and artifacts cannot traverse
  symlinks or escape the root;
- an existing file is accepted only when its size and hash are identical;
- a new file is linked into place without overwriting an existing path;
- a failed acquisition removes only the partial file created by that attempt.

## ZIP controls

Gate v2 never extracts an archive. It inspects the central directory and
rejects:

- invalid or empty ZIPs;
- excessive member count, total expanded bytes, single-member size, or
  expansion ratio;
- duplicate member names;
- absolute, parent-traversal, Windows-separator, NUL, or drive-like paths;
- encrypted members, symbolic links, and nested archives.

Passing this inspection is not permission to extract. Canonical provider
parsers and controlled extraction remain a later gate.

## Operator command

Keep all execution switches disabled. Store the request outside the repository
and create a dedicated empty dataset directory. Then run one complete block:

```powershell
cd C:\CTCC-V2

$requestPath = "C:\Users\holy1\Downloads\benchmark-request.json"
$datasetRoot = "C:\Users\holy1\CTCC-V2-benchmark-data"
New-Item -ItemType Directory -Path $datasetRoot -Force | Out-Null

powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\acquire_external_benchmark_artifact.ps1 `
  -RequestPath $requestPath `
  -DatasetRoot $datasetRoot
```

Acceptance output:

```text
EXTERNAL_BENCHMARK_ARTIFACT_ACQUIRED=1
EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0
```

The receipt is printed to the console. Preserve it beside the reviewed request
before creating the v1 dataset manifest.

## Verification

```powershell
cd C:\CTCC-V2
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\verify_external_benchmark_pack.ps1
```

Acceptance requires:

```text
EXTERNAL_BENCHMARK_PACK_V2_VERIFIED=1
EXTERNAL_BENCHMARK_RUNTIME_CONSUMERS=0
EXTERNAL_BENCHMARK_EXECUTION_AUTHORITY=0
ALEMBIC_HEAD=0015
API_HEALTH=healthy
```

## Explicit non-goals

Gate v2 does not:

- discover URLs, symbols, dates, checksums, or licenses automatically;
- accept provider data on first-seen hash alone;
- extract or parse archives;
- normalize timestamps or repair provider data;
- compare CTCC returns with a public result;
- perform walk-forward, PBO, DSR, bootstrap, or promotion review;
- write PostgreSQL, expose an API route, or alter execution settings.

The first provider-specific parser and operator flow are implemented in
[External Benchmark Pack v2.1](external_benchmark_pack_v2_1.md). Later gates
must add deterministic time-partitioned replay and may consume only v2 receipts
and v1 manifests that passed every quality check.
