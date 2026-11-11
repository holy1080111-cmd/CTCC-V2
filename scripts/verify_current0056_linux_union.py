"""Read the complete, exact-source DB0056 Docker regression union."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.current0056_hermetic_contract import (
    HEAD,
    REQUIRED_CURRENT56_CASES,
    RUNNER_CONTRACT,
    component_report_paths,
    component_stage_names,
    verify_current56_component,
)
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_linux_union import (
    _GIT,
    _MAX_JUNIT,
    _SHA,
    _archive_identity,
    _component_specs,
    _digest,
    _read,
    _selected_passed_cases,
)
from scripts.verify_final_hermetic import (
    verify_linux_collection,
    verify_pytest_report,
    verify_selected_junit_nodes,
)


def verify_union(
    artifacts: Path, *, commit: str, tree: str, manifest_sha256: str
) -> dict:
    """Read back exactly two PostgreSQL and eight disjoint full-suite shards."""
    if (
        type(commit) is not str
        or _GIT.fullmatch(commit) is None
        or type(tree) is not str
        or _GIT.fullmatch(tree) is None
        or type(manifest_sha256) is not str
        or _SHA.fullmatch(manifest_sha256) is None
        or artifacts.is_symlink()
        or not artifacts.is_dir()
    ):
        raise ValueError("current56_union_input_invalid")
    specs = _component_specs(commit)
    expected_names = {name for _, _, name in specs}
    children = list(artifacts.iterdir())
    if {child.name for child in children} != expected_names or any(
        child.is_symlink() or not child.is_dir() for child in children
    ):
        raise ValueError("current56_union_component_set_incomplete")
    required = set(REQUIRED_CURRENT56_CASES)
    if (
        len(required) != len(REQUIRED_CURRENT56_CASES)
        or not required
        or any(
            type(module) is not str
            or not module.startswith("tests.")
            or type(case) is not str
            or not case.startswith("test_")
            for module, case in required
        )
    ):
        raise ValueError("current56_union_required_case_contract_invalid")

    full: list[str] | None = None
    selected_union: set[str] = set()
    required_counts = {case: 0 for case in required}
    common: tuple[str, str, str, str] | None = None
    components = []
    run_ids: set[str] = set()
    for mode, index, name in specs:
        attachment = artifacts / name
        root = attachment / "ctcc-validation"
        archive_path = attachment / "ctcc-source.tar"
        if root.is_symlink() or not root.is_dir():
            raise ValueError("current56_union_component_root_invalid")
        verified = verify_current56_component(
            root,
            archive_path,
            commit=commit,
            tree=tree,
            manifest_sha256=manifest_sha256,
        )
        if (
            type(verified) is not dict
            or set(verified) != {"identity", "full_nodeids", "selected_nodeids"}
            or type(verified["identity"]) is not dict
            or type(verified["full_nodeids"]) is not list
            or type(verified["selected_nodeids"]) is not list
        ):
            raise ValueError("current56_union_component_contract_invalid")
        identity = verified["identity"]
        nodes = verified["full_nodeids"]
        selected = verified["selected_nodeids"]
        if (
            identity != read_evidence_json(_read(root, "identity.json"))
            or identity.get("suite") != mode
            or identity.get("shard_index") != (index if mode == "shard" else None)
            or identity.get("postgres_component_index")
            != (index if mode == "postgres" else None)
            or identity.get("runner_contract") != RUNNER_CONTRACT
            or identity.get("source_contract_head") != HEAD
            or type(identity.get("run_id")) is not str
            or identity["run_id"] in run_ids
            or identity.get("trading_acceptance") is not False
            or type(identity.get("external_order_writes")) is not int
            or identity["external_order_writes"] != 0
            or type(nodes) is not list
            or not nodes
            or any(type(node) is not str for node in nodes)
            or len(nodes) != len(set(nodes))
            or type(selected) is not list
            or not selected
            or any(type(node) is not str for node in selected)
            or len(selected) != len(set(selected))
            or not set(selected) <= set(nodes)
        ):
            raise ValueError("current56_union_component_identity_invalid")
        run_ids.add(identity["run_id"])
        stages = read_evidence_json(_read(root, "steps.json"))
        if type(stages) is not list or tuple(
            step.get("name") for step in stages
        ) != component_stage_names(mode, index):
            raise ValueError("current56_union_component_stage_changed")

        archive_sha256, files = _archive_identity(
            archive_path, commit=commit, tree=tree
        )
        source_map = _read(root, "source-files.json")
        migration = identity.get("migration")
        if (
            type(migration) is not dict
            or migration.get("source_head") != HEAD
            or migration.get("database_head") != HEAD
            or migration.get("status") != "PASS"
            or type(migration.get("migration_sha256")) is not str
            or _SHA.fullmatch(migration["migration_sha256"]) is None
            or archive_sha256 != identity.get("archive_sha256")
            or read_evidence_json(source_map) != files
            or _digest(source_map) != identity.get("source_files_sha256")
            or files["MANIFEST.sha256"]["sha256"] != manifest_sha256
        ):
            raise ValueError("current56_union_source_or_migration_changed")
        same = (
            archive_sha256,
            _digest(source_map),
            manifest_sha256,
            migration["migration_sha256"],
        )
        if common is None:
            common = same
        elif common != same:
            raise ValueError("current56_union_component_source_diverged")

        junit_name, collection_name = component_report_paths(mode, index)
        junit_raw = _read(root, junit_name, maximum=_MAX_JUNIT)
        report = verify_pytest_report(
            root / junit_name, allow_unrelated_skips=mode == "shard"
        )
        collection = verify_linux_collection(
            root / collection_name, mode=mode, index=index, report=report
        )
        verify_selected_junit_nodes(
            root / junit_name, selected, expected_sha256=report["sha256"]
        )
        if (
            report != identity.get("tests")
            or report["tests"] != len(selected)
            or (mode == "postgres" and report["skipped"] != 0)
            or (full is not None and nodes != full)
            or selected_union.intersection(selected)
        ):
            raise ValueError("current56_union_collection_or_report_diverged")
        if full is None:
            full = nodes
        selected_union.update(selected)
        for case, count in _selected_passed_cases(junit_raw, required).items():
            required_counts[case] += count
        components.append(
            {
                "name": name,
                "mode": mode,
                "index": index,
                "identity_sha256": _digest(_read(root, "identity.json")),
                "run_id": identity["run_id"],
                "junit_sha256": report["sha256"],
                "collection_sha256": collection["sha256"],
                "selected_cases": len(selected),
                "passed_cases": report["passed"],
                "skipped_cases": report["skipped"],
                "component_validation": "PASS",
            }
        )

    if (
        full is None
        or common is None
        or selected_union != set(full)
        or len(selected_union) != len(full)
        or any(count != 1 for count in required_counts.values())
    ):
        raise ValueError("current56_union_required_coverage_incomplete")
    return {
        "schema": "ctcc.current0056.linux_hermetic_union.v1",
        "source_contract_head": HEAD,
        "runner_contract": RUNNER_CONTRACT,
        "commit": commit,
        "tree": tree,
        "manifest_sha256": manifest_sha256,
        "archive_sha256": common[0],
        "source_files_sha256": common[1],
        "migration_sha256": common[3],
        "full_collection_sha256": _digest(
            json.dumps(full, ensure_ascii=True, separators=(",", ":")).encode()
        ),
        "full_collected_cases": len(full),
        "selected_cases": len(selected_union),
        "passed_cases": sum(component["passed_cases"] for component in components),
        "skipped_cases": sum(component["skipped_cases"] for component in components),
        "required_current56_cases": len(required_counts),
        "postgres_component_count": 2,
        "shard_count": 8,
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
    if (
        args.manifest.is_symlink()
        or not args.manifest.is_file()
        or not 0 < args.manifest.stat().st_size <= 16 * 1024 * 1024
        or args.output.exists()
        or args.output.is_symlink()
        or args.output.parent.is_symlink()
        or not args.output.parent.is_dir()
    ):
        raise ValueError("current56_union_cli_path_invalid")
    result = verify_union(
        args.artifacts,
        commit=args.commit,
        tree=args.tree,
        manifest_sha256=_digest(args.manifest.read_bytes()),
    )
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
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
