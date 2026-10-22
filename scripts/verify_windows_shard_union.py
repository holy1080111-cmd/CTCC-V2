"""Read back every native Windows shard before admitting the complete suite.

The final gate deliberately reuses verify_windows_regression.verify_evidence on
the exact union, so sharding cannot waive an approved-skip, native-case,
integration-module, source, or dependency assertion.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from scripts.verify_windows_regression import (
    NO_DB_INTEGRATION,
    WindowsRegressionError,
    _json,
    _junit_key,
    _sha256,
    test_selection,
    verify_evidence,
)
from scripts.verify_windows_shard import SHARD_COUNT, selected_for

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ARTIFACT_FILES = frozenset(
    {
        "source.zip",
        "source-identity.json",
        "dependency-result.json",
        "windows-collection.json",
        "windows-junit.xml",
    }
)


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    if _json(path) != value:
        raise WindowsRegressionError("windows_union_json_readback_changed")


def _zip_members(path: Path) -> dict[str, bytes]:
    """Read exact archived members independent of ZIP timezone metadata."""
    if (
        path.is_symlink()
        or path.is_junction()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 64 * 1024 * 1024
    ):
        raise WindowsRegressionError("windows_union_archive_missing_or_unsafe")
    members: dict[str, bytes] = {}
    total_uncompressed = 0
    with zipfile.ZipFile(path) as zipped:
        for info in zipped.infolist():
            name = info.filename
            relative = PurePosixPath(name)
            canonical = relative.as_posix() + ("/" if info.is_dir() else "")
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or name != canonical
                or any(part in {"", ".", ".."} for part in relative.parts)
                or name in members
                or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
            ):
                raise WindowsRegressionError("windows_union_archive_entry_unsafe")
            total_uncompressed += info.file_size
            if (
                info.file_size > 32 * 1024 * 1024
                or total_uncompressed > 128 * 1024 * 1024
            ):
                raise WindowsRegressionError("windows_union_archive_too_large")
            members[name] = zipped.read(info)
    if not members:
        raise WindowsRegressionError("windows_union_archive_empty")
    return members


def _shard_record(record: dict, *, index: int, root: Path) -> list[dict]:
    if (
        record.get("schema") != "ctcc.windows_full_collection_shard.v1"
        or type(record.get("shard_index")) is not int
        or record.get("shard_index") != index
        or type(record.get("shard_count")) is not int
        or record.get("shard_count") != SHARD_COUNT
        or record.get("requested_paths") != list(test_selection(root))
        or record.get("manifest_sha256") != _sha256(root / "MANIFEST.sha256")
        or record.get("python") != sys.version
        or record.get("selection_filters_absent") is not True
        or type(record.get("pytest_exitstatus")) is not int
        or record.get("pytest_exitstatus") != 0
        or not isinstance(record.get("full_cases"), list)
        or not record["full_cases"]
    ):
        raise WindowsRegressionError("windows_union_collection_identity_invalid")
    full = record["full_cases"]
    seen_nodeids = set()
    seen_keys = set()
    for case in full:
        if not isinstance(case, dict) or type(case.get("nodeid")) is not str:
            raise WindowsRegressionError("windows_union_case_invalid")
        nodeid = case["nodeid"]
        key = _junit_key(nodeid)
        if (
            case.get("classname") != key[0]
            or case.get("name") != key[1]
            or nodeid in seen_nodeids
            or key in seen_keys
        ):
            raise WindowsRegressionError("windows_union_case_mapping_or_duplicate")
        seen_nodeids.add(nodeid)
        seen_keys.add(key)
    required_modules = {
        path.relative_to(root).as_posix()
        for path in (root / "tests" / "unit").rglob("test_*.py")
        if path.is_file()
    } | {f"tests/integration/{name}" for name in NO_DB_INTEGRATION}
    if {nodeid.split("::", 1)[0] for nodeid in seen_nodeids} != required_modules:
        raise WindowsRegressionError("windows_union_full_module_set_changed")
    expected = [case["nodeid"] for case in full if selected_for(index, case["nodeid"])]
    if (
        not expected
        or record.get("selected_nodeids") != expected
        or record.get("observed_nodeids") != expected
    ):
        raise WindowsRegressionError("windows_union_shard_selection_or_observation")
    return full


def _junit_cases(path: Path, expected: set[tuple[str, str]]) -> list[ET.Element]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 64 * 1024 * 1024
    ):
        raise WindowsRegressionError("windows_union_junit_missing_or_unsafe")
    xml = ET.fromstring(path.read_bytes())
    if xml.tag != "testsuites" or len(xml) != 1 or xml[0].tag != "testsuite":
        raise WindowsRegressionError("windows_union_junit_schema_invalid")
    suite = xml[0]
    cases = suite.findall("testcase")
    actual = [(case.get("classname"), case.get("name")) for case in cases]
    totals = {
        "tests": len(cases),
        "failures": sum(case.find("failure") is not None for case in cases),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": sum(case.find("skipped") is not None for case in cases),
    }
    if (
        not cases
        or len(actual) != len(set(actual))
        or set(actual) != expected
        or any(suite.get(key) != str(value) for key, value in totals.items())
        or totals["failures"]
        or totals["errors"]
    ):
        raise WindowsRegressionError("windows_union_junit_cases_or_outcomes_changed")
    return cases


def verify_union(
    artifacts: Path,
    *,
    root: Path,
    git_archive: Path,
    commit: str,
    tree: str,
    manifest_sha256: str,
    output: Path,
) -> dict:
    if (
        not HEX40.fullmatch(commit)
        or not HEX40.fullmatch(tree)
        or not HEX64.fullmatch(manifest_sha256)
        or _sha256(root / "MANIFEST.sha256") != manifest_sha256
    ):
        raise WindowsRegressionError("windows_union_git_identity_invalid")
    git_members = _zip_members(git_archive)
    names = {f"ctcc-windows-shard-{index}-{commit}" for index in range(SHARD_COUNT)}
    if (
        artifacts.is_symlink()
        or artifacts.is_junction()
        or {path.name for path in artifacts.iterdir() if path.is_dir()} != names
    ):
        raise WindowsRegressionError("windows_union_component_set_incomplete")
    if any(
        path.is_symlink() or path.is_junction() or not path.is_dir()
        for path in artifacts.iterdir()
    ):
        raise WindowsRegressionError("windows_union_component_path_unsafe")
    full: list[dict] | None = None
    selected_union: set[str] = set()
    all_cases: list[ET.Element] = []
    common_source: tuple | None = None
    common_dependency: str | None = None
    components = []
    first: Path | None = None
    for index in range(SHARD_COUNT):
        component = artifacts / f"ctcc-windows-shard-{index}-{commit}"
        if {path.name for path in component.iterdir()} != ARTIFACT_FILES:
            raise WindowsRegressionError("windows_union_component_files_incomplete")
        if any(
            path.is_symlink() or path.is_junction() or not path.is_file()
            for path in component.iterdir()
        ):
            raise WindowsRegressionError("windows_union_component_file_unsafe")
        archive_path = component / "source.zip"
        if _zip_members(archive_path) != git_members:
            raise WindowsRegressionError(
                "windows_union_archive_members_differ_from_git"
            )
        source = _json(component / "source-identity.json")
        dependency = _json(component / "dependency-result.json")
        archive_sha = _sha256(archive_path)
        if (
            set(source)
            != {
                "schema",
                "commit",
                "github_sha",
                "tree",
                "archive_sha256",
                "manifest_sha256",
                "runner_label",
                "runner_image_version",
            }
            or source.get("schema") != "ctcc.windows_ci_source.v1"
            or type(source.get("runner_image_version")) is not str
            or not source["runner_image_version"]
        ):
            raise WindowsRegressionError("windows_union_source_schema_invalid")
        source_key = tuple(
            source.get(name)
            for name in (
                "commit",
                "github_sha",
                "tree",
                "manifest_sha256",
                "runner_label",
            )
        )
        dependency_key = json.dumps(dependency, sort_keys=True, separators=(",", ":"))
        if (
            source_key[:3] != (commit, commit, tree)
            or source.get("archive_sha256") != archive_sha
            or source_key[3:] != (manifest_sha256, "windows-2025")
            or dependency.get("target") != "windows"
            or dependency.get("accepted_full_validation_baseline") is not False
            or (dependency.get("installed_inventory") or {}).get("mode")
            not in {
                "exact_inventory_match",
                "isolated_exact_with_one_source_project_metadata_record",
            }
        ):
            raise WindowsRegressionError(
                "windows_union_source_or_lock_identity_invalid"
            )
        if common_source is None:
            common_source = source_key
            common_dependency = dependency_key
        elif source_key != common_source or dependency_key != common_dependency:
            raise WindowsRegressionError("windows_union_source_or_lock_drift")
        record_path = component / "windows-collection.json"
        record = _json(record_path)
        current_full = _shard_record(record, index=index, root=root)
        if full is None:
            full = current_full
        elif current_full != full:
            raise WindowsRegressionError("windows_union_full_collection_drift")
        selected = record["selected_nodeids"]
        if selected_union.intersection(selected):
            raise WindowsRegressionError("windows_union_duplicate_execution")
        selected_union.update(selected)
        expected_keys = {_junit_key(nodeid) for nodeid in selected}
        junit_path = component / "windows-junit.xml"
        all_cases.extend(_junit_cases(junit_path, expected_keys))
        components.append(
            {
                "index": index,
                "selected": len(selected),
                "archive_sha256": archive_sha,
                "source_identity_sha256": _sha256(component / "source-identity.json"),
                "dependency_result_sha256": _sha256(
                    component / "dependency-result.json"
                ),
                "collection_sha256": _sha256(record_path),
                "junit_sha256": _sha256(junit_path),
            }
        )
        if first is None:
            first = component
    assert full is not None and first is not None
    if selected_union != {case["nodeid"] for case in full}:
        raise WindowsRegressionError("windows_union_full_suite_incomplete")
    output.mkdir(exist_ok=False)
    collection = output / "windows-collection.json"
    merged_record = {
        "schema": "ctcc.windows_ci_collection.v1",
        "requested_paths": list(test_selection(root)),
        "manifest_sha256": manifest_sha256,
        "python": sys.version,
        "selection_filters_absent": True,
        "cases": full,
    }
    _write_json(collection, merged_record)
    suite = ET.Element(
        "testsuite",
        name="pytest",
        tests=str(len(all_cases)),
        failures="0",
        errors="0",
        skipped=str(sum(case.find("skipped") is not None for case in all_cases)),
    )
    suite.extend(all_cases)
    suites = ET.Element("testsuites")
    suites.append(suite)
    junit = output / "windows-junit.xml"
    with junit.open("xb") as stream:
        stream.write(ET.tostring(suites, encoding="utf-8"))
    # The original unsharded acceptance gate remains the final arbiter.
    regression = verify_evidence(
        root,
        first / "source.zip",
        first / "source-identity.json",
        first / "dependency-result.json",
        collection,
        junit,
    )
    result = {
        "schema": "ctcc.windows_ci_shard_union.v1",
        "commit": commit,
        "tree": tree,
        "manifest_sha256": manifest_sha256,
        "full_collected_cases": len(full),
        "selected_cases": sum(item["selected"] for item in components),
        "components": components,
        "merged_collection_sha256": _sha256(collection),
        "merged_junit_sha256": _sha256(junit),
        "regression": regression,
        "full_suite_union": "PASS",
        "execution_authority": False,
    }
    _write_json(output / "windows-union.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--git-archive", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify_union(
            args.artifacts,
            root=args.root,
            git_archive=args.git_archive,
            commit=args.commit,
            tree=args.tree,
            manifest_sha256=_sha256(args.manifest),
            output=args.output,
        )
        print(
            json.dumps(
                {
                    "status": result["full_suite_union"],
                    "full_collected_cases": result["full_collected_cases"],
                    "selected_cases": result["selected_cases"],
                    "union_sha256": _sha256(args.output / "windows-union.json"),
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        code = (
            str(exc) if isinstance(exc, WindowsRegressionError) else type(exc).__name__
        )
        print(f"WINDOWS_UNION_FAIL_CLOSED:{code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
