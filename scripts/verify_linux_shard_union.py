"""Verify that independently hermetic Linux jobs executed the entire suite.

The old all-in-one runner timed out after a second PostgreSQL run. This gate
admits only one exact-source PostgreSQL component and every disjoint full-suite
shard, with read-back test reports and identical full collection inventories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from scripts.verify_final_hermetic import (
    ARCHIVE_REQUIRED_CASES,
    verify_linux_collection,
    verify_pytest_report,
)
from scripts.verify_linux_shard import POSTGRES_TEST_PATHS, SHARD_COUNT

COMMON_STAGES = (
    "build",
    "image-identity",
    "network-create",
    "postgres-start",
    "redis-start",
    "copied-manifest",
    "copied-source-exact",
    "dependencies",
    "dependency-lock",
    "ruff",
    "format",
    "migration-initial-supported",
    "migration-upgrade",
    "migration-identity",
    "migration-drift",
    "migration-downgrade",
    "migration-reupgrade",
    "migration-reidentity",
    "migration-redrift",
)


def _json(path: Path):
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 16 * 1024 * 1024
    ):
        raise ValueError(f"linux_union_evidence_missing_or_unsafe:{path.name}")
    return json.loads(path.read_bytes())


def _verify_hashes(root: Path):
    recorded = _json(root / "results.sha256.json")
    if not isinstance(recorded, dict):
        raise TypeError("linux_union_hash_map_invalid")
    for name in ("identity.json", "steps.json", "source-files.json"):
        if name not in recorded:
            raise ValueError("linux_union_core_hash_missing")
    for name, digest in recorded.items():
        if (
            type(name) is not str
            or type(digest) is not str
            or not re.fullmatch("[a-f0-9]{64}", digest)
            or Path(name).is_absolute()
            or ".." in Path(name).parts
        ):
            raise ValueError("linux_union_hash_map_invalid")
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError(f"linux_union_hash_mismatch:{name}")
    return recorded


def verify_union(
    artifacts: Path, *, commit: str, tree: str, manifest_sha256: str
) -> dict:
    if any(not re.fullmatch("[a-f0-9]{40}", value) for value in (commit, tree)):
        raise ValueError("linux_union_git_identity_invalid")
    if not re.fullmatch("[a-f0-9]{64}", manifest_sha256):
        raise ValueError("linux_union_manifest_invalid")
    names = {f"ctcc-linux-postgres-{commit}"} | {
        f"ctcc-linux-shard-{i}-{commit}" for i in range(SHARD_COUNT)
    }
    actual = {path.name for path in artifacts.iterdir() if path.is_dir()}
    if actual != names:
        raise ValueError("linux_union_component_set_incomplete")
    full: list[str] | None = None
    selected_union: set[str] = set()
    common_identity: tuple | None = None
    components = []
    for mode, index in (
        ("postgres", None),
        *(("shard", i) for i in range(SHARD_COUNT)),
    ):
        name = (
            f"ctcc-linux-postgres-{commit}"
            if mode == "postgres"
            else f"ctcc-linux-shard-{index}-{commit}"
        )
        attachment = artifacts / name
        root = attachment / "ctcc-validation"
        if attachment.is_symlink() or root.is_symlink():
            raise ValueError("linux_union_artifact_symlink")
        hashes = _verify_hashes(root)
        identity = _json(root / "identity.json")
        stages = _json(root / "steps.json")
        if (
            identity.get("commit") != commit
            or identity.get("tree") != tree
            or identity.get("manifest_sha256") != manifest_sha256
            or identity.get("source_files_sha256")
            != hashlib.sha256((root / "source-files.json").read_bytes()).hexdigest()
            or identity.get("suite") != mode
            or identity.get("shard_index") != index
            or identity.get("shard_count") != SHARD_COUNT
            or identity.get("host_credentials") is not False
            or identity.get("host_env") is not False
            or identity.get("external_order_writes") != 0
            or identity.get("trading_acceptance") is not False
            or identity.get("component_validation") != "PASS"
            or identity.get("hermetic_regression") != "PENDING_UNION"
            or identity.get("cleanup") != "PASS"
            or not isinstance(identity.get("migration"), dict)
        ):
            raise ValueError(f"linux_union_component_identity_rejected:{name}")
        archive = attachment / "ctcc-source.tar"
        if (
            archive.is_symlink()
            or not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest()
            != identity.get("archive_sha256")
        ):
            raise ValueError(f"linux_union_archive_readback_changed:{name}")
        shared = (
            identity.get("archive_sha256"),
            identity.get("source_files_sha256"),
            identity.get("runner_sha256"),
            identity.get("migration"),
        )
        if common_identity is None:
            common_identity = shared
        elif shared != common_identity:
            raise ValueError("linux_union_source_or_migration_drift")
        if not isinstance(stages, list) or any(
            not any(
                step.get("name") == required and step.get("exit_code") == 0
                for step in stages
            )
            for required in COMMON_STAGES
        ):
            raise ValueError(f"linux_union_common_stage_missing:{name}")
        stage = "postgres-intent" if mode == "postgres" else f"linux-shard-{index}"
        if not any(
            step.get("name") == stage and step.get("exit_code") == 0 for step in stages
        ):
            raise ValueError(f"linux_union_suite_stage_failed:{name}")
        if mode == "postgres" and (
            identity.get("synthetic_process_pg_redis_crash_durability") != "PASS"
            or identity.get("api_service_restart") != "PASS"
        ):
            raise ValueError("linux_union_restart_acceptance_missing")
        xml = root / "test-results" / f"{stage}.xml"
        collection_path = (
            root
            / "test-results"
            / (
                "postgres-collection.json"
                if mode == "postgres"
                else f"{stage}-collection.json"
            )
        )
        for relative in (
            xml.relative_to(root).as_posix(),
            collection_path.relative_to(root).as_posix(),
        ):
            if relative not in hashes:
                raise ValueError("linux_union_test_hash_missing")
        report = verify_pytest_report(
            xml,
            required_cases=ARCHIVE_REQUIRED_CASES if mode == "postgres" else (),
            required_modules=(
                tuple(
                    sorted(
                        path.removesuffix(".py").replace("/", ".")
                        for path in POSTGRES_TEST_PATHS
                    )
                )
                if mode == "postgres"
                else ()
            ),
            allow_unrelated_skips=mode == "shard",
        )
        recorded_report = identity.get(
            "postgres_tests" if mode == "postgres" else "linux_tests"
        )
        if report != recorded_report:
            raise ValueError("linux_union_test_report_readback_changed")
        collection = verify_linux_collection(
            collection_path,
            mode=mode,
            index=index,
            report=report,
        )
        if identity.get("collection") != collection:
            raise ValueError("linux_union_collection_readback_changed")
        record = _json(collection_path)
        if full is None:
            full = record["full_nodeids"]
        elif record["full_nodeids"] != full:
            raise ValueError("linux_union_full_collection_drift")
        for nodeid in record["selected_nodeids"]:
            if nodeid in selected_union:
                raise ValueError("linux_union_duplicate_execution")
            selected_union.add(nodeid)
        components.append(
            {
                "component": name,
                "selected": collection["selected_count"],
                "passed": report["passed"],
                "skipped": report["skipped"],
                "junit_sha256": report["sha256"],
                "collection_sha256": collection["sha256"],
            }
        )
    assert full is not None
    if selected_union != set(full):
        raise ValueError("linux_union_missing_full_suite_cases")
    return {
        "schema": "ctcc.linux_hermetic_union.v1",
        "commit": commit,
        "tree": tree,
        "manifest_sha256": manifest_sha256,
        "full_collected_cases": len(full),
        "selected_cases": sum(item["selected"] for item in components),
        "passed_cases": sum(item["passed"] for item in components),
        "skipped_cases": sum(item["skipped"] for item in components),
        "components": components,
        "hermetic_regression": "PASS",
        "trading_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify_union(
        args.artifacts,
        commit=args.commit,
        tree=args.tree,
        manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
    )
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
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
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
