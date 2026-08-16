from __future__ import annotations

import hashlib
from pathlib import Path

from app.research.external_benchmarks.contracts import (
    ArtifactVerification,
    ExternalDatasetManifest,
)
from app.research.external_benchmarks.catalog import validate_manifest_source


class ArtifactVerificationError(ValueError):
    pass


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    if chunk_size < 4096:
        raise ValueError("chunk_size must be at least 4096 bytes")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_artifacts(
    manifest: ExternalDatasetManifest,
    dataset_root: Path,
) -> tuple[ArtifactVerification, ...]:
    """Verify local files without parsing, mutating, or contacting a provider."""

    validate_manifest_source(manifest)
    if dataset_root.is_symlink():
        raise ArtifactVerificationError("dataset root cannot be a symlink")
    try:
        root = dataset_root.resolve(strict=True)
    except OSError as exc:
        raise ArtifactVerificationError("dataset root does not exist") from exc
    if not root.is_dir():
        raise ArtifactVerificationError("dataset root must be a real directory")

    verified: list[ArtifactVerification] = []
    for artifact in manifest.artifacts:
        candidate = root.joinpath(*artifact.relative_path.split("/"))
        if candidate.is_symlink():
            raise ArtifactVerificationError("dataset artifacts cannot be symlinks")
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ArtifactVerificationError(
                f"dataset artifact is missing: {artifact.relative_path}"
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ArtifactVerificationError("dataset artifact escaped its root")
        byte_size = resolved.stat().st_size
        if byte_size != artifact.byte_size:
            raise ArtifactVerificationError(
                f"dataset artifact size mismatch: {artifact.relative_path}"
            )
        digest = sha256_file(resolved)
        if digest != artifact.sha256:
            raise ArtifactVerificationError(
                f"dataset artifact hash mismatch: {artifact.relative_path}"
            )
        verified.append(
            ArtifactVerification(
                dataset_id=manifest.dataset_id,
                relative_path=artifact.relative_path,
                sha256=digest,
                byte_size=byte_size,
            )
        )
    return tuple(verified)
