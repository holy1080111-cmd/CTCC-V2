"""Strict dependency identity matching for release validation.

Ported from the verified September 15 deployment repair. Only one exact source
project metadata record may differ from an independently measured installed
multiset. No package versions are altered and no trading authority is granted.
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


class DependencyMismatch(RuntimeError):
    def __init__(self, code="DEPENDENCIES_DIFFER_FROM_FULL_VALIDATION", details=None):
        super().__init__(code)
        self.details = details if details is not None else {}


def _dependency_inventory():
    importlib.invalidate_caches()
    rows = []
    for distribution in importlib.metadata.distributions():
        metadata_path = getattr(distribution, "_path", None)
        rows.append(
            {
                "name": distribution.metadata.get("Name"),
                "version": distribution.metadata.get("Version"),
                "metadata_path": str(metadata_path)
                if metadata_path is not None
                else None,
            }
        )
    return rows


def _isolated_dependency_inventory():
    # No application imports or environment/configuration output. The isolated
    # child has the same discovery scope as the successful recovery verifier.
    code = """import importlib.metadata,json,sys
rows=[]
for d in importlib.metadata.distributions():
    p=getattr(d,"_path",None)
    rows.append({"name":d.metadata["Name"],"version":d.version,"metadata_path":str(p) if p is not None else None})
print(json.dumps({"python":sys.version,"rows":rows},ensure_ascii=True))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("ISOLATED_INVENTORY_EXIT_NONZERO")
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise ValueError("ISOLATED_INVENTORY_TOO_LARGE")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or not isinstance(value.get("python"), str):
        raise TypeError("ISOLATED_INVENTORY_SCHEMA_INVALID")
    _inventory_counter(value.get("rows"))
    return value


def _inventory_counter(rows):
    if not isinstance(rows, list):
        raise TypeError("DEPENDENCY_INVENTORY_SCHEMA_INVALID")
    pairs = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("DEPENDENCY_INVENTORY_ROW_INVALID")
        name, version = row.get("name"), row.get("version")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or not version
        ):
            raise ValueError("DEPENDENCY_INVENTORY_PAIR_INVALID")
        if row.get("metadata_path") is not None and not isinstance(
            row["metadata_path"], str
        ):
            raise ValueError("DEPENDENCY_METADATA_PATH_INVALID")
        pairs.append((name, version))
    return Counter(pairs)


def _safe_metadata_value(value, *, path=False):
    if not isinstance(value, str):
        return None
    limit = 1024 if path else 256
    if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "[INVALID_METADATA_VALUE]"
    return value


def _counter_rows(counter):
    return [
        {
            "name": _safe_metadata_value(name),
            "version": _safe_metadata_value(version),
            "count": count,
        }
        for (name, version), count in sorted(counter.items())
    ]


def _relevant_metadata(rows, pairs):
    relevant = []
    for row in rows:
        if (row["name"], row["version"]) in pairs or row["name"] == "ctcc-v2":
            relevant.append(
                {
                    "name": _safe_metadata_value(row["name"]),
                    "version": _safe_metadata_value(row["version"]),
                    "metadata_path": _safe_metadata_value(
                        row.get("metadata_path"), path=True
                    ),
                }
            )
    return relevant


