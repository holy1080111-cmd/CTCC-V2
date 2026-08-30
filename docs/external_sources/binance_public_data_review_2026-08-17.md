# Binance public-data review — 2026-08-17

## Review scope

This review covers only one reference artifact:

- provider: Binance Public Data;
- market: USD-M futures;
- dataset: BTCUSDT 1-minute klines;
- UTC day: 2024-01-01;
- official host: `data.binance.vision`;
- use: CTCC data-quality and research calculation reference only.

It does not approve exchange writes, strategy promotion, position sizing,
leverage selection, or redistribution of the downloaded archive.

## Official statements reviewed

The official
[Binance Public Data repository](https://github.com/binance/binance-public-data)
states that:

1. public market data is grouped into daily and monthly files;
2. daily data normally becomes available the following day;
3. USD-M futures kline files originate from `/fapi/v1/klines`;
4. the reviewed kline schema contains open time, OHLC, volume, close time,
   quote volume, trade count, taker-buy volumes, and an ignored field;
5. each ZIP has a sibling `.CHECKSUM` file for SHA-256 verification;
6. archived files can later be corrected when issues are discovered;
7. the repository declares an MIT license.

The official object listing for
[BTCUSDT USD-M 1-minute daily klines](https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2Fklines%2FBTCUSDT%2F1m%2F)
lists the 2024-01-01 ZIP and its sibling checksum with a 2024-01-02 last-modified
time. A read-only operator probe on 2026-08-22 observed the exact ZIP URL return
HTTP 200 without a redirect and the base media type `binary/octet-stream` from
Amazon S3. CTCC accepts that exact provider value alongside `application/zip`
and `application/octet-stream`; HTML and every other media type remain blocked.
Exact SHA-256, byte size, and Last-Modified values are not copied into source
code; the preparation step reads them from the official host and freezes them
before the ZIP artifact is requested.

## CTCC interpretation

- Source authenticity initially relies on credential-free HTTPS to the exact
  reviewed host and the provider-published sibling checksum.
- The checksum, its payload digest, exact byte size, media type, Last-Modified,
  observation time, reviewed coordinates, and this note's SHA-256 are frozen
  into evidence before the artifact GET.
- Because Binance documents later archive corrections, the revision policy is
  `provider_correctable`, not immutable.
- The raw ZIP and all generated evidence must remain outside the Git repository.
- A changed checksum or size is a new provider revision and must use a new empty
  evidence directory; existing evidence is never overwritten.
- Passing structural and data-quality checks proves only that this reference
  file conforms to the reviewed format. It does not prove predictive value or
  authorize any CTCC model or trading action.

## Decision

Approved for a single, operator-confirmed, reference-only acquisition and
quality probe under External Benchmark Gate v2.1. Promotion eligibility and
execution authority remain false.
