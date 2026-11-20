"""Declared synthetic fixtures only: select a proven exact migration prefix.

This helper never rewrites a measured SQL/source receipt or production tree.
The complete actual inventory is proved before a historical fixture is made.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import current_contract_source_scopes_v1 as scopes


def _inventory(root):
    root = Path(root).resolve(strict=True)
    paths = tuple(sorted((root / "migrations").rglob("*.py")))
    if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("synthetic_fixture_exact_inventory_required")
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    matches = [
        head
        for head, (pin, count) in scopes._PREFIX.items()
        if pin == digest and count == len(files)
    ]
    if len(matches) != 1:
        raise ValueError("synthetic_fixture_exact_inventory_required")
    return root, files, matches[0]


def historical_migration_paths(root, *, target):
    root, files, actual = _inventory(root)
    if target not in scopes._SUPPORTED[actual]:
        raise ValueError("synthetic_fixture_prefix_unsupported")
    remove = {record[0] for head, record in scopes._TAIL.items() if head > target}
    selected = {name: pin for name, pin in files.items() if name not in remove}
    pin = hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (pin, len(selected)) != scopes._PREFIX[target]:
        raise ValueError("synthetic_fixture_prefix_changed")
    return tuple(root / name for name in selected)


def trim_synthetic_migration_tree(fixture_root, *, source_root, target):
    source = Path(source_root).resolve(strict=True)
    fixture = Path(fixture_root).resolve(strict=True)
    if fixture == source or source.is_relative_to(fixture) or fixture.is_symlink():
        raise ValueError("synthetic_fixture_must_be_separate")
    historical_migration_paths(source, target=target)
    selected = set(historical_migration_paths(fixture, target=target))
    migration_root = (fixture / "migrations").resolve(strict=True)
    for path in tuple(migration_root.rglob("*.py")):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(migration_root) or path.is_symlink():
            raise ValueError("synthetic_fixture_path_changed")
        if resolved not in selected:
            path.unlink()
    historical_migration_paths(fixture, target=target)
