from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from app.research.external_benchmarks import (
    ArchiveKind,
    DatasetArtifact,
    acquire_external_artifact,
    profile_dataset_records,
    verify_dataset_artifacts,
)
from tests.unit.research.helpers import (
    END,
    RETRIEVED,
    START,
    MockAsyncByteStream,
    acquisition_request,
    trade_manifest,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reviewed_download_flows_to_immutable_quality_checked_reference(
    tmp_path: Path,
) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(
        buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "BTCUSDT-trades-2026-01-01.csv",
            "1,1767225600000,100,1\n"
            "2,1767225601000,101,1\n"
            "3,1767225602000,102,1\n",
        )
    payload = buffer.getvalue()
    request = acquisition_request(
        payload,
        relative_path="raw/trades.zip",
        archive_kind=ArchiveKind.ZIP,
        media_types=("application/zip", "application/octet-stream"),
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload, chunk_size=11),
            headers={"content-type": "application/zip"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        receipt = await acquire_external_artifact(
            request,
            tmp_path,
            client=client,
            clock=lambda: RETRIEVED,
        )

    base_manifest = trade_manifest(payload=payload)
    manifest = base_manifest.model_copy(
        update={
            "artifacts": (
                DatasetArtifact(
                    relative_path="raw/trades.zip",
                    sha256=request.expected_sha256,
                    byte_size=len(payload),
                    row_count=3,
                    media_type="application/zip",
                ),
            )
        }
    )
    artifact_receipts = verify_dataset_artifacts(manifest, tmp_path)
    records = (
        {
            "trade_id": "1",
            "timestamp": int(START.timestamp() * 1000),
            "price": "100",
            "quantity": "1",
        },
        {
            "trade_id": "2",
            "timestamp": int(
                (START + timedelta(seconds=1)).timestamp() * 1000
            ),
            "price": "101",
            "quantity": "1",
        },
        {
            "trade_id": "3",
            "timestamp": int(END.timestamp() * 1000),
            "price": "102",
            "quantity": "1",
        },
    )
    quality = profile_dataset_records(
        manifest,
        records,
        generated_at=RETRIEVED,
    )

    assert receipt.archive_report_sha256 is not None
    assert receipt.execution_authority is False
    assert artifact_receipts[0].verified is True
    assert quality.passed is True
    assert quality.promotion_eligible is False
