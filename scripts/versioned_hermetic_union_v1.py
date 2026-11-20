"""Closed-profile source/JUnit/collection union without borrowing old results."""

from __future__ import annotations

from scripts import verify_final_hermetic as shared
from scripts.evidence_json import read_evidence_json
from scripts.verify_current0055_linux_union import (
    _archive_identity,
    _selected_passed_cases,
)
from scripts.versioned_hermetic_engine_v1 import contract_for


def specs_for(contract, commit):
    return (
        *(
            ("postgres", index, f"ctcc-linux-postgres-{index}-{commit}")
            for index in range(contract.collection.POSTGRES_COMPONENT_COUNT)
        ),
        *(
            ("shard", index, f"ctcc-linux-shard-{index}-{commit}")
            for index in range(contract.collection.SHARD_COUNT)
        ),
    )


def verify_union(profile, artifacts, *, commit, tree, manifest_sha256):
    contract = contract_for(profile)
    need = contract._need
    need(
        type(commit) is type(tree) is type(manifest_sha256) is str
        and contract._GIT.fullmatch(commit) is not None
        and contract._GIT.fullmatch(tree) is not None
        and contract._SHA.fullmatch(manifest_sha256) is not None
    )
    need(artifacts.is_dir() and not artifacts.is_symlink())
    specs = specs_for(contract, commit)
    children = list(artifacts.iterdir())
    need(
        {path.name for path in children} == {name for _, _, name in specs}
        and all(path.is_dir() and not path.is_symlink() for path in children)
    )
    required = set(contract.REQUIRED_CASES)
    need(bool(required) and len(required) == len(contract.REQUIRED_CASES))
    full, common, selected_union, runs, components = None, None, set(), set(), []
    counts = {case: 0 for case in required}
    actual_profile = None
    extra_summary = {}
    for mode, index, name in specs:
        attachment = artifacts / name
        root, archive = attachment / "ctcc-validation", attachment / "ctcc-source.tar"
        verified = contract.verify_component(
            root, archive, commit=commit, tree=tree, manifest_sha256=manifest_sha256
        )
        need(
            type(verified) is dict
            and set(verified)
            == {
                "identity",
                "full_nodeids",
                "selected_nodeids",
                "historical0056",
                contract.PROFILE_RESULT_KEY,
                *contract.EXTRA_RESULT_KEYS,
            }
        )
        identity, nodes, selected = (
            verified["identity"],
            verified["full_nodeids"],
            verified["selected_nodeids"],
        )
        extras = contract.union_extras(verified, mode, index)
        need(type(extras) is dict and not extra_summary.keys() & extras.keys())
        extra_summary.update(extras)
        need(identity == read_evidence_json(contract._read(root, "identity.json")))
        need(
            identity["suite"] == mode
            and identity["shard_index"] == (index if mode == "shard" else None)
            and identity["postgres_component_index"]
            == (index if mode == "postgres" else None)
        )
        need(
            identity["runner_contract"] == contract.RUNNER_CONTRACT
            and identity["source_contract_head"] == contract.HEAD
            and identity["run_id"] not in runs
        )
        runs.add(identity["run_id"])
        pin, files = _archive_identity(archive, commit=commit, tree=tree)
        mapping = contract._read(root, "source-files.json")
        need(
            pin == identity["archive_sha256"]
            and read_evidence_json(mapping) == files
            and contract.digest(mapping) == identity["source_files_sha256"]
        )
        need(
            files["MANIFEST.sha256"]["sha256"] == manifest_sha256
            and identity["migration"]["source_head"]
            == identity["migration"]["database_head"]
            == contract.HEAD
        )
        same = (
            pin,
            contract.digest(mapping),
            manifest_sha256,
            contract.migration_digest(files),
        )
        if common is None:
            common = same
        need(common == same)
        junit, selection_path = contract.component_report_paths(mode, index)
        report = shared.verify_pytest_report(
            root / junit,
            required_cases=contract.required_cases(mode, index),
            allow_unrelated_skips=mode != "postgres",
        )
        selection_raw = contract._read(root, selection_path)
        selection = contract.collection.verify_collection(
            selection_raw, mode=mode, index=index, report=report
        )
        shared.verify_selected_junit_nodes(
            root / junit, selected, expected_sha256=report["sha256"]
        )
        need(
            selection["full_nodeids"] == nodes
            and selection["selected_nodeids"] == selected
            and identity["tests"] == report
        )
        need(full is None or full == nodes)
        if full is None:
            full = nodes
        need(not selected_union.intersection(selected))
        selected_union.update(selected)
        for case, count in _selected_passed_cases(
            contract._read(root, junit), required
        ).items():
            counts[case] += count
        if mode == "postgres" and index == 2:
            measurements = verified[contract.PROFILE_RESULT_KEY]
            need(
                type(measurements) is list
                and len(measurements) == len(contract.g12.CASES)
            )
            actual_profile = {
                key: measurements[0][key] for key in contract.PROFILE_METADATA_KEYS
            }
        components.append(
            {
                "name": name,
                "mode": mode,
                "index": index,
                "run_id": identity["run_id"],
                "identity_sha256": contract.digest(
                    contract._read(root, "identity.json")
                ),
                "junit_sha256": report["sha256"],
                "collection_sha256": contract.digest(selection_raw),
                "selected_cases": len(selected),
                "passed_cases": report["passed"],
                "skipped_cases": report["skipped"],
                "component_validation": "PASS",
            }
        )
    need(
        full is not None
        and common is not None
        and selected_union == set(full)
        and len(selected_union) == len(full)
        and all(count == 1 for count in counts.values())
    )
    need(
        actual_profile is not None and actual_profile["database_head"] == contract.HEAD
    )
    return {
        "schema": f"ctcc.current{contract.HEAD}.linux_hermetic_union.v1",
        "source_contract_head": contract.HEAD,
        "runner_contract": contract.RUNNER_CONTRACT,
        "commit": commit,
        "tree": tree,
        "manifest_sha256": manifest_sha256,
        "archive_sha256": common[0],
        "source_files_sha256": common[1],
        "migration_sha256": common[3],
        "full_collection_sha256": contract.collection.collection_digest(full),
        "full_collected_cases": len(full),
        "selected_cases": len(selected_union),
        "passed_cases": sum(row["passed_cases"] for row in components),
        "skipped_cases": sum(row["skipped_cases"] for row in components),
        f"required_current{int(contract.HEAD)}_cases": len(counts),
        "postgres_component_count": 3,
        "shard_count": 8,
        contract.PROFILE_RESULT_KEY: actual_profile,
        "components": components,
        "hermetic_regression": "PASS",
        "execution_authority": False,
        "trading_acceptance": False,
    } | extra_summary