def verify_dependencies(root, contract):
    if not isinstance(contract, dict) or contract.get("python") != sys.version:
        raise DependencyMismatch("PYTHON_DIFFERS_FROM_FULL_VALIDATION")
    expected_pairs = [tuple(pair) for pair in contract["dependencies"]]
    expected = Counter(expected_pairs)
    details = {
        "expected_count": len(expected_pairs),
        "current_count": None,
        "isolated_count": None,
        "missing": [],
        "unexpected": [],
        "relevant_metadata": [],
    }
    try:
        rows = _dependency_inventory()
        current = _inventory_counter(rows)
    except Exception as exc:  # noqa: BLE001 -- Fail closed; expose only error class.
        details["current_inventory_error_class"] = type(exc).__name__
        raise DependencyMismatch(details=details) from None
    details["current_count"] = len(rows)
    # Preserve exact existing behavior when the full multiset already matches.
    if current == expected:
        return {
            "mode": "exact_inventory_match",
            "expected_count": len(expected_pairs),
            "current_count": len(rows),
            "source_metadata_excluded": False,
            "excluded_metadata": [],
        }
    missing, unexpected = expected - current, current - expected
    details.update(
        missing=_counter_rows(missing),
        unexpected=_counter_rows(unexpected),
        relevant_metadata=_relevant_metadata(rows, set(missing) | set(unexpected)),
    )
    try:
        isolated = _isolated_dependency_inventory()
        if not isinstance(isolated, dict) or not isinstance(
            isolated.get("python"), str
        ):
            raise TypeError("ISOLATED_INVENTORY_SCHEMA_INVALID")
        isolated_rows = isolated["rows"]
        isolated_counter = _inventory_counter(isolated_rows)
        details["isolated_count"] = len(isolated_rows)
        details["isolated_missing"] = _counter_rows(expected - isolated_counter)
        details["isolated_unexpected"] = _counter_rows(isolated_counter - expected)
        details["isolated_relevant_metadata"] = _relevant_metadata(
            isolated_rows,
            set(expected - isolated_counter) | set(isolated_counter - expected),
        )
    except Exception as exc:  # noqa: BLE001 -- Fail closed; never echo child output.
        details["isolated_inventory_error_class"] = type(exc).__name__
        raise DependencyMismatch(details=details) from None
    if isolated_counter != expected or isolated["python"] != contract["python"]:
        details["isolated_python_matches"] = isolated["python"] == contract["python"]
        raise DependencyMismatch(details=details)
    # Only one extra source-tree project record is admissible. No blanket
    # deduplication, version normalization, or third-party exclusions.
    project_pairs = [pair for pair in expected_pairs if pair[0] == "ctcc-v2"]
    if len(project_pairs) != 1:
        raise DependencyMismatch(details=details)
    project_pair = project_pairs[0]
    if missing or unexpected != Counter({project_pair: 1}):
        raise DependencyMismatch(details=details)
    root = Path(root)
    source_metadata = root / "ctcc_v2.egg-info"
    try:
        if (
            not root.is_absolute()
            or source_metadata.is_symlink()
            or not source_metadata.is_dir()
        ):
            raise ValueError("SOURCE_METADATA_DIRECTORY_INVALID")
        pkg_info = source_metadata / "PKG-INFO"
        if pkg_info.is_symlink() or not pkg_info.is_file():
            raise ValueError("SOURCE_METADATA_FILE_INVALID")
        source_resolved = source_metadata.resolve(strict=True)
        matches = []
        for index, row in enumerate(rows):
            path = row.get("metadata_path")
            if path is None:
                continue
            lexical = Path(path)
            # stdin execution exposes cwd as sys.path[0] == ''. Metadata from
            # that entry legitimately reports a relative egg-info path. Accept
            # only the single direct child when cwd is the verified root.
            if not lexical.is_absolute() and lexical == Path("ctcc_v2.egg-info"):
                if Path.cwd() != root:
                    raise ValueError("RELATIVE_SOURCE_METADATA_CWD_MISMATCH")
                lexical = root / lexical
            if lexical == source_metadata and lexical.is_absolute():
                if lexical.resolve(strict=True) != source_resolved:
                    raise ValueError("SOURCE_METADATA_RESOLUTION_CHANGED")
                if (row["name"], row["version"]) != project_pair:
                    raise ValueError("SOURCE_METADATA_PROJECT_MISMATCH")
                matches.append(index)
        if len(matches) != 1:
            raise ValueError("SOURCE_METADATA_NOT_EXACTLY_ONE_RECORD")
        excluded = {**rows[matches[0]], "metadata_path": str(source_metadata)}
        remaining = [row for index, row in enumerate(rows) if index != matches[0]]
        if _inventory_counter(remaining) != expected:
            raise ValueError("DEPENDENCIES_AFTER_SOURCE_EXCLUSION_DIFFER")
        # Recheck both filesystem objects before recording the exception.
        if (
            source_metadata.is_symlink()
            or not source_metadata.is_dir()
            or pkg_info.is_symlink()
            or not pkg_info.is_file()
        ):
            raise ValueError("SOURCE_METADATA_CHANGED_DURING_CHECK")
    except Exception as exc:  # noqa: BLE001 -- Fail closed on any filesystem error.
        details["source_metadata_error_class"] = type(exc).__name__
        raise DependencyMismatch(details=details) from None
    return {
        "mode": "isolated_exact_with_one_source_project_metadata_record",
        "expected_count": len(expected_pairs),
        "current_count": len(rows),
        "isolated_count": len(isolated_rows),
        "isolated_python_matches": True,
        "source_metadata_excluded": True,
        "excluded_metadata": _relevant_metadata([excluded], {project_pair}),
        "exclusion_scope": "one_source_ctcc_v2_egg_info_record_only; installed_versions_unchanged",
    }
