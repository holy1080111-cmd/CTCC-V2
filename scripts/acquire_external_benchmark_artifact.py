from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from app.research.external_benchmarks import (
    AcquisitionLimits,
    ArchiveInspectionPolicy,
    ExternalArtifactAcquisitionRequest,
    acquire_external_artifact,
)


def _real_file(path: Path, name: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{name} cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{name} does not exist") from exc
    if not resolved.is_file():
        raise ValueError(f"{name} must be a real file")
    return resolved


async def _run(args: argparse.Namespace) -> int:
    request_path = _real_file(args.request, "request")
    request = ExternalArtifactAcquisitionRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    receipt = await acquire_external_artifact(
        request,
        args.dataset_root,
        limits=AcquisitionLimits(
            max_bytes=args.max_bytes,
            max_redirects=args.max_redirects,
        ),
        archive_policy=ArchiveInspectionPolicy(
            max_members=args.max_archive_members,
            max_total_uncompressed_bytes=args.max_uncompressed_bytes,
            max_single_member_bytes=args.max_single_member_bytes,
            max_expansion_ratio=args.max_expansion_ratio,
        ),
    )
    print(receipt.model_dump_json(indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire one operator-reviewed external benchmark artifact "
            "without granting runtime or execution authority"
        )
    )
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024 * 1024)
    parser.add_argument("--max-redirects", type=int, default=3)
    parser.add_argument("--max-archive-members", type=int, default=10_000)
    parser.add_argument(
        "--max-uncompressed-bytes",
        type=int,
        default=4 * 1024 * 1024 * 1024,
    )
    parser.add_argument(
        "--max-single-member-bytes",
        type=int,
        default=1024 * 1024 * 1024,
    )
    parser.add_argument("--max-expansion-ratio", default="100")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
