"""Run one complete actual-source58/physical58 hermetic regression component."""

from __future__ import annotations

import argparse
import io
import re
import tarfile
from pathlib import Path

from scripts import current0058_full_hermetic_contract_v1 as contract
from scripts import verify_final_hermetic as shared
from scripts.versioned_hermetic_engine_v1 import HermeticProfileV1, run_component


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--suite", choices=("full", "postgres", "shard"), default="full"
    )
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--postgres-component-index", type=int)
    args = parser.parse_args()
    if (
        (args.suite == "shard") != (args.shard_index is not None)
        or (args.suite == "postgres") != (args.postgres_component_index is not None)
        or any(
            re.fullmatch(pattern, value) is None
            for pattern, value in (
                (r"[a-f0-9]{40}", args.commit),
                (r"[a-f0-9]{40}", args.tree),
                (r"[a-f0-9]{64}", args.archive_sha256),
            )
        )
    ):
        raise ValueError("current58_full_runner_arguments_invalid")
    index = (
        args.postgres_component_index if args.suite == "postgres" else args.shard_index
    )
    contract.collection._mode(args.suite, index)
    return args


def admit_archive(args, archive_bytes=None):
    raw = contract._read_archive(
        args.archive if archive_bytes is None else archive_bytes
    )
    if contract.digest(raw) != args.archive_sha256:
        raise ValueError("current58_full_archive_changed")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as stream:
        if (
            stream.pax_headers.get("comment") != args.commit
            or shared.archive_tree(stream) != args.tree
        ):
            raise ValueError("current58_full_git_identity_changed")
        files = shared.archive_files(stream)
    contract.require_host_code(files)
    contract.migration_digest(files)
    return raw, files


def main(*, archive_bytes=None):
    args = _arguments()
    raw, files = admit_archive(args, archive_bytes)
    return run_component(HermeticProfileV1.CURRENT0058, args, raw, files)


if __name__ == "__main__":
    raise SystemExit(main())
