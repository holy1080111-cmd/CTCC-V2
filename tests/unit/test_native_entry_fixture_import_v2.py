"""Disposable native ACL setup has no heavy test-module import side effects."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests import native_private_temp_v1 as private

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_FUNCTION_AST_SHA256 = {
    "_owner_sid_string": "f0435ed48ef9231e3bd2889c25961af76704d7ffa8c054af717062e4ebc9146d",
    "_set_protected_directory_dacl": "20e31af2cf5476063a3192a1733ffed18b8f5a1b83901f8e0074672a108f0eb0",
    "_private_root_path": "815207b90f9d426c1e2ce27d80f55b2c5706d0c058bba3666a8e3793b61cfe7e",
}


@pytest.mark.parametrize("name,expected", ORIGINAL_FUNCTION_AST_SHA256.items())
def test_native_helper_preserves_original_function_ast(name, expected):
    source = (ROOT / "tests/native_private_temp_v1.py").read_text(encoding="utf-8")
    definitions = {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }
    actual = hashlib.sha256(
        ast.dump(definitions[name], include_attributes=False).encode()
    ).hexdigest()
    assert actual == expected


def test_private_temp_root_remains_native_verified_and_no_clobber(tmp_path):
    from app.trade_qualification import account_native_proof_storage as storage

    root = private._private_root_path(tmp_path)
    assert root == tmp_path / "private-custody"
    if os.name != "nt":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    with storage._root_context(root) as directory:
        assert directory.names() == []
    identity = storage._root_identity(root)
    with pytest.raises(FileExistsError):
        private._private_root_path(tmp_path)
    assert storage._root_identity(root) == identity


@pytest.mark.parametrize(
    "module,helper_only",
    [
        ("tests.native_private_temp_v1", True),
        ("tests.unit.native_entry_worker_fixture_v1", False),
    ],
)
def test_clean_child_import_avoids_native_caller_and_custody_test_modules(
    module, helper_only, record_property
):
    # No inherited CTCC/OKX/Notion configuration, secrets, credentials or service
    # startup. Importing the helper performs no ACL writes or directory creation.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LOCALAPPDATA",
            "APPDATA",
            "USERPROFILE",
            "COMSPEC",
        }
    }
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    script = """
import importlib, json, sys, time
started = time.perf_counter()
importlib.import_module(sys.argv[1])
forbidden = (
    'tests.unit.exchange.test_live_account_config_custody_v1',
    'tests.unit.test_native_private_root_inheritance_windows_v2',
    'app.demo_automation.native_caller_v2',
    'app.demo_automation.native_source_only_v1',
)
loaded = sorted(name for name in forbidden if name in sys.modules)
helper_only = sys.argv[2] == 'True'
if helper_only:
    loaded += sorted(name for name in sys.modules
                     if name == 'app' or name.startswith('app.')
                     or name == 'tests.unit' or name.startswith('tests.unit.'))
assert not loaded, loaded
print(json.dumps({'elapsed_seconds': time.perf_counter() - started,
                  'forbidden_modules': loaded, 'helper_only': helper_only}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-s", "-c", script, module, str(helper_only)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    data = json.loads(result.stdout)
    assert data["forbidden_modules"] == []
    assert data["helper_only"] is helper_only
    assert data["elapsed_seconds"] >= 0
    record_property("clean_import", json.dumps(data, sort_keys=True))
