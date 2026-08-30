from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from pathlib import Path
import zipfile

import httpx
import pytest

from app.research.external_benchmarks import (
    AcquisitionLimits,
    AcquisitionStatus,
    ArchiveInspectionError,
    ArchiveKind,
    ExternalArtifactAcquisitionError,
    ExternalArtifactAcquisitionRequest,
    acquire_external_artifact,
)
from tests.unit.research.helpers import (
    RETRIEVED,
    MockAsyncByteStream,
    acquisition_request,
)


@pytest.mark.asyncio
async def test_acquisition_pins_hash_size_media_type_and_reviewed_redirect(
    tmp_path: Path,
) -> None:
    payload = b"1,100,2\n2,101,1\n"
    request = acquisition_request(payload)
    calls: list[str] = []

    def handler(http_request: httpx.Request) -> httpx.Response:
        calls.append(str(http_request.url))
        assert http_request.method == "GET"
        assert "authorization" not in http_request.headers
        assert http_request.headers["accept-encoding"] == "identity"
        if len(calls) == 1:
            return httpx.Response(
                302,
                headers={
                    "location": (
                        "https://cdn.data.binance.vision/test/trades.csv"
                    )
                },
            )
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload, chunk_size=3),
            headers={"content-type": "text/csv; charset=utf-8"},
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

    assert receipt.status == AcquisitionStatus.DOWNLOADED
    assert receipt.redirect_count == 1
    assert receipt.sha256 == request.expected_sha256
    assert receipt.request_sha256 == request.canonical_sha256()
    assert receipt.execution_authority is False
    assert (tmp_path / "raw" / "trades.csv").read_bytes() == payload
    assert list((tmp_path / "raw").glob("*.partial")) == []


@pytest.mark.asyncio
async def test_acquisition_is_idempotent_only_for_identical_existing_artifact(
    tmp_path: Path,
) -> None:
    payload = b"already-reviewed"
    request = acquisition_request(payload)
    destination = tmp_path / "raw" / "trades.csv"
    destination.parent.mkdir()
    destination.write_bytes(payload)
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        receipt = await acquire_external_artifact(
            request,
            tmp_path,
            client=client,
            clock=lambda: RETRIEVED,
        )
    assert receipt.status == AcquisitionStatus.ALREADY_PRESENT
    assert calls == 0

    destination.write_bytes(b"different-content")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="size disagrees|hash disagrees",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert destination.read_bytes() == b"different-content"
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({"content-type": "text/html"}, "media type disagrees"),
        (
            {
                "content-type": "text/csv",
                "content-encoding": "gzip",
            },
            "encoded artifact responses",
        ),
        (
            {
                "content-type": "text/csv",
                "content-length": "999",
            },
            "Content-Length disagrees",
        ),
    ],
)
async def test_acquisition_rejects_response_identity_disagreement(
    tmp_path: Path,
    headers: dict[str, str],
    error: str,
) -> None:
    payload = b"reviewed"
    request = acquisition_request(payload)

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload),
            headers=headers,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ExternalArtifactAcquisitionError, match=error):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert not (tmp_path / "raw" / "trades.csv").exists()
    assert list(tmp_path.rglob("*.partial")) == []


@pytest.mark.asyncio
async def test_acquisition_enforces_streamed_size_and_redirect_limits(
    tmp_path: Path,
) -> None:
    request = acquisition_request(b"expected")

    def oversized_handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(b"expected-extra", chunk_size=2),
            headers={"content-type": "text/csv"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(oversized_handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="exceeded its expected byte size",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert list(tmp_path.rglob("*.partial")) == []

    def redirect_handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={
                "location": "https://data.binance.vision/second.csv"
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(redirect_handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="redirect limit exceeded",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                limits=AcquisitionLimits(max_redirects=0),
                clock=lambda: RETRIEVED,
            )


@pytest.mark.asyncio
async def test_acquisition_rejects_unsafe_zip_before_atomic_placement(
    tmp_path: Path,
) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.csv", "unsafe")
    payload = buffer.getvalue()
    request = acquisition_request(
        payload,
        relative_path="raw/trades.zip",
        archive_kind=ArchiveKind.ZIP,
        media_types=("application/zip",),
    )

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(payload),
            headers={"content-type": "application/zip"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ArchiveInspectionError):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert not (tmp_path / "raw" / "trades.zip").exists()
    assert list(tmp_path.rglob("*.partial")) == []


@pytest.mark.asyncio
async def test_acquisition_rejects_unreviewed_redirect_and_leaves_no_file(
    tmp_path: Path,
) -> None:
    payload = b"public-data"
    request = acquisition_request(payload)

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://example.com/stolen.csv"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="outside reviewed provider",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert not (tmp_path / "raw" / "trades.csv").exists()


@pytest.mark.asyncio
async def test_acquisition_rejects_hash_mismatch_and_cleans_partial_file(
    tmp_path: Path,
) -> None:
    request = acquisition_request(b"expected")

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=MockAsyncByteStream(b"tampered", chunk_size=2),
            headers={"content-type": "text/csv"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="SHA-256 disagrees",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert not (tmp_path / "raw" / "trades.csv").exists()
    assert list(tmp_path.rglob("*.partial")) == []


@pytest.mark.asyncio
async def test_acquisition_fails_before_network_for_limit_or_future_review(
    tmp_path: Path,
) -> None:
    payload = b"x" * 4097
    request = acquisition_request(payload)
    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="expected artifact size exceeds",
        ):
            await acquire_external_artifact(
                request,
                tmp_path,
                client=client,
                limits=AcquisitionLimits(max_bytes=4096),
                clock=lambda: RETRIEVED,
            )

        future_review = ExternalArtifactAcquisitionRequest.model_validate(
            {
                **request.model_dump(),
                "terms_reviewed_at": RETRIEVED + timedelta(seconds=1),
            }
        )
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="before the recorded terms review",
        ):
            await acquire_external_artifact(
                future_review,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )

        unreviewed_source = ExternalArtifactAcquisitionRequest.model_validate(
            {
                **request.model_dump(),
                "download_url": "https://example.com/public.csv",
            }
        )
        with pytest.raises(
            ExternalArtifactAcquisitionError,
            match="outside the reviewed catalog",
        ):
            await acquire_external_artifact(
                unreviewed_source,
                tmp_path,
                client=client,
                clock=lambda: RETRIEVED,
            )
    assert calls == 0
