"""Route an exact Git archive to a closed CI topology; never validation PASS."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

from scripts.current_hermetic_contract_dispatch_v1 import _archive_head
from scripts.verify_final_hermetic import archive_files, archive_tree


def profile(raw, *, commit, tree, archive_sha256):
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= 128 * 1024 * 1024
        or any(
            type(value) is not str or re.fullmatch(pattern, value) is None
            for value, pattern in (
                (commit, r"[a-f0-9]{40}"),
                (tree, r"[a-f0-9]{40}"),
                (archive_sha256, r"[a-f0-9]{64}"),
            )
        )
        or hashlib.sha256(raw).hexdigest() != archive_sha256
    ):
        raise ValueError("hermetic_workflow_exact_source_required")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as stream:
        if stream.pax_headers.get("comment") != commit or archive_tree(stream) != tree:
            raise ValueError("hermetic_workflow_exact_git_required")
        head, files = _archive_head(stream), archive_files(stream)
    if head == 58:
        from scripts.current0058_hermetic_contract_v1 import migration_digest

        migration_digest(files)
    elif head == 57:
        from scripts.current0057_hermetic_contract import migration_digest

        migration_digest(files)
    return {
        "schema": "ctcc.hermetic_workflow_profile.v1",
        "source_head": f"{head:04}",
        "postgres_components": list(range(5))
        if head == 58
        else [0, 1, 2]
        if head == 57
        else [0, 1],
        "shard_indices": list(range(8)),
        "validation_admission": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--archive-sha256", required=True)
    args = parser.parse_args()
    if (
        args.archive.is_symlink()
        or not args.archive.is_file()
        or not 0 < args.archive.stat().st_size <= 128 * 1024 * 1024
    ):
        raise ValueError("hermetic_workflow_archive_unavailable")
    result = profile(
        args.archive.read_bytes(),
        commit=args.commit,
        tree=args.tree,
        archive_sha256=args.archive_sha256,
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
