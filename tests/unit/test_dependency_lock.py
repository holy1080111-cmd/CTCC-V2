"""Reject altered dependency/source identities before inventory acceptance."""

import hashlib
import json
import zipfile

import pytest

from scripts import verify_dependency_lock as verifier
from scripts.generate_dependency_locks import wheel_record


@pytest.fixture
def lock_root(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.platform, "system", lambda: "Linux")
    monkeypatch.setattr(verifier.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(verifier.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(verifier.sys, "version_info", (3, 12, 14))
    source = b'[project]\nname="ctcc-v2"\nversion="1.6.9"\n'
    (tmp_path / "pyproject.toml").write_bytes(source)
    directory = tmp_path / "requirements"
    directory.mkdir()
    lock = (
        "--index-url https://pypi.org/simple\n--only-binary :all:\n"
        f"pip==26.2.1 --hash=sha256:{'a' * 64}\n"
    ).encode()
    lock_path = directory / "validation-linux-py312.lock"
    lock_path.write_bytes(lock)
    manifest = {
        "schema": "ctcc.validation_dependency_lock.v1",
        "target": "linux",
        "architecture": "x86_64",
        "python": "3.12",
        "source_pyproject_sha256": hashlib.sha256(source).hexdigest(),
        "lock_sha256": hashlib.sha256(lock).hexdigest(),
        "packages": [
            {
                "name": "pip",
                "distribution_name": "pip",
                "version": "26.2.1",
                "sha256": "a" * 64,
                "filename": "pip-26.2.1-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/synthetic-wheel",
            }
        ],
    }
    manifest_path = directory / "validation-linux-py312.json"
    manifest_path.write_text(json.dumps(manifest))
    return tmp_path, lock_path, manifest_path, manifest


@pytest.mark.parametrize("damage", ["lock", "source", "missing_hash", "wheel"])
def test_lock_mutations_fail_before_installed_inventory(lock_root, damage, monkeypatch):
    root, lock_path, manifest_path, manifest = lock_root
    called = []
    monkeypatch.setattr(verifier, "verify_dependencies", lambda *_: called.append(True))
    if damage == "lock":
        lock_path.write_bytes(lock_path.read_bytes() + b"# altered\n")
    elif damage == "source":
        with (root / "pyproject.toml").open("ab") as stream:
            stream.write(b'\nrequires-python=">=3.13"\n')
    elif damage == "missing_hash":
        raw = lock_path.read_bytes().replace(f" --hash=sha256:{'a' * 64}".encode(), b"")
        lock_path.write_bytes(raw)
        manifest["lock_sha256"] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
    else:
        manifest["packages"][0]["sha256"] = "b" * 64
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verifier.verify_lock(root, "linux")
    assert called == []


def test_foreign_platform_lock_fails_before_filesystem_read(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.platform, "system", lambda: "Darwin")
    with pytest.raises(ValueError, match="interpreter/platform"):
        verifier.verify_lock(tmp_path / "does-not-exist", "linux")


def test_wheel_identity_uses_root_metadata_and_rejects_ambiguous_identity(tmp_path):
    wheel = tmp_path / "synthetic.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n"
        )
        archive.writestr(
            "example/_vendor/other-2.0.dist-info/METADATA",
            "Name: other\nVersion: 2.0\n",
        )
    assert wheel_record(wheel)["name"] == "example"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("other-2.0.dist-info/METADATA", "Name: other\nVersion: 2.0\n")
    with pytest.raises(ValueError, match="METADATA count"):
        wheel_record(wheel)
