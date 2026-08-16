from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from urllib.parse import urljoin, urlparse

import httpx

from app.research.external_benchmarks.archive import require_safe_zip_archive
from app.research.external_benchmarks.artifacts import sha256_file
from app.research.external_benchmarks.catalog import validate_acquisition_source
from app.research.external_benchmarks.contracts import (
    AcquisitionLimits,
    AcquisitionStatus,
    ArchiveInspectionPolicy,
    ArchiveKind,
    ExternalArtifactAcquisitionReceipt,
    ExternalArtifactAcquisitionRequest,
    require_utc,
)


Clock = Callable[[], datetime]
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class ExternalArtifactAcquisitionError(RuntimeError):
    pass


def _validate_transport_url(
    url: str,
    *,
    approved_hosts: tuple[str, ...],
) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ExternalArtifactAcquisitionError(
            "artifact URL contains an invalid port"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ExternalArtifactAcquisitionError(
            "artifact URL must be query-free HTTPS on port 443"
        )
    if not any(
        host == approved or host.endswith(f".{approved}")
        for approved in approved_hosts
    ):
        raise ExternalArtifactAcquisitionError(
            "artifact URL host is outside reviewed provider scope"
        )
    return url


def _dataset_root(path: Path) -> Path:
    if path.is_symlink():
        raise ExternalArtifactAcquisitionError(
            "acquisition root cannot be a symlink"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ExternalArtifactAcquisitionError(
            "acquisition root does not exist"
        ) from exc
    if not resolved.is_dir():
        raise ExternalArtifactAcquisitionError(
            "acquisition root must be a real directory"
        )
    return resolved


def _destination(root: Path, relative_path: str) -> Path:
    parts = PurePosixPath(relative_path).parts
    parent = root
    for part in parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ExternalArtifactAcquisitionError(
                "acquisition destination cannot traverse a symlink"
            )
        if parent.exists() and not parent.is_dir():
            raise ExternalArtifactAcquisitionError(
                "acquisition destination parent is not a directory"
            )
        parent.mkdir(exist_ok=True)
    resolved_parent = parent.resolve(strict=True)
    if not resolved_parent.is_relative_to(root):
        raise ExternalArtifactAcquisitionError(
            "acquisition destination escaped its root"
        )
    candidate = resolved_parent / parts[-1]
    if candidate.is_symlink():
        raise ExternalArtifactAcquisitionError(
            "acquisition destination cannot be a symlink"
        )
    return candidate


def _normalized_media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _inspect_archive(
    request: ExternalArtifactAcquisitionRequest,
    path: Path,
    policy: ArchiveInspectionPolicy,
) -> str | None:
    if request.archive_kind == ArchiveKind.NONE:
        return None
    report = require_safe_zip_archive(path, policy=policy)
    return report.canonical_sha256()


def _receipt(
    request: ExternalArtifactAcquisitionRequest,
    *,
    final_url: str,
    media_type: str,
    retrieved_at: datetime,
    redirect_count: int,
    status: AcquisitionStatus,
    archive_report_sha256: str | None,
) -> ExternalArtifactAcquisitionReceipt:
    return ExternalArtifactAcquisitionReceipt(
        request_id=request.request_id,
        request_sha256=request.canonical_sha256(),
        source_id=request.source_id,
        final_url=final_url,
        relative_path=request.relative_path,
        sha256=request.expected_sha256,
        byte_size=request.expected_byte_size,
        media_type=media_type,
        retrieved_at=retrieved_at,
        redirect_count=redirect_count,
        status=status,
        archive_report_sha256=archive_report_sha256,
    )


def _verify_existing(
    request: ExternalArtifactAcquisitionRequest,
    destination: Path,
    *,
    retrieved_at: datetime,
    archive_policy: ArchiveInspectionPolicy,
) -> ExternalArtifactAcquisitionReceipt | None:
    if not destination.exists():
        return None
    if not destination.is_file():
        raise ExternalArtifactAcquisitionError(
            "acquisition destination already exists and is not a file"
        )
    if destination.stat().st_size != request.expected_byte_size:
        raise ExternalArtifactAcquisitionError(
            "existing artifact size disagrees with the acquisition request"
        )
    if sha256_file(destination) != request.expected_sha256:
        raise ExternalArtifactAcquisitionError(
            "existing artifact hash disagrees with the acquisition request"
        )
    archive_sha = _inspect_archive(request, destination, archive_policy)
    return _receipt(
        request,
        final_url=str(request.download_url),
        media_type=request.expected_media_types[0],
        retrieved_at=retrieved_at,
        redirect_count=0,
        status=AcquisitionStatus.ALREADY_PRESENT,
        archive_report_sha256=archive_sha,
    )


async def acquire_external_artifact(
    request: ExternalArtifactAcquisitionRequest,
    dataset_root: Path,
    *,
    client: httpx.AsyncClient | None = None,
    limits: AcquisitionLimits | None = None,
    archive_policy: ArchiveInspectionPolicy | None = None,
    clock: Clock | None = None,
) -> ExternalArtifactAcquisitionReceipt:
    """Acquire one pre-hashed public artifact through a fail-closed boundary."""

    try:
        descriptor = validate_acquisition_source(request)
    except ValueError as exc:
        raise ExternalArtifactAcquisitionError(
            "artifact source is outside the reviewed catalog scope"
        ) from exc
    acquisition_limits = limits or AcquisitionLimits()
    inspection_policy = archive_policy or ArchiveInspectionPolicy()
    if request.expected_byte_size > acquisition_limits.max_bytes:
        raise ExternalArtifactAcquisitionError(
            "expected artifact size exceeds the acquisition limit"
        )
    current_url = _validate_transport_url(
        str(request.download_url),
        approved_hosts=descriptor.approved_hosts,
    )
    root = _dataset_root(dataset_root)
    destination = _destination(root, request.relative_path)
    acquisition_clock = clock or (lambda: datetime.now(timezone.utc))
    started_at = require_utc(
        acquisition_clock(),
        "retrieved_at",
    )
    if started_at < request.terms_reviewed_at:
        raise ExternalArtifactAcquisitionError(
            "artifact cannot be acquired before the recorded terms review"
        )
    existing = _verify_existing(
        request,
        destination,
        retrieved_at=started_at,
        archive_policy=inspection_policy,
    )
    if existing is not None:
        return existing

    own_client = client is None
    transport_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(
            float(acquisition_limits.read_timeout_seconds),
            connect=float(acquisition_limits.connect_timeout_seconds),
        ),
        follow_redirects=False,
        trust_env=False,
    )
    partial_path: Path | None = None
    redirect_count = 0
    media_type = ""
    try:
        while True:
            try:
                async with transport_client.stream(
                    "GET",
                    current_url,
                    headers={
                        "Accept": ", ".join(request.expected_media_types),
                        "Accept-Encoding": "identity",
                        "User-Agent": "CTCC-V2-external-benchmark-acquisition/2",
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code in REDIRECT_STATUS_CODES:
                        if redirect_count >= acquisition_limits.max_redirects:
                            raise ExternalArtifactAcquisitionError(
                                "artifact redirect limit exceeded"
                            )
                        location = response.headers.get("location")
                        if not location:
                            raise ExternalArtifactAcquisitionError(
                                "artifact redirect omitted Location"
                            )
                        current_url = _validate_transport_url(
                            urljoin(current_url, location),
                            approved_hosts=descriptor.approved_hosts,
                        )
                        redirect_count += 1
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ExternalArtifactAcquisitionError(
                            f"artifact download failed with HTTP {response.status_code}"
                        )
                    content_encoding = response.headers.get(
                        "content-encoding",
                        "identity",
                    ).strip().lower()
                    if content_encoding not in {"", "identity"}:
                        raise ExternalArtifactAcquisitionError(
                            "encoded artifact responses are not accepted"
                        )
                    media_type = _normalized_media_type(
                        response.headers.get("content-type")
                    )
                    if media_type not in request.expected_media_types:
                        raise ExternalArtifactAcquisitionError(
                            "artifact media type disagrees with the acquisition request"
                        )
                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_size = int(content_length)
                        except ValueError as exc:
                            raise ExternalArtifactAcquisitionError(
                                "artifact Content-Length is invalid"
                            ) from exc
                        if declared_size != request.expected_byte_size:
                            raise ExternalArtifactAcquisitionError(
                                "artifact Content-Length disagrees with expected size"
                            )

                    digest = hashlib.sha256()
                    downloaded = 0
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=destination.parent,
                        prefix=f".{destination.name}.",
                        suffix=".partial",
                        delete=False,
                    ) as handle:
                        partial_path = Path(handle.name)
                        async for chunk in response.aiter_raw(
                            acquisition_limits.chunk_size
                        ):
                            downloaded += len(chunk)
                            if downloaded > acquisition_limits.max_bytes:
                                raise ExternalArtifactAcquisitionError(
                                    "artifact exceeded the acquisition byte limit"
                                )
                            if downloaded > request.expected_byte_size:
                                raise ExternalArtifactAcquisitionError(
                                    "artifact exceeded its expected byte size"
                                )
                            handle.write(chunk)
                            digest.update(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())

                    if downloaded != request.expected_byte_size:
                        raise ExternalArtifactAcquisitionError(
                            "artifact byte size disagrees with the acquisition request"
                        )
                    if digest.hexdigest() != request.expected_sha256:
                        raise ExternalArtifactAcquisitionError(
                            "artifact SHA-256 disagrees with the acquisition request"
                        )
                    archive_sha = _inspect_archive(
                        request,
                        partial_path,
                        inspection_policy,
                    )
                    retrieved_at = require_utc(
                        acquisition_clock(),
                        "retrieved_at",
                    )
                    if retrieved_at < started_at:
                        raise ExternalArtifactAcquisitionError(
                            "artifact acquisition clock moved backwards"
                        )
                    try:
                        os.link(
                            partial_path,
                            destination,
                            follow_symlinks=False,
                        )
                    except FileExistsError as exc:
                        raise ExternalArtifactAcquisitionError(
                            "artifact destination appeared during acquisition"
                        ) from exc
                    partial_path.unlink()
                    partial_path = None
                    return _receipt(
                        request,
                        final_url=current_url,
                        media_type=media_type,
                        retrieved_at=retrieved_at,
                        redirect_count=redirect_count,
                        status=AcquisitionStatus.DOWNLOADED,
                        archive_report_sha256=archive_sha,
                    )
            except httpx.HTTPError as exc:
                raise ExternalArtifactAcquisitionError(
                    f"artifact transport failed: {type(exc).__name__}"
                ) from exc
    finally:
        if partial_path is not None:
            partial_path.unlink(missing_ok=True)
        if own_client:
            await transport_client.aclose()
