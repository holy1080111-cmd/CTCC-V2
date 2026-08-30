from __future__ import annotations

from pathlib import Path

import pytest

from app.research.external_benchmarks import (
    ArtifactVerificationError,
    verify_dataset_artifacts,
)
from tests.unit.research.helpers import trade_manifest


def test_artifact_verification_checks_size_and_sha256(tmp_path: Path) -> None:
    payload = b"trade_id,timestamp,price,quantity\n1,1,2,3\n"
    manifest = trade_manifest(payload=payload)
    artifact = tmp_path / "raw" / "trades.csv"
    artifact.parent.mkdir()
    artifact.write_bytes(payload)

    receipt = verify_dataset_artifacts(manifest, tmp_path)

    assert len(receipt) == 1
    assert receipt[0].verified is True
    assert receipt[0].sha256 == manifest.artifacts[0].sha256
    assert receipt[0].execution_authority is False

    artifact.write_bytes(payload + b"tampered")
    with pytest.raises(ArtifactVerificationError, match="size mismatch"):
        verify_dataset_artifacts(manifest, tmp_path)


def test_artifact_verification_rejects_symlinks(tmp_path: Path) -> None:
    payload = b"test-data"
    manifest = trade_manifest(payload=payload)
    outside = tmp_path / "outside.csv"
    outside.write_bytes(payload)
    artifact = tmp_path / "raw" / "trades.csv"
    artifact.parent.mkdir()
    artifact.symlink_to(outside)

    with pytest.raises(ArtifactVerificationError, match="symlinks"):
        verify_dataset_artifacts(manifest, tmp_path)


def test_artifact_verification_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ArtifactVerificationError, match="does not exist"):
        verify_dataset_artifacts(trade_manifest(), tmp_path / "missing")
