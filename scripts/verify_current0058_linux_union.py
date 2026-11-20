"""Independently read all eleven exact-source58 Docker regression components."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts import current0058_full_hermetic_contract_v1 as contract
from scripts.versioned_hermetic_engine_v1 import HermeticProfileV1
from scripts.versioned_hermetic_union_v1 import verify_union as verify_versioned_union


def verify_union(artifacts, *, commit, tree, manifest_sha256):
    return verify_versioned_union(
        HermeticProfileV1.CURRENT0058,
        artifacts,
        commit=commit,
        tree=tree,
        manifest_sha256=manifest_sha256,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract._need(
        not args.manifest.is_symlink()
        and args.manifest.is_file()
        and 0 < args.manifest.stat().st_size <= 16 * 1024 * 1024
    )
    contract._need(
        not args.output.exists()
        and not args.output.is_symlink()
        and not args.output.parent.is_symlink()
        and args.output.parent.is_dir()
    )
    result = verify_union(
        args.artifacts,
        commit=args.commit,
        tree=args.tree,
        manifest_sha256=contract.digest(args.manifest.read_bytes()),
    )
    with args.output.open("xb") as stream:
        stream.write(contract.canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    contract._need(args.output.read_bytes() == contract.canonical(result))
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "commit",
                    "tree",
                    "full_collected_cases",
                    "selected_cases",
                    "passed_cases",
                    "skipped_cases",
                    "hermetic_regression",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
