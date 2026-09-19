"""Verify a platform wheel lock and the complete installed validation inventory.

This read-only check does not grant release or trading authority. The exact final
source still requires full validation and the strict release identity contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import tomllib
from pathlib import Path

from scripts.release_dependencies import verify_dependencies

PIN = re.compile(r"([a-z0-9][a-z0-9-]*)==([^\s]+) --hash=sha256:([0-9a-f]{64})")


def verify_lock(root: Path, target: str) -> dict:
    if target not in {"linux", "windows"}:
        raise ValueError("unsupported dependency lock target")
    if (
        platform.python_implementation() != "CPython"
        or sys.version_info[:2] != (3, 12)
        or platform.system().lower() != target
        or platform.machine().lower() not in {"amd64", "x86_64"}
    ):
        raise ValueError("dependency lock does not support this interpreter/platform")
    root = root.resolve(strict=True)
    stem = root / "requirements" / f"validation-{target}-py312"
    lock_path = stem.with_suffix(".lock")
    manifest_path = stem.with_suffix(".json")
    if lock_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("dependency lock cannot be a symlink")
    lock = lock_path.read_bytes()
    manifest = json.loads(manifest_path.read_bytes())
    source = (root / "pyproject.toml").read_bytes()
    if (
        manifest["schema"] != "ctcc.validation_dependency_lock.v1"
        or manifest["target"] != target
        or manifest["architecture"] != "x86_64"
        or manifest["python"] != "3.12"
        or manifest["source_pyproject_sha256"] != hashlib.sha256(source).hexdigest()
        or manifest["lock_sha256"] != hashlib.sha256(lock).hexdigest()
    ):
        raise ValueError("source or dependency lock identity differs")
    pins = {}
    options = []
    for line in lock.decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("--"):
            options.append(line)
            continue
        match = PIN.fullmatch(line)
        if match is None or match[1] in pins:
            raise ValueError("dependency lock has an unpinned/duplicate requirement")
        pins[match[1]] = (match[2], match[3])
    if options != ["--index-url https://pypi.org/simple", "--only-binary :all:"]:
        raise ValueError("dependency lock index/binary policy changed")
    packages = manifest["packages"]
    if len(packages) != len(pins) or not pins:
        raise ValueError("dependency lock manifest inventory differs")
    for row in packages:
        if (
            pins.get(row["name"]) != (row["version"], row["sha256"])
            or not row["url"].startswith("https://files.pythonhosted.org/")
            or not row["filename"].endswith(".whl")
        ):
            raise ValueError("dependency lock wheel identity differs")
    if len({row["name"] for row in packages}) != len(packages):
        raise ValueError("dependency lock manifest repeats a package")
    project = tomllib.loads(source.decode())["project"]
    # Distribution name case is significant to the historical strict multiset.
    expected = [(row["distribution_name"], row["version"]) for row in packages]
    expected.append((project["name"], project["version"]))
    result = verify_dependencies(
        root, {"python": sys.version, "dependencies": expected}
    )
    return {
        "target": target,
        "lock_sha256": manifest["lock_sha256"],
        "packages": len(packages),
        "installed_inventory": result,
        "accepted_full_validation_baseline": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--target", choices=("linux", "windows"), required=True)
    args = parser.parse_args()
    print(json.dumps(verify_lock(args.root, args.target), sort_keys=True))


if __name__ == "__main__":
    main()
