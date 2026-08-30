from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx

from app.research.external_benchmarks import (
    ArchiveKind,
    DatasetArtifact,
    DatasetKind,
    DatasetWindow,
    ExternalDatasetManifest,
    ExternalArtifactAcquisitionRequest,
    IntendedUse,
    LicenseStatus,
    RevisionPolicy,
    SourceKind,
    TimestampEncoding,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(seconds=2)
AVAILABLE = END + timedelta(minutes=1)
RETRIEVED = AVAILABLE + timedelta(minutes=1)


class MockAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, payload: bytes, *, chunk_size: int | None = None) -> None:
        self.payload = payload
        self.chunk_size = chunk_size or max(len(payload), 1)

    async def __aiter__(self):
        for offset in range(0, len(self.payload), self.chunk_size):
            yield self.payload[offset : offset + self.chunk_size]


def acquisition_request(
    payload: bytes,
    *,
    relative_path: str = "raw/trades.csv",
    download_url: str = "https://data.binance.vision/test/trades.csv",
    archive_kind: ArchiveKind = ArchiveKind.NONE,
    media_types: tuple[str, ...] = ("text/csv",),
) -> ExternalArtifactAcquisitionRequest:
    return ExternalArtifactAcquisitionRequest(
        request_id="binance.btcusdt.trades.2026-01-01",
        source_id="binance.public_data",
        download_url=download_url,
        terms_url="https://github.com/binance/binance-public-data",
        terms_review_sha256="f" * 64,
        terms_reviewed_at=START,
        relative_path=relative_path,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_byte_size=len(payload),
        expected_media_types=media_types,
        archive_kind=archive_kind,
    )


def trade_manifest(
    *,
    row_count: int = 3,
    payload: bytes = b"test-data",
    timestamp_encoding: TimestampEncoding = TimestampEncoding.UNIX_MILLISECONDS,
) -> ExternalDatasetManifest:
    return ExternalDatasetManifest(
        dataset_id="okx.btc-usdt-swap.trades.2026-01-01",
        source_id="okx.historical_data",
        source_kind=SourceKind.EXCHANGE,
        dataset_kind=DatasetKind.TRADE,
        source_url="https://www.okx.com/historical-data",
        terms_url="https://www.okx.com/help/terms-of-service",
        license_status=LicenseStatus.REVIEW_REQUIRED,
        revision_policy=RevisionPolicy.PROVIDER_CORRECTABLE,
        retrieved_at=RETRIEVED,
        window=DatasetWindow(
            start=START,
            end=END,
            available_at=AVAILABLE,
        ),
        timestamp_encoding=timestamp_encoding,
        timestamp_field="timestamp",
        fields=("trade_id", "timestamp", "price", "quantity"),
        required_fields=("trade_id", "timestamp", "price", "quantity"),
        key_fields=("trade_id",),
        positive_numeric_fields=("price", "quantity"),
        instrument_ids=("BTC-USDT-SWAP",),
        intended_uses=(
            IntendedUse.DATA_QUALITY_REFERENCE,
            IntendedUse.EXECUTION_CALIBRATION,
        ),
        point_in_time_safe=True,
        row_count=row_count,
        artifacts=(
            DatasetArtifact(
                relative_path="raw/trades.csv",
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
                row_count=row_count,
                media_type="text/csv",
            ),
        ),
    )
