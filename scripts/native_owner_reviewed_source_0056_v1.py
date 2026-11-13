"""Bind a reviewed owner archive to the exact source executing a diagnostic."""

from pathlib import Path

from scripts.devval_campaign_source import verify_source_checkpoint
from scripts.native_owner_upgrade_0056_v1 import OwnerUpgradePinsV1


class NativeOwnerReviewedSource0056Error(ValueError):
    """Fixed refusal without source or deployment path details."""


def require_exact_installed_owner_source_0056_v1(
    source: Path,
    pins: OwnerUpgradePinsV1,
    source_archive_file: Path,
) -> None:
    """Check archive commit/tree, raw installed files and the complete manifest."""
    try:
        if type(pins) is not OwnerUpgradePinsV1:
            raise ValueError
        observed = verify_source_checkpoint(
            source, source_archive_file, pins.source_archive_sha256
        )
        if observed != {
            "expected_source_commit": pins.source_commit,
            "expected_source_tree": pins.source_tree,
            "expected_source_manifest_sha256": pins.route.source_manifest_sha256,
        }:
            raise ValueError
    except Exception:  # noqa: BLE001 -- preserve a fixed operator-facing denial
        raise NativeOwnerReviewedSource0056Error(
            "db0056_owner_exact_installed_source_refused"
        ) from None
